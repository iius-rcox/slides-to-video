"""Assemble narrated slide video — split-track approach.

Usage:
    python assemble_video.py <notes_json> <slides_dir> <audio_dir> <output_mp4>

Split-track assembly: audio and video are built as separate tracks and muxed
at the end. Audio is encoded to AAC exactly ONCE, preventing the severe quality
degradation caused by repeated lossy re-encoding in pairwise merges.

Steps:
  1. Pad per-slide audio as lossless WAV (pcm_s16le intermediates)
  2. Concatenate all WAVs into one continuous audio track
  3. Build video-only slideshow from PNGs with xfade crossfade transitions
  4. Mux video + audio with single high-quality AAC encode
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

FFMPEG_TIMEOUT = 600
WIDTH = 1920
HEIGHT = 1080
FPS = 30
CRF = 18
PRESET = "medium"

PRE_PAD = 1.0
POST_PAD = 1.0
SILENT_SLIDE_DUR = 2.0
TRANSITION_DURATION = 0.5

DEFAULT_AUDIO_POSTPROCESSING = {
    "deesser": {
        "enabled": False,
        "intensity": 0.0,
        "mode": "wide",
        "frequency": 6000,
    },
    "highpass": {
        "enabled": True,
        "frequency": 80,
    },
    "presence_eq": {
        "enabled": True,
        "frequency": 3000,
        "width_octave": 1.5,
        "gain_db": 1.5,
    },
    "loudnorm": {
        "enabled": True,
        "two_pass": False,
        "I": -16,
        "LRA": 11,
        "TP": -1.5,
    },
    "limiter": {
        "enabled": True,
        "intensity": 1.0,
        "base_limit": 0.891,
        "attack": 5,
        "release": 50,
    },
}


def _is_narrated_slide(note: dict) -> bool:
    """Return True when this slide is expected to have narration audio."""
    return bool(str(note.get("text", "")).strip())


def preflight_audio_files(notes: list[dict], audio_dir: Path) -> None:
    """Validate narrated slides have expected WAV files before ffmpeg work starts."""
    missing_wav: list[tuple[int, Path]] = []
    legacy_mp3_present: list[tuple[int, Path]] = []

    for note in notes:
        if not _is_narrated_slide(note):
            continue

        sn = note["slide"]
        wav_file = audio_dir / f"slide_{sn:02d}.wav"
        mp3_file = audio_dir / f"slide_{sn:02d}.mp3"

        if wav_file.exists():
            continue

        missing_wav.append((sn, wav_file))
        if mp3_file.exists():
            legacy_mp3_present.append((sn, mp3_file))

    if not missing_wav:
        return

    print("\nERROR: Missing narration WAV files for narrated slides:")
    for sn, wav_file in missing_wav:
        print(f"  - Slide {sn:02d}: expected {wav_file}")

    print("\nAction: Generate WAV narration files before assembly.")
    print("  python synthesize_tts.py <notes_json> <audio_dir> --lang <lang>")

    if legacy_mp3_present:
        print("\nLegacy MP3 detected (supported for migration only, not used by assembler):")
        for sn, mp3_file in legacy_mp3_present:
            print(f"  - Slide {sn:02d}: found legacy file {mp3_file}")

        print("\nConvert legacy MP3 to expected WAV format with:")
        print(
            "  ffmpeg -y -i audio/slide_XX.mp3 -ar 44100 -ac 2 -c:a pcm_s16le "
            "audio/slide_XX.wav"
        )

    raise FileNotFoundError("Preflight failed: missing narration WAV files.")


def get_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_audio_postprocess_config(lang: str, voice_id: str | None) -> dict:
    """Load per-language/per-voice audio post-processing from lang_config.json."""
    config_path = Path(__file__).parent / "lang_config.json"
    config = DEFAULT_AUDIO_POSTPROCESSING

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            all_config = json.load(f)
    except FileNotFoundError:
        print("  Warning: lang_config.json not found, using default audio post-processing")
        return config
    except json.JSONDecodeError as e:
        print(f"  Warning: Error reading lang_config.json ({e}), using default audio post-processing")
        return config

    lang_cfg = all_config.get(lang)
    if not lang_cfg:
        print(f"  Warning: Language '{lang}' not found in lang_config.json, using default audio post-processing")
        return config

    config = _deep_merge(config, lang_cfg.get("audio_postprocessing", {}))

    if voice_id:
        voice_cfg = lang_cfg.get("voice_overrides", {}).get(voice_id, {})
        config = _deep_merge(config, voice_cfg.get("audio_postprocessing", {}))

    return config


def build_audio_filter_chain(config: dict, include_limiter: bool = True, loudnorm_override: str | None = None) -> str:
    filters: list[str] = []

    deesser = config.get("deesser", {})
    if deesser.get("enabled"):
        intensity = max(0.0, float(deesser.get("intensity", 0.0)))
        if intensity > 0:
            mode = deesser.get("mode", "wide")
            freq = int(deesser.get("frequency", 6000))
            filters.append(f"deesser=i={intensity:.3f}:m={mode}:f={freq}")

    highpass = config.get("highpass", {})
    if highpass.get("enabled"):
        freq = float(highpass.get("frequency", 80))
        if freq > 0:
            filters.append(f"highpass=f={freq:g}")

    presence = config.get("presence_eq", {})
    if presence.get("enabled"):
        gain = float(presence.get("gain_db", 0.0))
        if abs(gain) > 1e-3:
            freq = float(presence.get("frequency", 3000))
            width = float(presence.get("width_octave", 1.5))
            filters.append(
                f"equalizer=f={freq:g}:width_type=o:width={width:g}:g={gain:g}"
            )

    if loudnorm_override:
        filters.append(loudnorm_override)
    else:
        loudnorm_cfg = config.get("loudnorm", {})
        if loudnorm_cfg.get("enabled"):
            filters.append(
                "loudnorm="
                f"I={float(loudnorm_cfg.get('I', -16)):g}:"
                f"LRA={float(loudnorm_cfg.get('LRA', 11)):g}:"
                f"TP={float(loudnorm_cfg.get('TP', -1.5)):g}"
            )

    limiter_cfg = config.get("limiter", {})
    if include_limiter and limiter_cfg.get("enabled"):
        base_limit = float(limiter_cfg.get("base_limit", 0.891))
        intensity = max(0.0, float(limiter_cfg.get("intensity", 1.0)))
        attack = float(limiter_cfg.get("attack", 5))
        release = float(limiter_cfg.get("release", 50))
        limit = min(1.0, base_limit + (1.0 - base_limit) * (1.0 - intensity))
        filters.append(f"alimiter=limit={limit:.3f}:attack={attack:g}:release={release:g}")

    return ",".join(filters) if filters else "anull"


def _parse_loudnorm_stats(stderr: str) -> dict | None:
    matches = re.findall(r"\{\s*\"input_i\".*?\}", stderr, flags=re.DOTALL)
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except json.JSONDecodeError:
        return None


def run_loudnorm_analysis(audio_path: Path, config: dict) -> dict | None:
    loudnorm_cfg = config.get("loudnorm", {})
    analysis_filter = (
        "loudnorm="
        f"I={float(loudnorm_cfg.get('I', -16)):g}:"
        f"LRA={float(loudnorm_cfg.get('LRA', 11)):g}:"
        f"TP={float(loudnorm_cfg.get('TP', -1.5)):g}:"
        "print_format=json"
    )
    chain = build_audio_filter_chain(config, include_limiter=False, loudnorm_override=analysis_filter)

    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(audio_path), "-af", chain, "-f", "null", "-"],
        capture_output=True, text=True, timeout=FFMPEG_TIMEOUT,
    )
    if result.returncode != 0:
        return None
    return _parse_loudnorm_stats(result.stderr)


def build_two_pass_loudnorm_filter(config: dict, stats: dict) -> str:
    loudnorm_cfg = config.get("loudnorm", {})
    loudnorm = (
        "loudnorm="
        f"I={float(loudnorm_cfg.get('I', -16)):g}:"
        f"LRA={float(loudnorm_cfg.get('LRA', 11)):g}:"
        f"TP={float(loudnorm_cfg.get('TP', -1.5)):g}:"
        f"measured_I={float(stats['input_i']):g}:"
        f"measured_LRA={float(stats['input_lra']):g}:"
        f"measured_TP={float(stats['input_tp']):g}:"
        f"measured_thresh={float(stats['input_thresh']):g}:"
        f"offset={float(stats['target_offset']):g}:"
        "linear=true:print_format=summary"
    )
    return build_audio_filter_chain(config, include_limiter=True, loudnorm_override=loudnorm)


# ── Step 1: Lossless WAV intermediates ──

def pad_audio_to_wav(audio_path: Path, padded_path: Path, pre: float = PRE_PAD, post: float = POST_PAD) -> None:
    """Pad audio with silence, output as lossless WAV."""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-i", str(audio_path),
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-filter_complex",
         f"[0:a]atrim=0:{pre},aformat=sample_rates=44100:channel_layouts=stereo[pre];"
         f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo[main];"
         f"[2:a]atrim=0:{post},aformat=sample_rates=44100:channel_layouts=stereo[post];"
         f"[pre][main][post]concat=n=3:v=0:a=1[out]",
         "-map", "[out]", "-c:a", "pcm_s16le",
         str(padded_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


def create_silence_wav(duration: float, output_path: Path) -> None:
    """Create a silent WAV file."""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", str(duration),
         "-c:a", "pcm_s16le",
         str(output_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


# ── Step 2: Concatenate WAVs ──

def concat_audio(wav_files: list[Path], output_path: Path) -> None:
    """Concatenate WAV files into one continuous track (no re-encode)."""
    concat_list = output_path.parent / "audio_concat.txt"
    with open(concat_list, "w") as f:
        for wav in wav_files:
            f.write(f"file '{str(wav).replace(chr(92), '/')}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_list),
         "-c:a", "pcm_s16le",
         str(output_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


# ── Step 3: Video-only slideshow with xfade ──

def build_video_slideshow(
    notes: list[dict],
    slides_dir: Path,
    slide_durations: list[float],
    output_path: Path,
    transition_dur: float,
) -> None:
    """Build video-only slideshow with xfade crossfade transitions."""
    n = len(notes)
    inputs: list[str] = []
    filter_parts: list[str] = []

    for i, note in enumerate(notes):
        sn = note["slide"]
        slide_img = slides_dir / f"slide_{sn:02d}.png"
        dur = slide_durations[i]
        inputs.extend(["-loop", "1", "-t", f"{dur:.3f}", "-i", str(slide_img)])

    if n == 1:
        filter_parts.append(f"[0:v]scale={WIDTH}:{HEIGHT},format=yuv420p[vout]")
    else:
        for i in range(n):
            filter_parts.append(f"[{i}:v]scale={WIDTH}:{HEIGHT},format=yuv420p[s{i}]")

        cumulative = 0.0
        prev = "[s0]"
        for i in range(1, n):
            offset = cumulative + slide_durations[i - 1] - transition_dur
            if offset < 0.1:
                offset = 0.1
            out_label = "vout" if i == n - 1 else f"v{i}"
            filter_parts.append(
                f"{prev}[s{i}]xfade=transition=fade"
                f":duration={transition_dur}:offset={offset:.3f}[{out_label}]"
            )
            cumulative = offset
            prev = f"[{out_label}]"

    filter_graph = ";\n".join(filter_parts)
    cmd = (
        ["ffmpeg", "-y"] + inputs
        + ["-filter_complex", filter_graph,
           "-map", "[vout]",
           "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
           "-r", str(FPS), "-an",
           str(output_path)]
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
    if result.returncode != 0:
        print("  xfade failed, falling back to concat (hard cuts)...")
        _fallback_concat_video(notes, slides_dir, slide_durations, output_path)


def _fallback_concat_video(notes, slides_dir, slide_durations, output_path):
    work_dir = output_path.parent
    seg_list = work_dir / "video_concat.txt"
    with open(seg_list, "w") as f:
        for i, note in enumerate(notes):
            sn = note["slide"]
            slide_img = slides_dir / f"slide_{sn:02d}.png"
            dur = slide_durations[i]
            seg = work_dir / f"seg_{sn:02d}.mp4"
            if not seg.exists():
                subprocess.run(
                    ["ffmpeg", "-y",
                     "-loop", "1", "-i", str(slide_img),
                     "-t", f"{dur:.3f}",
                     "-vf", f"scale={WIDTH}:{HEIGHT},format=yuv420p",
                     "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
                     "-r", str(FPS), "-an", str(seg)],
                    capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)
            f.write(f"file '{str(seg).replace(chr(92), '/')}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(seg_list),
         "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
         "-an", str(output_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


# ── Step 4: Final mux ──

def mux_video_audio(video_path: Path, audio_path: Path, output_path: Path, config: dict) -> None:
    """Mux video + audio with broadcast-quality audio processing.

    Audio filter chain (applied during the single AAC encode):
      1. loudnorm  — EBU R128 loudness normalization (broadcast standard)
      2. highpass   — Remove low-frequency rumble below 80Hz
      3. equalizer  — Boost speech presence around 3kHz
      4. alimiter   — Peak limiter at -1dB to prevent clipping

    Video is copied (no re-encode). Audio encoded to 256k AAC.
    -movflags +faststart moves the moov atom for web streaming.
    """
    loudnorm_cfg = config.get("loudnorm", {})
    two_pass = loudnorm_cfg.get("enabled") and loudnorm_cfg.get("two_pass")
    if two_pass:
        stats = run_loudnorm_analysis(audio_path, config)
        if stats:
            print("  Audio post-processing: two-pass loudnorm enabled")
            audio_filters = build_two_pass_loudnorm_filter(config, stats)
        else:
            print("  Warning: loudnorm analysis failed, falling back to single-pass")
            audio_filters = build_audio_filter_chain(config)
    else:
        audio_filters = build_audio_filter_chain(config)

    subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(video_path),
         "-i", str(audio_path),
         "-c:v", "copy",
         "-af", audio_filters,
         "-c:a", "aac", "-b:a", "256k",
         "-ar", "44100",
         "-movflags", "+faststart",
         "-shortest",
         str(output_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


# ── Main ──

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Assemble narrated slide video")
    parser.add_argument("notes_json", help="Path to notes JSON")
    parser.add_argument("slides_dir", help="Directory with slide PNGs")
    parser.add_argument("audio_dir", help="Directory with TTS WAV files")
    parser.add_argument("output_mp4", help="Output video path")
    parser.add_argument("--lang", default="en", help="Language code for post-processing config")
    parser.add_argument("--voice-id", default=None, help="Voice ID for per-voice post-processing overrides")
    args = parser.parse_args()

    notes_path = Path(args.notes_json)
    slides_dir = Path(args.slides_dir)
    audio_dir = Path(args.audio_dir)
    output_path = Path(args.output_mp4)

    v2_dir = output_path.parent / f"{output_path.stem}_work" / "v2"
    v2_dir.mkdir(parents=True, exist_ok=True)

    with open(notes_path, "r", encoding="utf-8") as f:
        notes = json.load(f)

    preflight_audio_files(notes, audio_dir)

    # Step 1: Pad audio as lossless WAV
    print("Step 1: Preparing per-slide audio (WAV lossless)...")
    slide_durations: list[float] = []
    wav_files: list[Path] = []

    for note in notes:
        sn = note["slide"]
        # TTS now outputs WAV directly (pcm_44100 format)
        audio_file = audio_dir / f"slide_{sn:02d}.wav"
        padded_wav = v2_dir / f"slide_{sn:02d}_padded.wav"

        if audio_file.exists():
            if not padded_wav.exists():
                print(f"  Slide {sn}: padding audio...")
                pad_audio_to_wav(audio_file, padded_wav)
            dur = get_duration(padded_wav)
            slide_durations.append(dur)
            wav_files.append(padded_wav)
            print(f"  Slide {sn}: {dur:.1f}s (narrated)")
        else:
            silence_wav = v2_dir / f"slide_{sn:02d}_silence.wav"
            if not silence_wav.exists():
                create_silence_wav(SILENT_SLIDE_DUR, silence_wav)
            slide_durations.append(SILENT_SLIDE_DUR)
            wav_files.append(silence_wav)
            print(f"  Slide {sn}: {SILENT_SLIDE_DUR}s (silent)")

    # Step 2: Concatenate audio
    print("\nStep 2: Concatenating audio into single track...")
    full_audio = v2_dir / "full_audio.wav"
    if not full_audio.exists():
        concat_audio(wav_files, full_audio)
    print(f"  Full audio: {get_duration(full_audio):.1f}s")

    # Step 3: Build video-only slideshow
    print("\nStep 3: Building video slideshow with crossfades...")
    video_only = v2_dir / "video_only.mp4"
    if not video_only.exists():
        build_video_slideshow(notes, slides_dir, slide_durations, video_only, TRANSITION_DURATION)
    print(f"  Video track: {get_duration(video_only):.1f}s")

    # Step 4: Mux
    print("\nStep 4: Muxing video + audio (single AAC encode)...")
    post_config = load_audio_postprocess_config(args.lang, args.voice_id)
    mux_video_audio(video_only, full_audio, output_path, post_config)

    final_dur = get_duration(output_path)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nDone! Output: {output_path.name}")
    print(f"  Duration: {final_dur:.1f}s ({final_dur / 60:.1f} min)")
    print(f"  Size: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
