# Repository Guidelines

## Project Structure & Module Organization
- `extract_notes.py` extracts speaker notes from a PPTX into `notes.json`.
- `synthesize_tts.py` generates per-slide WAV audio using ElevenLabs.
- `assemble_video.py` builds the final MP4 from slide PNGs and audio.
- `export_slides.ps1` exports slide images as `slides/slide_01.png`, `slide_02.png`, etc.
- Configuration and reference data live in JSON/MD files such as `lang_config.json`, `glossary_en_es.json`, `quality-gates.md`, and `pipeline-troubleshooting.md`.
- The skill overview and pipeline steps are documented in `SKILL.md`.

## Architecture Overview
- The pipeline is linear: extract notes → (optional refinement) → export slides → synthesize TTS → assemble video.
- Audio duration drives slide timing; `assemble_video.py` aligns PNGs to per-slide WAV lengths and applies crossfades.
- Quality gates are documented in `quality-gates.md` and are intended to stop the pipeline on CRITICAL failures.

## Build, Test, and Development Commands
There is no build system; you run the scripts directly.
- Verify prerequisites:
  - `ffmpeg -version`
  - `python -c "import pptx, PIL, lxml, elevenlabs; print('OK')"`
- Extract notes:
  - `python extract_notes.py "<pptx_path>" "<work_dir>\\notes.json"`
- Export slides (Windows PowerPoint COM):
  - `powershell -ExecutionPolicy Bypass -File export_slides.ps1 -PptxPath "<pptx_path>" -OutputDir "<work_dir>\\slides"`
- Synthesize audio:
  - `python synthesize_tts.py "<work_dir>\\notes_refined.json" "<work_dir>\\audio" --lang en`
- Assemble video:
  - `python assemble_video.py "<work_dir>\\notes_refined.json" "<work_dir>\\slides" "<work_dir>\\audio" "<output.mp4>"`

## Coding Style & Naming Conventions
- Python uses 4-space indentation, type hints, and module docstrings. Keep functions small and focused.
- Follow existing naming: snake_case for functions/variables, `UPPER_SNAKE_CASE` for constants.
- Keep CLI usage strings up to date in the module docstring.
- Output naming patterns matter: `notes.json`, `notes_refined.json`, `slides/slide_01.png`, `audio/slide_01.wav`.

## Testing Guidelines
- No automated test framework is configured.
- Validate changes by running the full pipeline on a small PPTX and checking quality gates in `quality-gates.md`.
- If you add a new validation or rule, update the relevant documentation file.

## Commit & Pull Request Guidelines
- Git history uses concise, imperative, sentence-case messages (e.g., “Update project structure…”).
- Include a clear PR description, steps to reproduce (commands run), and any config changes.
- If output changes (audio/video), note the expected impact and provide example commands used.
