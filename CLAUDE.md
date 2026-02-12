# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A Claude Code skill that converts PowerPoint files into narrated videos. The pipeline extracts speaker notes as the narration script, synthesizes voiceover via ElevenLabs TTS, and assembles a video with crossfade transitions and broadcast-quality audio. Optionally translates the PPTX to another language first (using Claude in-context, no external LLM API).

## Prerequisites

- `ffmpeg` / `ffprobe` in PATH
- Python 3.11+ with: `python-pptx`, `python-dotenv`, `Pillow`, `lxml`, `elevenlabs`
- PowerPoint (Windows COM) for slide export; LibreOffice as fallback
- `ELEVENLABS_API_KEY` in `~/.env`

Verify: `ffmpeg -version` and `python -c "import pptx, PIL, lxml, elevenlabs; print('OK')"`

## Commands

There is no build system or test framework. Scripts are invoked directly:

```powershell
# Extract speaker notes
python extract_notes.py "<pptx>" "<work_dir>\notes.json"

# Export slides as PNGs (Windows)
powershell -ExecutionPolicy Bypass -File export_slides.ps1 -PptxPath "<pptx>" -OutputDir "<work_dir>\slides"

# Synthesize TTS audio
python synthesize_tts.py "<work_dir>\notes_refined.json" "<work_dir>\audio" --lang en

# Assemble final video
python assemble_video.py "<work_dir>\notes_refined.json" "<work_dir>\slides" "<work_dir>\audio" "<output.mp4>"
```

Always use absolute paths. The skill directory path is available during execution.

## Architecture

### Pipeline Flow

```
PPTX → [Translate] → Extract Notes → [Refine Notes] → Export PNGs → TTS Audio → Assemble Video → {stem}_{lang}.mp4
```

Steps in brackets are conditional: translation is optional; refinement auto-triggers when 60%+ of notes are robotic (Guidde/Scribe/Tango style).

### Key Design Decisions

**Split-track video assembly** (`assemble_video.py`): Audio and video are built as separate tracks, muxed at the end with a single AAC encode. Never use pairwise xfade merges that include audio — each merge re-encodes AAC, causing severe audio degradation after ~10 slides.

**Uncompressed TTS output**: `synthesize_tts.py` requests `pcm_44100` from ElevenLabs (raw PCM), wraps it in WAV headers. All intermediates stay as lossless WAV (`pcm_s16le`) until the final mux.

**Paragraph-level translation**: Text is collected at paragraph level with `||N||` run boundary markers. Claude translates full paragraphs, then `split_translation_to_runs()` splits back to individual runs, preserving formatting.

**Mandatory whitespace restoration**: Every PPTX write-back must use `_restore_whitespace()` to prevent concatenation bugs (e.g., "Visiony" instead of "Vision y").

**SmartArt handling**: SmartArt text is in diagram XML parts, not standard text frames. Access via `diagramData` relationship type, iterate `<a:t>` elements. Always freeze iterators to `list()` in both collection and write-back for stable ordering.

### Scripts (permanent, do not regenerate)

| Script | Role |
|--------|------|
| `extract_notes.py` | Extracts notes from PPTX, detects robotic style |
| `export_slides.ps1` | PowerPoint COM slide export (1920x1080 PNGs) |
| `synthesize_tts.py` | ElevenLabs TTS with normalization pipeline |
| `assemble_video.py` | Split-track video assembly with broadcast audio |

### Configuration Files

| File | Purpose |
|------|---------|
| `lang_config.json` | Per-language voice IDs, VoiceSettings, TTS replacements, normalization rules |
| `glossary_en_es.json` | EN-ES terminology glossary + never-translate list |

To add a new language: copy the `en` block in `lang_config.json` as a template.

### Reference Documentation

| File | Content |
|------|---------|
| `pptx-translation-script.md` | Full translation script pattern (collect, batch, translate, write-back) |
| `translation-prompt-template.md` | System prompts for slide text vs. narration translation |
| `translation-qa.md` | 6 automated QA checks (ERROR/WARNING severity) |
| `narration-refinement.md` | Refinement validation rules (word caps, divergence fallback) |
| `tts-normalization.md` | TTS text normalization patterns per language |
| `quality-gates.md` | 6 pipeline quality gates with CRITICAL/WARNING definitions |
| `pipeline-troubleshooting.md` | Common issues and fixes |
| `video-assembly-script.md` | Split-track assembly rationale and pattern |
| `slide-export-script.md` | PowerPoint COM + LibreOffice export details |

## Critical Invariants

- **PowerPoint COM constants**: Use `-1` (msoTrue) and `0` (msoFalse). Do not use .NET interop enum types.
- **Output location**: All output goes in the PPTX's parent folder, derived dynamically. Never hardcode paths.
- **Checkpointing**: Each pipeline step checks for existing output before re-running (idempotent).
- **Quality gates**: CRITICAL failures stop the pipeline; WARNING issues are logged. See `quality-gates.md`.
- **Note refinement**: Done by Claude in-context (no external API). Falls back to original notes on validation failure — never blocks the pipeline.
- **Translation**: Also done by Claude in-context. Adaptive batching targets ~4000 chars/batch. Auto-retries missing IDs and QA failures (max 2 attempts).

## Naming Conventions

- Python: snake_case functions/variables, UPPER_SNAKE_CASE constants, 4-space indent, type hints
- Output files: `notes.json`, `notes_refined.json`, `slides/slide_01.png`, `audio/slide_01.wav`
- Work directory: `{stem}_{lang}_work/`
- Final video: `{stem}_{lang}.mp4`
