"""Synthesize TTS audio for each slide using ElevenLabs.

Usage:
    python synthesize_tts.py <notes_json> <audio_dir> [--voice-id ID] [--lang en]

Voice quality improvements over baseline:
  1. VoiceSettings with use_speaker_boost=True — boosts clarity and speaker fidelity
  2. pcm_44100 output — uncompressed WAV eliminates MP3 compression artifacts at source
  3. TTS_REPLACEMENTS — regex pronunciation fixes for brand names and abbreviations
  4. Per-language config from lang_config.json — voice, settings, replacements, normalization
  5. Text normalization pipeline — expands units, abbreviations, symbols for natural TTS
"""

from __future__ import annotations

import json
import os
import re
import struct
import sys
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs import ElevenLabs, VoiceSettings

load_dotenv(os.path.expanduser("~/.env"))

# ── Configuration ──

TTS_MODEL = "eleven_multilingual_v2"
# pcm_44100 = uncompressed 16-bit PCM at 44.1kHz — best quality, no compression artifacts.
# Output is raw PCM bytes, not a WAV file — we wrap it in a WAV header below.
OUTPUT_FORMAT = "pcm_44100"

# Hardcoded fallbacks — used when lang_config.json is missing or incomplete
DEFAULT_VOICES = {
    "en": "NHjG3gYsiwhncLX4Nfhc",
    "es": "sDh3eviBhiuHKi0MjTNq",
}

# Fallback voice settings (used when lang_config.json is missing)
DEFAULT_VOICE_SETTINGS = VoiceSettings(
    stability=0.5,
    similarity_boost=0.8,
    style=0.0,
    use_speaker_boost=True,
    speed=1.0,
)

# Fallback pronunciation fixes (used when lang_config.json is missing)
DEFAULT_TTS_REPLACEMENTS = {
    r'\bI&I\b': 'Eye and Eye',
    r'\bI & I\b': 'Eye and Eye',
    r'\bI&I Soft Craft Solutions\b': 'Eye and Eye Soft Craft Solutions',
    r'\bSmartBarrel\b': 'Smart Barrel',
    r'\bSmartbarrel\b': 'Smart Barrel',
}


# ── Language Config Loading ──

def load_lang_config(lang: str) -> dict:
    """Load per-language config from lang_config.json.

    Returns a dict with keys: voice_id, voice_settings, tts_replacements, normalization.
    Falls back to hardcoded defaults if the file is missing or the language key is absent.
    """
    config_path = Path(__file__).parent / "lang_config.json"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            all_config = json.load(f)
        if lang in all_config:
            return all_config[lang]
        print(f"  Warning: Language '{lang}' not found in lang_config.json, using defaults")
    except FileNotFoundError:
        print("  Warning: lang_config.json not found, using hardcoded defaults")
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  Warning: Error reading lang_config.json ({e}), using defaults")

    # Return fallback config matching the hardcoded defaults
    return {
        "voice_id": DEFAULT_VOICES.get(lang, DEFAULT_VOICES["en"]),
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.8,
            "style": 0.0,
            "use_speaker_boost": True,
            "speed": 1.0,
        },
        "tts_replacements": {k: v for k, v in DEFAULT_TTS_REPLACEMENTS.items()},
        "normalization": {},
    }


def build_voice_settings(config: dict) -> VoiceSettings:
    """Construct VoiceSettings from a lang_config dict."""
    vs = config.get("voice_settings", {})
    return VoiceSettings(
        stability=vs.get("stability", 0.5),
        similarity_boost=vs.get("similarity_boost", 0.8),
        style=vs.get("style", 0.0),
        use_speaker_boost=vs.get("use_speaker_boost", True),
        speed=vs.get("speed", 1.0),
    )


# ── Text Preprocessing ──

def normalize_for_tts(text: str, normalization: dict[str, str]) -> str:
    """Apply language-specific normalization rules (units, abbreviations, symbols).

    Runs before pronunciation replacements. Expands abbreviated forms into
    their spoken equivalents so the TTS engine pronounces them naturally.
    See tts-normalization.md for rule documentation.
    """
    for pattern, replacement in normalization.items():
        text = re.sub(pattern, replacement, text)
    return text


def preprocess_tts_text(text: str, replacements: dict[str, str] | None = None) -> str:
    """Apply pronunciation fixes before sending text to TTS.

    Args:
        text: Raw text to preprocess.
        replacements: Regex pattern -> spoken replacement dict.
                      Falls back to DEFAULT_TTS_REPLACEMENTS if None.
    """
    if replacements is None:
        replacements = DEFAULT_TTS_REPLACEMENTS
    for pattern, replacement in replacements.items():
        text = re.sub(pattern, replacement, text)
    return text


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 44100, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM bytes in a WAV header.

    ElevenLabs pcm_44100 returns raw 16-bit signed little-endian PCM mono.
    We need a proper WAV file for ffmpeg/ffprobe to read durations.
    """
    data_size = len(pcm_bytes)
    byte_rate = sample_rate * channels * (bits // 8)
    block_align = channels * (bits // 8)

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,       # ChunkSize
        b'WAVE',
        b'fmt ',
        16,                   # Subchunk1Size (PCM)
        1,                    # AudioFormat (PCM = 1)
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,                 # BitsPerSample
        b'data',
        data_size,
    )
    return header + pcm_bytes


def synthesize_slide(
    text: str,
    voice_id: str,
    output_path: Path,
    client: ElevenLabs,
    voice_settings: VoiceSettings | None = None,
    normalization: dict[str, str] | None = None,
    replacements: dict[str, str] | None = None,
) -> None:
    """Generate TTS audio for one slide. Saves as WAV.

    Args:
        text: Raw slide narration text.
        voice_id: ElevenLabs voice ID.
        output_path: Path to write the WAV file.
        client: ElevenLabs client instance.
        voice_settings: Per-language VoiceSettings (falls back to DEFAULT_VOICE_SETTINGS).
        normalization: Language-specific normalization rules (units, abbreviations).
        replacements: Pronunciation fix rules (brand names, acronyms).
    """
    if voice_settings is None:
        voice_settings = DEFAULT_VOICE_SETTINGS

    # Normalization pipeline: lang-specific rules -> pronunciation replacements
    processed = normalize_for_tts(text, normalization or {})
    processed = preprocess_tts_text(processed, replacements)

    audio_gen = client.text_to_speech.convert(
        voice_id=voice_id,
        text=processed,
        model_id=TTS_MODEL,
        output_format=OUTPUT_FORMAT,
        voice_settings=voice_settings,
    )

    # Collect raw PCM bytes
    pcm_bytes = b"".join(chunk for chunk in audio_gen if chunk)

    # Wrap in WAV header and save
    wav_bytes = pcm_to_wav(pcm_bytes)
    with open(output_path, "wb") as f:
        f.write(wav_bytes)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Synthesize TTS audio for slides")
    parser.add_argument("notes_json", help="Path to notes JSON file")
    parser.add_argument("audio_dir", help="Output directory for audio files")
    parser.add_argument("--voice-id", default=None, help="ElevenLabs voice ID")
    parser.add_argument("--lang", default="en", help="Language code for default voice")
    args = parser.parse_args()

    notes_path = Path(args.notes_json)
    audio_dir = Path(args.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Load per-language config from lang_config.json
    lang_config = load_lang_config(args.lang)
    voice_id = args.voice_id or lang_config.get("voice_id", DEFAULT_VOICES.get(args.lang, DEFAULT_VOICES["en"]))
    voice_settings = build_voice_settings(lang_config)
    normalization = lang_config.get("normalization", {})
    replacements = lang_config.get("tts_replacements", None)

    print(f"  Language: {args.lang}")
    print(f"  Voice ID: {voice_id}")
    print(f"  Voice settings: stability={voice_settings.stability}, "
          f"similarity_boost={voice_settings.similarity_boost}, "
          f"speed={voice_settings.speed}")
    print(f"  Normalization rules: {len(normalization)}")
    print(f"  TTS replacements: {len(replacements) if replacements else 'default'}")

    with open(notes_path, "r", encoding="utf-8") as f:
        notes = json.load(f)

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Error: ELEVENLABS_API_KEY not set in ~/.env")
        sys.exit(1)

    client = ElevenLabs(api_key=api_key)

    for note in notes:
        sn = note["slide"]
        text = note["text"].strip()
        # Output as WAV since we use pcm_44100 format
        audio_file = audio_dir / f"slide_{sn:02d}.wav"

        if not text:
            print(f"  Slide {sn}: (silent - no notes)")
            continue

        if audio_file.exists():
            print(f"  Slide {sn}: already exists, skipping")
            continue

        print(f"  Slide {sn}: synthesizing ({len(text)} chars)...")
        synthesize_slide(
            text, voice_id, audio_file, client,
            voice_settings=voice_settings,
            normalization=normalization,
            replacements=replacements,
        )
        print(f"  Slide {sn}: done -> {audio_file.name}")

    print(f"\nTTS synthesis complete. Audio files in: {audio_dir}")


if __name__ == "__main__":
    main()
