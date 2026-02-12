---
name: slide-to-video
description: Generates narrated videos from PowerPoint files using speaker notes as the script and ElevenLabs TTS for voiceover. Optionally translates the PPTX (slide text, notes, SmartArt) to a target language first. Auto-detects robotic documentation-style notes (Guidde, Scribe, Tango) and refines them into natural narration before TTS. Use when the user wants to create a video from a PPTX, turn slides into a narrated video, generate a presentation video with voiceover, translate a presentation to another language, produce a slide video in any language, or convert a Guidde guide to a narrated video.
---

# PPTX to Narrated Video

Generates a narrated video from a PowerPoint file by:
1. *(Optional)* Translating slide text + speaker notes to a target language (with glossary enforcement, length budgets, and automated QA)
2. Extracting speaker notes from each slide (the narration script)
3. *(Auto)* Refining notes if robotic documentation style is detected (Guidde, Scribe, Tango, etc.), with validation rules
4. Applying language-specific text normalization and TTS pronunciation fixes
5. Exporting slides as 1920x1080 PNG images
6. Synthesizing voiceover audio via ElevenLabs TTS (per-language voice config from `lang_config.json`)
7. Assembling a video: slide images + audio + crossfade transitions + broadcast-quality audio processing

Each slide's duration is driven by its audio length, producing natural pacing. Quality gates at each pipeline stage catch errors early.

# Prerequisites

**Required tools (verify before starting):**
- `ffmpeg` and `ffprobe` in PATH
- Python 3.11+ with packages: `python-pptx`, `python-dotenv`, `Pillow`, `lxml`
- PowerPoint (Windows COM automation) OR LibreOffice (fallback for slide export)
- API key in `~/.env`: `ELEVENLABS_API_KEY`
- ElevenLabs Python SDK: `elevenlabs`

**No OpenAI dependency.** Note refinement is done by Claude Code (Opus) directly in-context.
Translation (if needed) also uses Claude in-context — no external LLM API required.

**Configuration files (in the skill directory):**
- `lang_config.json` — Per-language voice IDs, VoiceSettings, TTS replacements, text normalization rules
- `glossary_en_es.json` — EN→ES terminology glossary + never-translate list (used during translation)

**Verify with:**
```
ffmpeg -version
python -c "import pptx, PIL, lxml, elevenlabs; print('OK')"
```

# Permanent Scripts

Reusable scripts live in the skill directory. Invoke them with CLI arguments — do NOT regenerate per-run.

| Script | Purpose | Usage |
|--------|---------|-------|
| `./extract_notes.py` | Extract speaker notes from PPTX | `python extract_notes.py <pptx> <output.json>` |
| `./export_slides.ps1` | Export slides as PNGs via PowerPoint COM | `powershell -File export_slides.ps1 -PptxPath <pptx> -OutputDir <dir>` |
| `./synthesize_tts.py` | Synthesize TTS audio per slide | `python synthesize_tts.py <notes.json> <audio_dir> [--voice-id ID] [--lang en]` |
| `./assemble_video.py` | Split-track video assembly | `python assemble_video.py <notes.json> <slides_dir> <audio_dir> <output.mp4>` |

The skill directory path is available as a variable during execution. Always use absolute paths when invoking these scripts.

# User Inputs

Gather these from the user before starting:

| Parameter | Required | Description |
|-----------|----------|-------------|
| PPTX file | Yes | Path to the PowerPoint file |
| Language | No | Language code for output filename suffix (default: `en`) |
| Translate? | No | Whether to translate the PPTX first (default: no) |
| Target language | No | Target language name if translating (e.g., `Spanish`) |
| ElevenLabs Voice ID | No | Voice for TTS (see defaults below) |
| Per-slide script | No | Optional JSON with per-slide narration (overrides notes) |
| Transition duration | No | Crossfade between slides, default 0.5s |
| Pre/post slide padding | No | Silence before/after each slide's audio, default 1.0s each |
| Output directory | No | Where to write the final video (default: PPTX's parent folder) |

## Default Voice IDs

| Language | Voice ID | Notes |
|----------|----------|-------|
| English (`en`) | `NHjG3gYsiwhncLX4Nfhc` | Default English voice |
| Spanish (`es`) | `sDh3eviBhiuHKi0MjTNq` | Default Spanish voice |

The `eleven_multilingual_v2` model handles any language from any voice, but a voice with native-language training produces more natural results. The user can pass any valid ElevenLabs voice ID to override these defaults.

# Pipeline Overview

```
PPTX ──> [Optional: Translate text + notes + SmartArt]
     ──> Extract Speaker Notes    (extract_notes.py)
     ──> [Auto: Refine notes]     (Claude Code Opus — in-context)
     ──> Export Slide PNGs         (export_slides.ps1)
     ──> Synthesize TTS Audio      (synthesize_tts.py)
     ──> Assemble Video            (assemble_video.py)
     ──> {stem}_{lang}.mp4
```

# Step 1 (Optional): Translate the PPTX

**Skip this step if** the PPTX is already in the target language.

**Script pattern:** See `./pptx-translation-script.md`
**Prompt template:** See `./translation-prompt-template.md`
**Glossary:** See `./glossary_en_es.json`
**QA checks:** See `./translation-qa.md`

### Translation Process

1. Open the PPTX with `python-pptx`
2. **Load glossary** from `glossary_en_es.json` — provides canonical term translations and never-translate list
3. Walk all slides -> shapes -> text frames -> **paragraphs** (not individual runs)
4. For each shape, detect its **role** (`title`, `subtitle`, `body`) using `PP_PLACEHOLDER` types
5. **Collect text at paragraph level** using `||N||` run boundary markers to preserve run structure. This ensures Claude translates complete sentences, not run fragments.
6. **Walk SmartArt diagrams** — stored in diagram data XML parts. Access via `diagramData` relationship type, iterate `<a:t>` elements. **Always freeze to `list()`** for stable order in both collection and write-back.
7. **Walk speaker notes** — these get translated too so the TTS reads the target language
8. Collect all text with metadata: `{id, text, role, location, type}` where type is `slide`, `table`, `smartart`, or `notes`

### Adaptive Batching

9. **Estimate batch sizes** based on character count (~4000 chars/batch) instead of fixed batch sizes. This prevents context window overflow with long text and avoids wasting capacity on short text.
10. **Claude (Opus) translates in-context** — applies the glossary, enforces length budgets (title ≤5 words, subtitle ≤8 words, body ≤120% of source), and uses the appropriate prompt based on text type (`notes` → narration prompt, others → slide prompt)
11. **Retry missing IDs** — if any text items are dropped from a batch response, automatically retranslate them in a smaller batch

### Write-Back & Validation

12. Write translations back, preserving formatting (font, size, color, bold, italic, position, images)
13. **Split paragraph translations** back to individual runs using `||N||` markers via `split_translation_to_runs()`
14. **For SmartArt:** write translated text back to the diagram data XML blob and update the part. Run a post-write regression check to verify text matches expected.
15. **MANDATORY:** Restore leading/trailing whitespace from original runs via `_restore_whitespace()` to prevent concatenation bugs (e.g., "Visiony" instead of "Vision y"). Run a validation scan after all writes.
16. **Run translation QA** — 6 automated checks (see `./translation-qa.md`): never-translate preservation, number preservation, bullet structure, title length budget, glossary compliance, empty translation detection. ERROR-severity items are auto-retranslated (max 2 attempts).
17. **Check length budgets** — auto-retranslate any items exceeding their word count limits
18. Save as `{stem}_{lang_code}.pptx`

**Checkpoint:** If `{stem}_{lang_code}.pptx` exists, skip this step.

# Step 2: Extract Narration Script

**Script:** `./extract_notes.py`

```bash
python extract_notes.py "<pptx_path>" "<work_dir>/notes.json"
```

The script:
- Extracts speaker notes from each slide
- Reports total slides, slides with/without notes
- Auto-detects robotic style (Guidde/Scribe/Tango patterns)
- Saves JSON array of `{"slide": N, "text": "..."}` objects

If translating, extract from the **translated** PPTX (not the original).

**Checkpoint:** If `notes.json` exists, skip extraction.

# Step 2.5 (Auto): Refine Notes for Narration

**Performed by Claude Code (Opus) directly in-context. No external API needed.**
**Validation rules:** See `./narration-refinement.md`

**Trigger:** The `extract_notes.py` script reports whether robotic style was detected (60%+ of non-empty notes start with "Click on", "Navigate to", "Select the", etc.). If detected, Claude reads `notes.json` and rewrites each note as natural conversational narration.

**What Claude fixes:**
- Repetitive "Click on the X button" → varied phrasing ("Select X", "Go ahead and open X", "You'll want to click X")
- Missing transitions between steps → adds "Next," "From here," "Now," "Once that's done,"
- No pauses or breathing room → adds natural sentence breaks and commas for TTS pacing
- Flat imperative tone → conversational training-video style

**Rules for rewriting:**
- Keep the SAME information and sequence — do not add, remove, or reorder steps
- Make it conversational, not robotic. Vary sentence openings.
- Keep each slide's narration to 1-3 sentences max, **10-55 words per slide**
- If a slide's text is empty, leave it empty (do not invent content)
- Add commas and short sentences for natural TTS pacing

**Post-Refinement Validation (see `narration-refinement.md`):**
After rewriting, Claude runs 5 validation checks on each slide:
1. **No new steps** — step count must be ≤ original
2. **Word cap** — each slide must be 10-55 words (with tolerance for already-short slides)
3. **Content overlap** — ≥30% content word overlap with original; if below, fall back to original
4. **Empty preservation** — if original was empty, refined must be empty too
5. **Sequence preservation** — key nouns/verbs must appear in same relative order

Any validation failure falls back to the original note for that slide (non-blocking).

**Process:**
1. Read `notes.json` from Step 2
2. If robotic style detected (or user forces it), rewrite notes directly
3. Run validation checklist on each refined note
4. Write refined notes as `notes_refined.json` in work directory
5. All subsequent steps (TTS, assembly) use `notes_refined.json` if it exists, else `notes.json`

**Checkpoint:** If `notes_refined.json` exists, use it instead of re-running refinement. The original `notes.json` is always preserved for reference.

# Step 3: Export Slide Images

**Script:** `./export_slides.ps1`

```powershell
powershell -ExecutionPolicy Bypass -File export_slides.ps1 -PptxPath "<pptx_path>" -OutputDir "<work_dir>\slides"
```

Uses PowerPoint COM (Windows) for pixel-perfect 1920x1080 PNG export. Falls back to LibreOffice on non-Windows.

**Important:** Uses integer constants: `-1` = msoTrue, `0` = msoFalse. Do NOT use .NET interop enum types.

Export from the **translated** PPTX if translating, otherwise from the original.

**Output:** `slides/slide_01.png`, `slides/slide_02.png`, ... in the work directory.

**Checkpoint:** If slide PNG directory has the expected number of files, skip export.

# Step 4: Synthesize TTS Audio

**Script:** `./synthesize_tts.py`
**Language config:** See `./lang_config.json`
**Normalization rules:** See `./tts-normalization.md`

```bash
python synthesize_tts.py "<work_dir>/notes_refined.json" "<work_dir>/audio" --lang en
```

## Voice Quality Features

Five improvements over baseline ElevenLabs calls:

### 1. Per-Language Voice Config (`lang_config.json`)
The script loads `lang_config.json` from the skill directory, which provides per-language settings:
- `voice_id` — optimized voice for each language
- `voice_settings` — tuned stability, similarity_boost, speed per language
- `tts_replacements` — language-specific pronunciation fixes
- `normalization` — language-specific text expansion rules

Falls back to hardcoded defaults if the config file is missing.

### 2. Tuned VoiceSettings with Speaker Boost
```python
# English defaults (Spanish uses slightly different values)
VoiceSettings(
    stability=0.5,           # Natural variation without instability
    similarity_boost=0.8,    # High clarity + similarity enhancement
    style=0.0,               # Clean narration, no style exaggeration
    use_speaker_boost=True,  # Boosts fidelity to original speaker voice
    speed=1.0,               # Normal speed
)
```
`use_speaker_boost=True` is the single biggest quality improvement — it activates additional processing to match the original speaker's characteristics more closely.

### 3. Uncompressed PCM Output (`pcm_44100`)
The script requests `pcm_44100` (raw 16-bit PCM at 44.1kHz) instead of `mp3_44100_192`. This eliminates MP3 compression artifacts entirely at the TTS stage. The raw PCM bytes are wrapped in a WAV header and saved as `.wav` files. Since the assembly pipeline uses WAV intermediates anyway, there's zero benefit to receiving compressed audio from ElevenLabs.

### 4. Text Normalization Pipeline (`tts-normalization.md`)
Before sending text to ElevenLabs, the script runs a two-stage normalization pipeline:
1. **Language-specific normalization** — expands units, abbreviations, and symbols into spoken forms (e.g., `100°F` → `100 degrees Fahrenheit`, `$50` → `50 dollars`)
2. **Pronunciation replacements** — fixes known mispronunciations of brand names and acronyms (e.g., `I&I` → `Eye and Eye`, `SmartBarrel` → `Smart Barrel`)

Rules are loaded from `lang_config.json` and can be extended without modifying the script.

### 5. Pronunciation Fixes (`TTS_REPLACEMENTS`)
Regex-based text preprocessing (loaded from `lang_config.json` or hardcoded fallbacks):
```python
TTS_REPLACEMENTS = {
    r'\bI&I\b': 'Eye and Eye',
    r'\bSmartBarrel\b': 'Smart Barrel',
}
```
Add entries to `lang_config.json` when TTS mispronounces a word or brand name.

**Output:** `audio/slide_01.wav`, `audio/slide_02.wav`, ... (WAV files, not MP3)

**Checkpoint:** Per-slide — if `audio/slide_NN.wav` exists, skip that slide.

# Step 5: Assemble Final Video

**Script:** `./assemble_video.py`

```bash
python assemble_video.py "<work_dir>/notes_refined.json" "<work_dir>/slides" "<work_dir>/audio" "<output.mp4>"
```

**CRITICAL — Split-track assembly to avoid audio degradation:**
Audio and video MUST be built as separate tracks and muxed at the end. The audio track is encoded to AAC exactly once. **Do NOT use pairwise xfade merges that include audio** — each merge re-encodes audio as AAC, and with N slides the earliest audio gets re-encoded N-1 times, causing severe scratchy/artifacted audio.

**Assembly strategy (4 steps):**
1. **Pad per-slide audio as lossless WAV** — use `pcm_s16le` intermediates, NOT AAC, to avoid lossy compression at this stage. For silent slides, create a WAV of silence.
2. **Concatenate all WAVs** into one continuous lossless audio track using ffmpeg concat demuxer (no re-encode: `-c:a pcm_s16le`).
3. **Build video-only slideshow** from slide PNGs with xfade crossfade transitions (`-an` flag, no audio in the filter graph). Use per-slide durations matching their padded audio. Falls back to concat demuxer if xfade fails.
4. **Mux video + audio** with broadcast-quality audio processing in a single final step:
   - `-c:v copy` (no video re-encode)
   - Audio filter chain: `loudnorm=I=-16:LRA=11:TP=-1.5` (EBU R128 broadcast loudness) → `highpass=f=80` (remove low-frequency rumble) → `equalizer=f=3000:width_type=o:width=1.5:g=1.5` (speech presence boost) → `alimiter=limit=0.891:attack=5:release=50` (peak limiter at -1dB)
   - `-c:a aac -b:a 256k` (single high-quality AAC encode)
   - `-ar 44100` (consistent sample rate)
   - `-movflags +faststart` (web streaming optimization)
   - `-shortest` (trim any duration mismatch)

**Output:** `{pptx_stem}_{lang}.mp4` in the PPTX's parent folder.

# Configuration Constants

```python
# Video
WIDTH = 1920
HEIGHT = 1080
FPS = 30
CRF = 18            # Video quality (lower = better, 18 is visually lossless)
PRESET = "medium"    # Encoding speed/quality tradeoff

# Timing
PRE_PAD = 1.0        # Silence before audio on each slide (seconds)
POST_PAD = 1.0       # Silence after audio on each slide (seconds)
SILENT_SLIDE_DUR = 2.0  # Duration for slides with no speaker notes
TRANSITION_DURATION = 0.5  # Crossfade between slides

# TTS
TTS_MODEL = "eleven_multilingual_v2"
TTS_OUTPUT_FORMAT = "pcm_44100"  # Uncompressed — best quality

# Audio (final mux)
AAC_BITRATE = "256k"  # High-quality AAC (single encode)
AUDIO_SAMPLE_RATE = 44100

# Default voices (overridden by lang_config.json if present)
DEFAULT_VOICES = {
    "en": "NHjG3gYsiwhncLX4Nfhc",
    "es": "sDh3eviBhiuHKi0MjTNq",
}

# Default TTS pronunciation fixes (overridden by lang_config.json if present)
TTS_REPLACEMENTS = {
    r'\bI&I\b': 'Eye and Eye',
    r'\bI & I\b': 'Eye and Eye',
    r'\bI&I Soft Craft Solutions\b': 'Eye and Eye Soft Craft Solutions',
    r'\bSmartBarrel\b': 'Smart Barrel',
    r'\bSmartbarrel\b': 'Smart Barrel',
}
```

All values are overridable by the user. Constants are defined in the permanent scripts. Per-language voice IDs, VoiceSettings, pronunciation replacements, and text normalization rules are configured in `lang_config.json`.

# Quality Gates

Quality gates run automatically at each pipeline stage. See `./quality-gates.md` for full definitions.

| Gate | When | Key Checks |
|------|------|------------|
| Post-Translation | After Step 1 | Never-translate terms preserved, length budgets met, no empty translations |
| Post-Extraction | After Step 2 | Notes JSON valid, slide count matches PPTX |
| Post-Refinement | After Step 2.5 | Word caps met, no content divergence, empty preservation |
| Post-Export | After Step 3 | Correct number of PNGs, resolution 1920x1080 |
| Post-TTS | After Step 4 | Audio files present for all narrated slides, non-zero duration |
| Post-Assembly | After Step 5 | Video playable, duration within expected range, audio track present |

**CRITICAL** checks stop the pipeline. **WARNING** checks are logged but non-blocking. Each gate has specific recovery steps documented in `quality-gates.md` and `pipeline-troubleshooting.md`.

# Output Naming & Directory

**All output is placed in the PPTX's parent folder** — derived dynamically from the input path, never hardcoded. Do NOT place output in the current working directory if it differs from the PPTX's location.

```python
from pathlib import Path
pptx_path = Path(user_provided_pptx_path)
base_dir = pptx_path.parent          # All output goes here
stem = pptx_path.stem                # e.g. "Presentation"
work_dir = base_dir / f"{stem}_{lang}_work"
output_video = base_dir / f"{stem}_{lang}.mp4"
```

Given input `Presentation.pptx` and language `es`:
- Translated PPTX *(if translating)*: `Presentation_es.pptx`
- Final video: `Presentation_es.mp4`
- Work directory: `Presentation_es_work/` (contains slides/, audio/, v2/ intermediates)

# Supporting Files

**Permanent scripts (invoke directly):**
- `./extract_notes.py` — Extract speaker notes from PPTX + robotic style detection
- `./export_slides.ps1` — PowerPoint COM slide export (Windows)
- `./synthesize_tts.py` — ElevenLabs TTS with quality optimizations
- `./assemble_video.py` — Split-track video assembly

**Reference documentation:**
- `./translation-prompt-template.md` — Translation system prompts (slide text + narration) with glossary placeholders
- `./pptx-translation-script.md` — Python patterns for PPTX translation (paragraph-level, SmartArt, adaptive batching)
- `./slide-export-script.md` — Slide export reference (PowerShell + LibreOffice details)
- `./video-assembly-script.md` — Split-track assembly reference with broadcast audio processing
- `./pipeline-troubleshooting.md` — Detailed troubleshooting for common issues

**Configuration & data files:**
- `./lang_config.json` — Per-language voice IDs, VoiceSettings, TTS replacements, text normalization rules
- `./glossary_en_es.json` — EN→ES terminology glossary + never-translate list

**Validation & quality:**
- `./translation-qa.md` — 6 automated post-translation QA checks (ERROR/WARNING severity)
- `./narration-refinement.md` — Refinement validation rules (word caps, divergence fallback)
- `./tts-normalization.md` — Language-specific TTS text normalization patterns and rules
- `./quality-gates.md` — Pipeline-wide quality gate definitions (6 gates, per-stage checks)
