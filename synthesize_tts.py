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
import math
import os
import random
import re
import struct
import sys
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs import ElevenLabs, VoiceSettings

load_dotenv(os.path.expanduser("~/.env"))

# ── Configuration ──

TTS_MODEL = "eleven_multilingual_v2"
# pcm_44100 = uncompressed 16-bit PCM at 44.1kHz — best quality, no compression artifacts.
# Output is raw PCM bytes, not a WAV file — we wrap it in a WAV header below.
OUTPUT_FORMAT = "pcm_44100"

DEFAULT_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
DEFAULT_API_MAX_RETRIES = 5
DEFAULT_API_BACKOFF_BASE_SEC = 1.0
DEFAULT_API_BACKOFF_MAX_SEC = 20.0
DEFAULT_API_BACKOFF_JITTER_SEC = 0.5
DEFAULT_SLIDE_MAX_RETRIES = 2
DEFAULT_MIN_WAV_DURATION_SEC = 0.35

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


def _read_int_env(name: str, default: int, minimum: int = 0) -> int:
    """Read integer env var with bounds and default fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        return max(value, minimum)
    except ValueError:
        print(f"  Warning: invalid {name}={raw!r}, using default {default}")
        return default


def _read_float_env(name: str, default: float, minimum: float = 0.0) -> float:
    """Read float env var with bounds and default fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
        return max(value, minimum)
    except ValueError:
        print(f"  Warning: invalid {name}={raw!r}, using default {default}")
        return default


def _extract_status_code(exc: Exception) -> int | None:
    """Best-effort extraction of HTTP status code from ElevenLabs/HTTP exceptions."""
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int):
            return response_status
    return None


def _is_retryable_api_error(exc: Exception, retryable_statuses: set[int]) -> bool:
    """Classify transient API failures eligible for retry."""
    if isinstance(exc, TimeoutError):
        return True

    status = _extract_status_code(exc)
    if status in retryable_statuses:
        return True

    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    transient_markers = (
        "timeout",
        "temporar",
        "too many requests",
        "rate limit",
        "connection reset",
        "connection aborted",
        "service unavailable",
        "gateway",
    )
    return any(marker in name or marker in message for marker in transient_markers)


def _compute_backoff(attempt: int, base_sec: float, max_sec: float, jitter_sec: float) -> float:
    """Exponential backoff with additive jitter."""
    exp = base_sec * math.pow(2, max(0, attempt - 1))
    jitter = random.uniform(0.0, jitter_sec)
    return min(max_sec, exp + jitter)


def _validate_wav(path: Path, min_duration_sec: float) -> tuple[bool, str]:
    """Validate generated WAV file (non-empty, readable header, minimum duration)."""
    if not path.exists():
        return False, "file was not created"
    if path.stat().st_size <= 44:
        return False, "file is empty or only contains a WAV header"

    try:
        with wave.open(str(path), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
    except wave.Error as exc:
        return False, f"invalid WAV header/content: {exc}"
    except OSError as exc:
        return False, f"unable to read WAV file: {exc}"

    if frame_count <= 0:
        return False, "WAV has zero frames"
    if sample_rate <= 0:
        return False, "WAV reports invalid sample rate"
    if channels <= 0 or sample_width <= 0:
        return False, "WAV reports invalid channel/sample-width metadata"

    duration_sec = frame_count / float(sample_rate)
    if duration_sec < min_duration_sec:
        return False, f"duration {duration_sec:.3f}s below minimum {min_duration_sec:.3f}s"

    return True, f"ok ({duration_sec:.2f}s)"


def synthesize_with_retries(
    *,
    slide_number: int,
    text: str,
    voice_id: str,
    output_path: Path,
    client: ElevenLabs,
    voice_settings: VoiceSettings,
    normalization: dict[str, str],
    replacements: dict[str, str] | None,
    retryable_statuses: set[int],
    api_max_retries: int,
    api_backoff_base_sec: float,
    api_backoff_max_sec: float,
    api_backoff_jitter_sec: float,
    slide_max_retries: int,
    min_wav_duration_sec: float,
) -> tuple[bool, str]:
    """Synthesize one slide with API retries + output validation retries."""
    validation_attempts = slide_max_retries + 1

    for validation_attempt in range(1, validation_attempts + 1):
        if output_path.exists():
            output_path.unlink()

        for api_attempt in range(1, api_max_retries + 1):
            try:
                synthesize_slide(
                    text,
                    voice_id,
                    output_path,
                    client,
                    voice_settings=voice_settings,
                    normalization=normalization,
                    replacements=replacements,
                )
                break
            except Exception as exc:
                retryable = _is_retryable_api_error(exc, retryable_statuses)
                status = _extract_status_code(exc)
                if retryable and api_attempt < api_max_retries:
                    delay = _compute_backoff(
                        api_attempt,
                        api_backoff_base_sec,
                        api_backoff_max_sec,
                        api_backoff_jitter_sec,
                    )
                    status_info = f"status={status}" if status is not None else "status=unknown"
                    print(
                        f"    API retry {api_attempt}/{api_max_retries - 1} for slide {slide_number} "
                        f"({status_info}, {exc.__class__.__name__}: {exc}); sleeping {delay:.2f}s"
                    )
                    time.sleep(delay)
                    continue

                status_info = f"status={status}" if status is not None else "status=unknown"
                action = (
                    "retry exhausted" if retryable else "non-retryable API error"
                )
                return False, (
                    f"{action} ({status_info}, {exc.__class__.__name__}: {exc}). "
                    "Check ELEVENLABS_API_KEY, voice_id, and service status."
                )

        valid, validation_msg = _validate_wav(output_path, min_wav_duration_sec)
        if valid:
            return True, validation_msg

        if validation_attempt < validation_attempts:
            print(
                f"    Validation failed for slide {slide_number} "
                f"(attempt {validation_attempt}/{validation_attempts}): {validation_msg}. Re-synthesizing..."
            )
            continue

        return False, (
            f"audio validation failed after {validation_attempts} attempts: {validation_msg}. "
            "Review source notes (too short/empty), lower minimum duration, or retry later."
        )

    return False, "unexpected retry loop exit"


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

    retryable_statuses = DEFAULT_RETRYABLE_STATUSES
    api_max_retries = _read_int_env("TTS_API_MAX_RETRIES", DEFAULT_API_MAX_RETRIES, minimum=1)
    api_backoff_base_sec = _read_float_env("TTS_API_BACKOFF_BASE_SEC", DEFAULT_API_BACKOFF_BASE_SEC, minimum=0.0)
    api_backoff_max_sec = _read_float_env("TTS_API_BACKOFF_MAX_SEC", DEFAULT_API_BACKOFF_MAX_SEC, minimum=0.0)
    api_backoff_jitter_sec = _read_float_env("TTS_API_BACKOFF_JITTER_SEC", DEFAULT_API_BACKOFF_JITTER_SEC, minimum=0.0)
    slide_max_retries = _read_int_env("TTS_SLIDE_MAX_RETRIES", DEFAULT_SLIDE_MAX_RETRIES, minimum=0)
    min_wav_duration_sec = _read_float_env("TTS_MIN_WAV_DURATION_SEC", DEFAULT_MIN_WAV_DURATION_SEC, minimum=0.0)

    print(f"  API retries: {api_max_retries} (statuses={sorted(retryable_statuses)})")
    print(
        f"  Backoff: base={api_backoff_base_sec:.2f}s, max={api_backoff_max_sec:.2f}s, "
        f"jitter={api_backoff_jitter_sec:.2f}s"
    )
    print(f"  Slide validation retries: {slide_max_retries}")
    print(f"  Minimum WAV duration: {min_wav_duration_sec:.2f}s")

    with open(notes_path, "r", encoding="utf-8") as f:
        notes = json.load(f)

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("Error: ELEVENLABS_API_KEY not set in ~/.env")
        sys.exit(1)

    client = ElevenLabs(api_key=api_key)

    failures: list[tuple[int, str]] = []

    for note in notes:
        sn = note["slide"]
        text = note["text"].strip()
        # Output as WAV since we use pcm_44100 format
        audio_file = audio_dir / f"slide_{sn:02d}.wav"

        if not text:
            print(f"  Slide {sn}: (silent - no notes)")
            continue

        if audio_file.exists():
            print(f"  Slide {sn}: [skip] already exists -> {audio_file.name}")
            continue

        print(f"  Slide {sn}: [start] synthesizing ({len(text)} chars)...")
        ok, message = synthesize_with_retries(
            slide_number=sn,
            text=text,
            voice_id=voice_id,
            output_path=audio_file,
            client=client,
            voice_settings=voice_settings,
            normalization=normalization,
            replacements=replacements,
            retryable_statuses=retryable_statuses,
            api_max_retries=api_max_retries,
            api_backoff_base_sec=api_backoff_base_sec,
            api_backoff_max_sec=api_backoff_max_sec,
            api_backoff_jitter_sec=api_backoff_jitter_sec,
            slide_max_retries=slide_max_retries,
            min_wav_duration_sec=min_wav_duration_sec,
        )
        if ok:
            print(f"  Slide {sn}: [ok] {audio_file.name} ({message})")
        else:
            failures.append((sn, message))
            print(f"  Slide {sn}: [failed] {message}")

    if failures:
        print("\nTTS synthesis finished with failures:")
        for sn, reason in failures:
            print(f"  - Slide {sn}: {reason}")
        print(
            "Action: fix failed slides (notes/voice/config), then rerun synthesize_tts.py; "
            "existing successful WAV files will be reused."
        )
        sys.exit(1)

    print(f"\nTTS synthesis complete. Audio files in: {audio_dir}")


if __name__ == "__main__":
    main()
