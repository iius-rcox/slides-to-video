# Pipeline Troubleshooting

Detailed solutions for common issues encountered during the PPTX-to-narrated-video pipeline.

## Translation Issues

### Title text merged with body content
**Symptom:** Slide titles contain long sentences instead of short headings.

**Cause:** Claude received a flat list of text runs without structural context.

**Fix:** Ensure `_shape_role()` is detecting title placeholders and the "role" field is included in the translation payload. The translation prompt must have the "Slide Structure" section with hard length budgets: titles ≤ 5 words, subtitles ≤ 8 words.

### Text overflow on translated slides
**Symptom:** Translated text extends beyond the text box boundary.

**Cause:** Target language text is longer than English (e.g., Spanish is ~15-20% longer).

**Fix options:**
1. The length budget system should catch this automatically (body text ≤ 120% of source). Check that `check_length_budgets()` is running and auto-retranslating violations.
2. Use per-slide retranslation with stricter length constraints
3. Reduce font size in the PPTX before exporting (requires python-pptx font manipulation)

### "Visiony" whitespace concatenation bug
**Symptom:** Words run together in the translated PPTX (e.g., "Visiony Mision" instead of "Vision y Mision").

**Cause:** PPTX text runs may have leading/trailing spaces. Translation strips these, and when runs are concatenated by PowerPoint, the spaces are missing.

**Fix:** Always use `_restore_whitespace()` when writing translations back. This is MANDATORY for every write-back. The validation scan after `apply_translations()` should catch any missed runs:
```python
def _restore_whitespace(original, translated):
    leading = original[:len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()):]
    return leading + translated.strip() + trailing
```

### SmartArt text not translated
**Symptom:** SmartArt diagrams on slides still show English text after translation.

**Cause:** python-pptx does NOT natively expose SmartArt text through the standard `shape.has_text_frame` / `shape.text_frame.paragraphs` traversal. SmartArt data is stored in separate diagram XML parts (`/ppt/diagrams/dataN.xml`), linked to slides via `diagramData` relationships.

**Fix:** The `collect_texts()` function must explicitly walk SmartArt by:
1. Iterating each slide's `part.rels` for relationships with type `_DIAGRAM_DATA_RELTYPE`
2. Parsing the target part's `.blob` with `lxml.etree`
3. Finding all `<a:t>` text elements (DrawingML namespace) — **freeze to `list()` for stable order**
4. Collecting them with `type: "smartart"` metadata

The `apply_translations()` function must write back by:
1. Parsing the diagram data XML blob again
2. Iterating `<a:t>` elements in the same order — **freeze to `list()` to match collection**
3. Replacing text with translations (using `_restore_whitespace()`)
4. Serializing back via `etree.tostring()` and assigning to `dgm_part._blob`

**Key imports:**
```python
from lxml import etree
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_DIAGRAM_DATA_RELTYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData"
)
```

### SmartArt text order mismatch after translation
**Symptom:** SmartArt text is translated but items appear in wrong positions (swapped labels, wrong hierarchy).

**Cause:** The `<a:t>` element iterator returned elements in different order during collection vs. write-back.

**Fix:** Always freeze the iterator to a `list()` in both `collect_texts()` and `apply_translations()`:
```python
t_elements = list(dgm_root.iter(f"{{{_A_NS}}}t"))
```
Add a post-write regression check: after writing translations back, re-parse the XML blob and verify each `<a:t>` element's text matches the expected translation.

### Translation drops some text items
**Symptom:** Some text items are missing translations after a batch.

**Cause:** With fixed batch sizes, a batch may exceed the context window, or Claude may lose track of some IDs.

**Fix:** The adaptive batching system (`estimate_batch_size()`) targets ~4000 characters per batch to avoid this. If items are still dropped, `retry_missing_ids()` automatically retranslates just the missing items in a smaller batch. Check for these log messages:
```
  Retrying N missing IDs from batch...
```
If retries keep failing, reduce the character target in `estimate_batch_size()`.

### Translation QA check failures
**Symptom:** QA checks report errors after translation (never-translate violations, length budget exceedances, empty translations).

**Cause:** Various — see `translation-qa.md` for the 6 automated checks.

**Fix:** ERROR-severity items are automatically retranslated (max 2 attempts). If errors persist:
1. Check the glossary (`glossary_en_es.json`) for correctness
2. Verify the translation prompt includes `{GLOSSARY_ENTRIES}` and `{NEVER_TRANSLATE_LIST}` placeholders
3. For persistent length budget violations, the target language may need longer phrasing — consider relaxing the body budget from 120% to 130%
4. Review `translation-qa.md` for details on each check

### Glossary terms not used consistently
**Symptom:** Translation uses different terms for the same concept across slides.

**Cause:** The glossary wasn't loaded or formatted into the prompt correctly.

**Fix:** Verify that `glossary_en_es.json` is in the skill directory and contains the expected terms. Check that the formatted glossary text is included in the translation prompt via `{GLOSSARY_ENTRIES}`. The QA check `check_glossary_compliance()` flags inconsistencies as WARNINGs.

## Slide Export Issues

### PowerPoint COM hangs / doesn't release
**Symptom:** Script hangs, or next run fails because PowerPoint is still running.

**Fix:**
```powershell
Stop-Process -Name POWERPNT -Force -ErrorAction SilentlyContinue
```
Always wrap COM operations in `try/finally` with proper cleanup.

### PowerPoint COM type errors (msoTrue/msoFalse)
**Symptom:** `Cannot convert value "msoTrue"` error.

**Fix:** Use integer constants: `-1` for msoTrue, `0` for msoFalse. Do NOT use .NET interop enum types.

### LibreOffice renders differently
**Symptom:** Exported slides look different from PowerPoint rendering.

**Fix:** This is expected. Use PowerPoint COM on Windows for best fidelity. LibreOffice is a fallback for non-Windows environments.

## Narration Refinement Issues

### Refinement invents steps not in original
**Symptom:** Refined notes contain information or instructions that don't appear in the original speaker notes.

**Cause:** Claude expanded sparse notes with additional detail.

**Fix:** The narration refinement validation rules (`narration-refinement.md`) enforce this automatically. If new steps are detected (step count > original), the pipeline falls back to the original note for that slide. If this happens frequently:
1. Make the refinement prompt more explicit: "ONLY rewrite existing content. NEVER add new information."
2. Check that the content word overlap is ≥ 30% (Rule 3 in `narration-refinement.md`)
3. Delete `notes_refined.json` and re-run

### Refinement divergence — fallback to original
**Symptom:** Log shows "Content divergence detected on slide N, falling back to original."

**Cause:** The refined text has < 30% content word overlap with the original, meaning it drifted too far from the source material.

**Fix:** This is the safety net working correctly. The original note is used instead. If many slides trigger this:
1. The notes may be too terse for meaningful refinement — consider skipping refinement entirely
2. Lower the divergence threshold from 0.30 to 0.20 if light rewording is acceptable
3. Check that the refinement prompt emphasizes faithfulness to the original

### Refined notes exceed word cap
**Symptom:** Slide narration is too long, causing slow-paced TTS audio.

**Cause:** Refined note exceeds the 55-word per-slide cap.

**Fix:** The validation rules attempt to shorten once. If still over 55 words, the pipeline falls back to the original note. To prevent this:
1. Ensure the refinement prompt says "Keep each slide under 55 words"
2. Shorter source notes produce shorter refinements — edit the original if needed

### LLM drops a slide's content
**Symptom:** A slide that had notes in `notes.json` has an empty `text` field in `notes_refined.json`.

**Cause:** Claude may return empty strings for short or ambiguous inputs.

**Fix:** The empty preservation check (Rule 4) catches this — if the original was non-empty, the original note is used as fallback. The validation checklist runs automatically after refinement.

### Auto-detection triggers on normal notes
**Symptom:** Notes that aren't robotic get refinement applied unnecessarily, changing their tone.

**Cause:** The 60% threshold for `needs_refinement()` was triggered because the notes legitimately start with phrases like "Click on" or "Navigate to" as part of normal training content.

**Fix:** The user can force refinement off. If this happens frequently, raise the threshold from 0.6 to 0.75, or add more specific patterns to `ROBOTIC_PATTERNS` that are less likely to match natural prose.

## TTS / Audio Issues

### ElevenLabs API rate limit / transient API failures
**Symptom:** `429 Too Many Requests`, `5xx`, or timeout-like failures during TTS synthesis.

**Fix:** `synthesize_tts.py` now retries transient failures automatically with exponential backoff + jitter. Tune these knobs as needed:
- `TTS_API_MAX_RETRIES` (default `5`)
- `TTS_API_BACKOFF_BASE_SEC` (default `1.0`)
- `TTS_API_BACKOFF_MAX_SEC` (default `20.0`)
- `TTS_API_BACKOFF_JITTER_SEC` (default `0.5`)

If failures persist after retries:
1. Verify `ELEVENLABS_API_KEY`
2. Confirm the `voice_id` is valid
3. Check ElevenLabs service status
4. Re-run later (successful slide WAVs are reused)

### TTS audio sounds wrong language
**Symptom:** Audio is synthesized but sounds like the wrong language.

**Fix:** Ensure `model_id="eleven_multilingual_v2"` is set. The multilingual model auto-detects the language from the input text. If the speaker notes haven't been translated yet, the TTS will read the original language. Also verify the correct `voice_id` is being used (check `lang_config.json`).

### TTS mispronounces units or abbreviations
**Symptom:** TTS says "one hundred F" instead of "100 degrees Fahrenheit", or reads abbreviations literally.

**Cause:** The text normalization pipeline didn't expand the abbreviation.

**Fix:** Add a normalization rule to `lang_config.json` under the appropriate language's `normalization` section. See `tts-normalization.md` for the rule format. For example:
```json
"\\b(\\d+)°F\\b": "\\1 degrees Fahrenheit"
```

### Empty audio for a slide
**Symptom:** A slide that should have narration produces no audio file.

**Cause:** The speaker notes for that slide are empty or whitespace-only.

**Fix:** Check the notes extraction output (`notes.json`). Verify the slide has speaker notes in the PPTX. Slides with empty notes are treated as silent slides (static image with `SILENT_SLIDE_DUR` duration).

### WAV validation failures after synthesis
**Symptom:** A slide logs validation errors (empty/corrupt WAV, unreadable header, or duration below threshold) and eventually fails.

**Cause:** API returned incomplete audio, or text is too short for the configured duration floor.

**Fix:**
1. Re-run synthesis — transient output failures may self-heal
2. Reduce `TTS_MIN_WAV_DURATION_SEC` if short clips are expected (default `0.35`)
3. Increase `TTS_SLIDE_MAX_RETRIES` to allow more re-synthesis attempts (default `2`)
4. Review/refine the slide note text to ensure it contains meaningful narration

The script now emits per-slide status and a final failure summary so you can quickly isolate affected slides.

### Audio quality or pronunciation issues
**Symptom:** TTS mispronounces words or sounds unnatural.

**Fix options:**
1. Try a different ElevenLabs voice ID
2. Add entries to `tts_replacements` in `lang_config.json` for phonetic pronunciation
3. Add SSML-style hints in the notes (e.g., commas for pauses)
4. Edit the speaker notes directly to spell out problematic words phonetically
5. Check `lang_config.json` voice settings — adjusting `stability` and `similarity_boost` affects naturalness

### Audio sounds scratchy, distorted, or painful to listen to
**Symptom:** Final video audio has scratchy, compressed, distorted, or artifacted quality — even though individual TTS WAV files sound fine when played directly.

**Cause (MOST LIKELY): Repeated lossy AAC re-encoding during video assembly.** If the assembly step uses pairwise xfade merges that include audio (the old approach), each merge re-encodes the audio as AAC.

**Fix:** Use the **split-track assembly approach** (see `video-assembly-script.md`):
1. Keep all intermediate audio as lossless WAV (`pcm_s16le`)
2. Build the video track separately with `-an` (no audio)
3. Concatenate WAV files into one continuous audio track
4. Mux video + audio with a single AAC encode at the very end (`-c:a aac -b:a 256k`)

**NEVER use pairwise xfade merges that include audio tracks.**

**Other possible causes:**
1. **Voice quality:** Some ElevenLabs voices have better source recordings than others. Try a different voice ID
2. **Model:** Ensure you're using `eleven_multilingual_v2`
3. **Text length:** Very long text blocks (>2000 chars) can sometimes degrade quality. Split into shorter segments
4. **Loudnorm filter:** The `loudnorm` + EQ chain in the mux step should improve clarity. If audio sounds worse after muxing, check the audio filter chain in `assemble_video.py`

## Video Assembly Issues

### xfade filter graph fails
**Symptom:** FFmpeg error during the video-only xfade step.

**Cause:** A slide image may have unexpected dimensions, or the filter graph has a syntax error.

**Fix:** The script falls back to concat demuxer with hard cuts. Check that all slide PNGs have consistent dimensions. The `-an` flag ensures audio is never part of the xfade filter graph.

### Output video has no audio
**Symptom:** Final video plays but is silent.

**Cause:** Audio stream wasn't mapped correctly, or padded audio file is corrupt.

**Fix:** Check that `full_audio.wav` exists and has non-zero duration. Verify the padded WAV files play correctly. Re-run assembly after deleting the `v2/` directory in the work folder.

### Slides appear for wrong duration
**Symptom:** A slide shows for too long or too short.

**Cause:** The slide duration is `PRE_PAD + audio_duration + POST_PAD`. If audio duration is read incorrectly, the timing will be off.

**Fix:** Check individual audio durations with `ffprobe`. Verify `PRE_PAD` and `POST_PAD` values are correct (default 1.0s each). Silent slides use `SILENT_SLIDE_DUR` (default 2.0s).

### Video is very long or very short
**Symptom:** Output video duration is unexpected.

**Cause:** Each slide's duration is driven by its TTS audio length.

**Fix:** This is by design — the video pacing follows the narration. To shorten: edit the speaker notes to be more concise. To lengthen: add more detail to the notes. Adjust `PRE_PAD` / `POST_PAD` to add or remove silence around each slide.

## Quality Gate Failures

### Gate failure stops the pipeline
**Symptom:** Pipeline halts with "GATE [name]: FAILED" message.

**Cause:** A CRITICAL-severity check failed. See `quality-gates.md` for the full gate definitions.

**Fix:** Each gate has specific recovery steps:
- **Post-Translation gate:** Auto-retranslates failed items (max 2 attempts), then stops if still failing
- **Post-Extraction gate:** Re-runs extraction; if fails again, check the PPTX file for corruption
- **Post-Refinement gate:** Falls back to original `notes.json`
- **Post-Export gate:** Kills PowerPoint COM and re-exports; tries LibreOffice fallback
- **Post-TTS gate:** Deletes corrupt WAV and re-synthesizes that slide
- **Post-Assembly gate:** Clears `v2/` intermediates and re-runs assembly

### WARNING-level quality issues accumulating
**Symptom:** Many WARNING-level issues logged but pipeline continues. Final output has issues.

**Cause:** WARNING checks are non-blocking by design.

**Fix:** Review the logged warnings after pipeline completion. Common patterns:
- Many glossary compliance warnings → update the glossary or relax compliance checking
- Many body length warnings → target language is naturally more verbose; consider raising the 120% threshold
- Audio duration warnings → some slides may have too much or too little narration

## Environment Issues

### `ffmpeg` not found
**Fix (Windows):** `winget install Gyan.FFmpeg` then restart terminal.

### `python-pptx` import error
**Fix:** `pip install python-pptx`

### `elevenlabs` import error
**Fix:** `pip install elevenlabs`

### `Pillow` or `lxml` import error
**Fix:** `pip install Pillow lxml`

### `.env` API keys not loading
**Fix:** Ensure `~/.env` file exists and contains:
```
ELEVENLABS_API_KEY=...
```
No OpenAI API key is needed — translation and refinement are done by Claude Code in-context.

### `lang_config.json` not found
**Fix:** Ensure `lang_config.json` is in the skill directory (`~/.claude/skills/slide-to-video/`). The TTS script falls back to hardcoded defaults if the file is missing, but per-language normalization and voice settings won't apply.
