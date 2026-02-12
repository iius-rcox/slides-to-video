# PPTX Translation Script (Automated Pipeline)

This repository uses an **automated translation pipeline** (not a semi-manual/in-context workflow).

The implementation is `translate_pptx.py`, which translates slide text, tables, SmartArt text, and speaker notes through batched API calls, then writes translations back while preserving formatting.

## Preflight (fail-fast) requirements

`translate_pptx.py` performs a preflight check before any work begins and exits immediately if any required artifact is missing:

- Input PPTX path (`--input-pptx`) must exist
- Glossary artifact (`--glossary`) must exist
- Prompt template artifact (`--prompt-template`) must exist
- `ANTHROPIC_API_KEY` must be set

## CLI usage

```bash
python translate_pptx.py \
  --input-pptx ./deck_en.pptx \
  --output-pptx ./deck_es.pptx \
  --target-language Spanish \
  --lang-code es \
  --glossary ./glossary_en_es.json \
  --prompt-template ./translation-prompt-template.md \
  --report-json ./translation_report.json \
  --batch-char-limit 4000 \
  --max-retries 2
```

## Expected inputs

- `--input-pptx`: Source `.pptx`
- `--target-language`: Human-readable target language (example: `Spanish`)
- `--lang-code`: Language code for metadata (default: `es`)
- `--glossary`: JSON file with:
  - `glossary`: source→target terminology map
  - `never_translate`: exact tokens that must remain unchanged
- `--prompt-template`: Translation instruction template

## Expected outputs

- `--output-pptx`: Translated PowerPoint file
- `--report-json`: Stable machine-readable report with schema:

```json
{
  "schema_version": "1.0",
  "timestamp_utc": "...",
  "input_pptx": "...",
  "output_pptx": "...",
  "target_language": "Spanish",
  "lang_code": "es",
  "total_items": 0,
  "total_batches": 0,
  "translated_items": 0,
  "batches": [
    {
      "batch_index": 1,
      "item_ids": [0, 1, 2],
      "attempts": 1,
      "translated_count": 3,
      "missing_ids": [],
      "errors": []
    }
  ]
}
```

## Pipeline behavior

1. Deterministically collect text items in fixed order:
   - slides/shapes/text frames
   - tables
   - SmartArt XML text nodes (`diagramData` relationships sorted by `rId`)
   - notes
2. Batch by character budget (`--batch-char-limit`)
3. Translate each batch with retries (`--max-retries`) for transient failures or missing IDs
4. Enforce glossary and never-translate rules post-translation
5. Write back translations while preserving run-level formatting and whitespace boundaries
6. Save translated PPTX and write JSON report

## Determinism and consistency guarantees

- Stable `id` assignment from deterministic traversal
- Stable report schema (`schema_version: 1.0`)
- Retry loop only for unresolved IDs/errors; no nondeterministic shape iteration
- Explicit output artifacts for downstream automation (`output_pptx`, `report_json`)
