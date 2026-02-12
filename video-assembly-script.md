# Video Assembly Script Template

Assembles the final narrated video from slide images + TTS audio files with crossfade transitions.

**CRITICAL: Split-track assembly.** Audio and video are built as separate tracks and muxed at the end. Audio is encoded to AAC exactly once. This avoids the severe quality degradation caused by repeated lossy re-encoding.

## Why Split-Track?

The old approach (pairwise xfade merges with audio+video together) re-encoded audio as AAC at every merge step. With N slides, the first slide's audio was re-encoded N-1 times. AAC is lossy — each generation adds compression artifacts, producing scratchy, painful-to-listen-to audio after ~10+ re-encodes. The split-track approach keeps audio lossless (WAV) until the final single AAC encode.

## Core Concept

Each slide's duration is driven by its TTS audio length:

```
slide_duration = PRE_PAD + audio_duration + POST_PAD
```

Slides with no speaker notes (no audio) get a fixed silent duration.

## Assembly Flow

```
Step 1: Pad per-slide audio as lossless WAV (pcm_s16le)
        - Narrated slides: silence + WAV audio + silence → padded WAV
        - Silent slides: generate WAV of silence

Step 2: Concatenate all WAVs into one continuous audio track
        - ffmpeg concat demuxer, -c:a pcm_s16le (no re-encode)

Step 3: Build video-only slideshow with xfade crossfade transitions
        - Each slide PNG looped for its duration
        - Video xfade between slides, NO audio (-an flag)
        - Falls back to concat demuxer if xfade fails

Step 4: Mux video + audio with broadcast-quality audio processing
        - -c:v copy (no video re-encode)
        - Audio filter chain: loudnorm → highpass → EQ → limiter
        - -c:a aac -b:a 256k (single high-quality AAC encode)
        - -ar 44100 (ensure consistent sample rate)
        - -movflags +faststart (web streaming optimization)
        - -shortest to trim any duration mismatch
```

## Script Pattern

```python
"""Assemble narrated slide video — split-track approach."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

FFMPEG_TIMEOUT = 600
WIDTH = 1920
HEIGHT = 1080
FPS = 30
CRF = 18
PRESET = "medium"

PRE_PAD = 1.0            # Silence before audio on each slide
POST_PAD = 1.0           # Silence after audio on each slide
SILENT_SLIDE_DUR = 2.0   # Duration for slides with no audio
TRANSITION_DURATION = 0.5 # Crossfade between slides


def get_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, timeout=30)
    return float(json.loads(r.stdout)["format"]["duration"])


# ── Step 1: Lossless WAV intermediates ──

def pad_audio_to_wav(audio_path, padded_path, pre=PRE_PAD, post=POST_PAD):
    """Pad audio with silence, output as lossless WAV (pcm_s16le).

    IMPORTANT: Use WAV (pcm_s16le) for intermediates, NOT AAC/MP3.
    This prevents lossy compression at the intermediate stage.
    """
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


def create_silence_wav(duration, output_path):
    """Create a silent WAV file of given duration."""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-t", str(duration),
         "-c:a", "pcm_s16le",
         str(output_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


# ── Step 2: Concatenate WAVs ──

def concat_audio(wav_files, output_path):
    """Concatenate WAV files into one continuous audio track (no re-encode)."""
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

def build_video_slideshow(notes, slides_dir, slide_durations, output_path, transition_dur):
    """Build video-only slideshow from PNGs with xfade transitions.

    Audio is handled separately — this produces a video with NO audio track (-an).
    Falls back to concat demuxer (hard cuts) if xfade fails.
    """
    n = len(notes)
    inputs = []
    filter_parts = []

    for i, note in enumerate(notes):
        sn = note["slide"]
        slide_img = slides_dir / f"slide_{sn:02d}.png"
        dur = slide_durations[i]
        inputs.extend(["-loop", "1", "-t", f"{dur:.3f}", "-i", str(slide_img)])

    if n == 1:
        filter_parts.append(f"[0:v]scale={WIDTH}:{HEIGHT},format=yuv420p[vout]")
    else:
        # Scale all inputs
        for i in range(n):
            filter_parts.append(
                f"[{i}:v]scale={WIDTH}:{HEIGHT},format=yuv420p[s{i}]")

        # Chain xfade transitions
        cumulative = 0.0
        prev = "[s0]"
        for i in range(1, n):
            offset = cumulative + slide_durations[i-1] - transition_dur
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
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", filter_graph,
           "-map", "[vout]",
           "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
           "-r", str(FPS),
           "-an",  # NO audio — critical for split-track approach
           str(output_path)]
    )

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)
    if result.returncode != 0:
        print("  xfade failed, falling back to concat (hard cuts)...")
        _fallback_concat_video(notes, slides_dir, slide_durations, output_path)


def _fallback_concat_video(notes, slides_dir, slide_durations, output_path):
    """Fallback: concat video segments without transitions."""
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
                     "-r", str(FPS), "-an",
                     str(seg)],
                    capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)
            f.write(f"file '{str(seg).replace(chr(92), '/')}'\n")

    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(seg_list),
         "-c:v", "libx264", "-crf", str(CRF), "-preset", PRESET,
         "-an", str(output_path)],
        capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


# ── Step 4: Final mux ──

def mux_video_audio(video_path, audio_path, output_path):
    """Mux video + audio with broadcast-quality audio processing.

    Audio filter chain (applied during the single AAC encode):
      1. loudnorm  — EBU R128 loudness normalization (broadcast standard, -16 LUFS)
      2. highpass   — Remove low-frequency rumble below 80Hz
      3. equalizer  — Boost speech presence around 3kHz (+1.5dB)
      4. alimiter   — Peak limiter at -1dB to prevent clipping

    Video is copied (no re-encode). Audio encoded to 256k AAC.
    -movflags +faststart moves the moov atom for web streaming.
    """
    audio_filters = (
        "loudnorm=I=-16:LRA=11:TP=-1.5,"
        "highpass=f=80,"
        "equalizer=f=3000:width_type=o:width=1.5:g=1.5,"
        "alimiter=limit=0.891:attack=5:release=50"
    )
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


# ── Main assembly ──

def assemble_video(notes, slides_dir, audio_dir, output_path):
    """Main assembly: builds the final narrated video using split-track approach.

    Args:
        notes: List of {"slide": N, "text": "..."} from notes extraction
        slides_dir: Path to slide PNGs (slide_01.png, ...)
        audio_dir: Path to TTS audio files (slide_01.wav, ...)
        output_path: Path for final output video
    """
    v2_dir = output_path.parent / f"{output_path.stem}_work" / "v2"
    v2_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Create padded WAV audio per slide
    print("Step 1: Preparing per-slide audio (WAV lossless)...")
    slide_durations = []
    wav_files = []

    for note in notes:
        sn = note["slide"]
        audio_file = audio_dir / f"slide_{sn:02d}.wav"
        padded_wav = v2_dir / f"slide_{sn:02d}_padded.wav"

        if audio_file.exists():
            if not padded_wav.exists():
                pad_audio_to_wav(audio_file, padded_wav)
            dur = get_duration(padded_wav)
            slide_durations.append(dur)
            wav_files.append(padded_wav)
        else:
            silence_wav = v2_dir / f"slide_{sn:02d}_silence.wav"
            if not silence_wav.exists():
                create_silence_wav(SILENT_SLIDE_DUR, silence_wav)
            slide_durations.append(SILENT_SLIDE_DUR)
            wav_files.append(silence_wav)

    # Step 2: Concatenate into one audio track
    print("Step 2: Concatenating audio into single track...")
    full_audio = v2_dir / "full_audio.wav"
    if not full_audio.exists():
        concat_audio(wav_files, full_audio)

    # Step 3: Build video-only slideshow
    print("Step 3: Building video slideshow with crossfades...")
    video_only = v2_dir / "video_only.mp4"
    if not video_only.exists():
        build_video_slideshow(
            notes, slides_dir, slide_durations, video_only, TRANSITION_DURATION)

    # Step 4: Mux video + audio (single AAC encode)
    print("Step 4: Muxing video + audio (single AAC encode)...")
    mux_video_audio(video_only, full_audio, output_path)

    final_dur = get_duration(output_path)
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nDone! Output: {output_path.name}")
    print(f"  Duration: {final_dur:.1f}s ({final_dur/60:.1f} min)")
    print(f"  Size: {size_mb:.1f} MB")
```


## Legacy MP3 migration (explicit, optional)

Runtime expectation is now **WAV per narrated slide** (`audio/slide_XX.wav`). If a project still has legacy `slide_XX.mp3` files, convert them before assembly:

```bash
ffmpeg -y -i audio/slide_XX.mp3 -ar 44100 -ac 2 -c:a pcm_s16le audio/slide_XX.wav
```

MP3 inputs are legacy-only and should be treated as a migration path, not the standard workflow.

## Anti-Pattern: Pairwise xfade with audio (DO NOT USE)

The old approach merged segments one pair at a time:
```
slide_1+2 → merged_001.mp4
merged_001+slide_3 → merged_002.mp4
merged_002+slide_4 → merged_003.mp4
...
```

Each merge re-encoded BOTH video and audio as AAC. With 22 slides, slide 1's audio was re-encoded 21 times. AAC is lossy — each generation adds compression artifacts. After ~10+ re-encodes, the audio becomes scratchy, distorted, and painful to listen to.

**This approach is NEVER acceptable** for presentations with more than ~5 slides. Always use the split-track approach above.
