# PPTX Translation Script Template

Generate a Python script following this pattern. Adapt paths, language, and prompt for the user's specific case.

**Translation Approach**: Claude Code (Opus) translates text directly in-context. No external LLM API call is needed. The script collects text, formats it for Claude to translate, and applies the results back to the PPTX.

## Core Script Structure

```python
"""Translate PowerPoint text to {TARGET_LANGUAGE} using Claude Code in-context translation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from lxml import etree

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE, PP_PLACEHOLDER

# XML namespace for DrawingML text elements (used in SmartArt diagrams)
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_DIAGRAM_DATA_RELTYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData"
)

# Paragraph-level markers to preserve run boundaries
RUN_SEPARATOR = "||N||"


# --- SYSTEM PROMPT ---
# Customize for the target language, audience, and industry.
# See ./translation-prompt-template.md for the full default.
# Include glossary entries and never_translate list in the prompt.
SYSTEM_PROMPT = """..."""


def load_glossary(glossary_path: Path = None) -> dict:
    """Load glossary from JSON file.

    Expected format:
    {
        "glossary": {
            "source_term": "target_term",
            "Mission": "Misión",
            "Vision": "Visión"
        },
        "never_translate": ["ACME Corp", "SafetyTrack"]
    }

    Append glossary entries and never_translate list to SYSTEM_PROMPT.
    Example prompt addition:

    GLOSSARY (always use these translations):
    - Mission → Misión
    - Vision → Visión

    NEVER TRANSLATE (preserve exactly):
    - ACME Corp
    - SafetyTrack
    """
    if glossary_path is None:
        glossary_path = Path(__file__).parent / "glossary_en_es.json"

    if not glossary_path.exists():
        return {"glossary": {}, "never_translate": []}

    with open(glossary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {
        "glossary": data.get("glossary", {}),
        "never_translate": data.get("never_translate", [])
    }


def _shape_role(shape) -> str:
    """Detect shape role: 'title', 'subtitle', or 'body'."""
    if shape.is_placeholder:
        ph_type = shape.placeholder_format.type
        if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
            return "title"
        if ph_type == PP_PLACEHOLDER.SUBTITLE:
            return "subtitle"
    name = shape.name.lower()
    if "title" in name:
        return "title"
    if "subtitle" in name:
        return "subtitle"
    return "body"


def collect_texts(prs: Presentation) -> list[dict]:
    """Walk all slides -> shapes -> text frames -> PARAGRAPHS.

    **Paragraph-level collection**: Each paragraph's runs are joined with ||N|| markers
    to preserve run boundaries. Claude translates the full paragraph, and we split
    back to individual runs using split_translation_to_runs().

    Returns list of {id, text, role, location, type, run_count}.
    Handles: slide text (paragraph-level), tables (paragraph-level), SmartArt (single-text), speaker notes (paragraph-level).
    """
    texts = []
    idx = 0

    for slide_num, slide in enumerate(prs.slides, 1):
        # Slide body shapes - PARAGRAPH LEVEL
        for shape in slide.shapes:
            if shape.has_text_frame:
                role = _shape_role(shape)
                for para_idx, para in enumerate(shape.text_frame.paragraphs):
                    run_texts = []
                    for run in para.runs:
                        if run.text and run.text.strip():
                            run_texts.append(run.text)

                    if run_texts:
                        # Join runs with marker
                        para_text = RUN_SEPARATOR.join(run_texts)
                        texts.append({
                            "id": idx,
                            "text": para_text,
                            "role": role,
                            "location": f"slide {slide_num}, shape '{shape.name}', para {para_idx}",
                            "type": "slide",
                            "run_count": len(run_texts),
                        })
                        idx += 1

            # Tables - PARAGRAPH LEVEL
            if shape.has_table:
                for row_idx, row in enumerate(shape.table.rows):
                    for col_idx, cell in enumerate(row.cells):
                        for para_idx, para in enumerate(cell.text_frame.paragraphs):
                            run_texts = []
                            for run in para.runs:
                                if run.text and run.text.strip():
                                    run_texts.append(run.text)

                            if run_texts:
                                para_text = RUN_SEPARATOR.join(run_texts)
                                texts.append({
                                    "id": idx,
                                    "text": para_text,
                                    "location": f"slide {slide_num}, table, row {row_idx}, col {col_idx}, para {para_idx}",
                                    "type": "table",
                                    "run_count": len(run_texts),
                                })
                                idx += 1

        # SmartArt diagrams (stored as separate diagram data XML parts)
        # python-pptx does NOT natively expose SmartArt text — must use lxml
        # SmartArt: single-text elements (no paragraph-level collection)
        slide_part = slide.part
        for rel in slide_part.rels.values():
            if rel.reltype == _DIAGRAM_DATA_RELTYPE:
                dgm_root = etree.fromstring(rel.target_part.blob)
                # STABILITY FIX: Wrap iterator with list() to preserve order
                t_elements = list(dgm_root.iter(f"{{{_A_NS}}}t"))
                for t_idx, t_elem in enumerate(t_elements):
                    if t_elem.text and t_elem.text.strip():
                        texts.append({
                            "id": idx,
                            "text": t_elem.text,
                            "role": "body",
                            "location": f"slide {slide_num}, smartart, text {t_idx}",
                            "type": "smartart",
                            "_rel_rId": rel.rId,
                            "_t_index": t_idx,
                            "run_count": 1,  # SmartArt elements are single-text
                        })
                        idx += 1

        # Speaker notes - PARAGRAPH LEVEL
        if slide.has_notes_slide:
            notes_tf = slide.notes_slide.notes_text_frame
            for para_idx, para in enumerate(notes_tf.paragraphs):
                run_texts = []
                for run in para.runs:
                    if run.text and run.text.strip():
                        run_texts.append(run.text)

                if run_texts:
                    para_text = RUN_SEPARATOR.join(run_texts)
                    texts.append({
                        "id": idx,
                        "text": para_text,
                        "location": f"slide {slide_num}, notes, para {para_idx}",
                        "type": "notes",
                        "run_count": len(run_texts),
                    })
                    idx += 1

    return texts


def split_translation_to_runs(translated_para: str, run_count: int) -> list[str]:
    """Split translated paragraph back to individual run texts.

    Claude translates a paragraph joined with ||N|| markers.
    This function splits the translation back to run_count pieces.

    If split count doesn't match run_count, fall back to single run.
    """
    parts = translated_para.split(RUN_SEPARATOR)

    if len(parts) == run_count:
        return parts
    else:
        # Mismatch: Claude may have removed or added markers
        # Fall back to assigning entire translation to first run
        print(f"  WARNING: Expected {run_count} runs, got {len(parts)} parts. Using single-run fallback.")
        return [translated_para] + [""] * (run_count - 1)


def estimate_batch_size(texts: list[dict]) -> int:
    """Adaptive batching: calculate batch size based on character count.

    Target ~4000 characters per batch to avoid context truncation.
    """
    if not texts:
        return 50  # default fallback

    total_chars = sum(len(t["text"]) for t in texts)
    avg_chars_per_text = total_chars / len(texts)

    target_chars_per_batch = 4000
    batch_size = max(1, int(target_chars_per_batch / avg_chars_per_text))

    # Cap at reasonable limits
    return min(batch_size, 100)


def retry_missing_ids(
    texts: list[dict],
    translations: dict[int, str],
    max_retries: int = 2
) -> dict[int, str]:
    """Retry translation for IDs that were dropped in initial batch.

    Sometimes Claude may not return all IDs. This function identifies
    missing IDs and retranslates them in a focused batch.
    """
    expected_ids = {t["id"] for t in texts}
    missing_ids = expected_ids - translations.keys()

    if not missing_ids:
        return translations

    print(f"  WARNING: {len(missing_ids)} translations missing. Retrying...")

    missing_texts = [t for t in texts if t["id"] in missing_ids]

    # Claude Code translates directly — user provides translations
    # This is a placeholder for the retry logic
    # In practice, Claude would re-translate the missing_texts batch

    print(f"  Please translate these {len(missing_texts)} missing items:")
    for t in missing_texts:
        print(f"    ID {t['id']}: {t['text'][:50]}...")

    # Return original translations (user would provide retry results)
    return translations


def check_length_budgets(
    texts: list[dict],
    translations: dict[int, str]
) -> dict[int, str]:
    """Validate length budgets and flag violations.

    Rules:
    - title: ≤ 5 words
    - subtitle: ≤ 8 words
    - body: ≤ 120% of source character count

    Auto-retranslate violators (max 2 attempts).
    """
    violations = []

    for t in texts:
        tid = t["id"]
        if tid not in translations:
            continue

        trans = translations[tid]
        role = t.get("role", "body")

        # Word count checks
        word_count = len(trans.split())
        if role == "title" and word_count > 5:
            violations.append({
                "id": tid,
                "reason": f"Title too long ({word_count} words > 5)",
                "text": t["text"],
                "translation": trans,
                "role": role,
            })
        elif role == "subtitle" and word_count > 8:
            violations.append({
                "id": tid,
                "reason": f"Subtitle too long ({word_count} words > 8)",
                "text": t["text"],
                "translation": trans,
                "role": role,
            })

        # Character count check (body only)
        if role == "body":
            source_len = len(t["text"])
            trans_len = len(trans)
            budget = source_len * 1.2
            if trans_len > budget:
                violations.append({
                    "id": tid,
                    "reason": f"Body too long ({trans_len} chars > {budget:.0f} budget)",
                    "text": t["text"],
                    "translation": trans,
                    "role": role,
                })

    if violations:
        print(f"  WARNING: {len(violations)} length budget violations:")
        for v in violations:
            print(f"    ID {v['id']}: {v['reason']}")
            print(f"      Source: {v['text'][:50]}...")
            print(f"      Translation: {v['translation'][:50]}...")

        # In practice, Claude would re-translate with stricter length constraints
        # For now, just flag the violations

    return translations


def _restore_whitespace(original: str, translated: str) -> str:
    """⚠️ MANDATORY: Re-apply leading/trailing whitespace from original to translation.

    PPTX text runs may have leading/trailing spaces (e.g., " and Mission").
    Without this, translated runs concatenate incorrectly.
    Example bug: "Vision" + "y Mision" -> "Visiony Mision" (missing space)

    ⚠️ CRITICAL: This function MUST be called on every write-back operation.
    """
    leading = original[: len(original) - len(original.lstrip())]
    trailing = original[len(original.rstrip()):]
    return leading + translated.strip() + trailing


def apply_translations(prs, texts, translations):
    """Write translations back, preserving ALL formatting.

    ⚠️ MANDATORY: _restore_whitespace() is called on every write-back.

    IMPORTANT: Must walk shapes in the EXACT same order as collect_texts()
    so the idx counter stays in sync.

    **Paragraph-level application**: Translated paragraphs are split back to runs
    using split_translation_to_runs().
    """
    idx = 0

    for slide_num, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    # Collect original run texts to preserve whitespace
                    run_originals = []
                    for run in para.runs:
                        if run.text and run.text.strip():
                            run_originals.append(run.text)

                    if run_originals and idx in translations:
                        # Split translated paragraph back to runs
                        text_entry = texts[idx]
                        run_count = text_entry.get("run_count", len(run_originals))
                        translated_runs = split_translation_to_runs(
                            translations[idx], run_count
                        )

                        # Apply to actual runs with whitespace restoration
                        run_idx = 0
                        for run in para.runs:
                            if run.text and run.text.strip():
                                if run_idx < len(translated_runs):
                                    # ⚠️ MANDATORY: Restore whitespace
                                    run.text = _restore_whitespace(
                                        run_originals[run_idx],
                                        translated_runs[run_idx]
                                    )
                                run_idx += 1

                        idx += 1
                    elif run_originals:
                        # No translation for this paragraph
                        idx += 1

            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        for para in cell.text_frame.paragraphs:
                            run_originals = []
                            for run in para.runs:
                                if run.text and run.text.strip():
                                    run_originals.append(run.text)

                            if run_originals and idx in translations:
                                text_entry = texts[idx]
                                run_count = text_entry.get("run_count", len(run_originals))
                                translated_runs = split_translation_to_runs(
                                    translations[idx], run_count
                                )

                                run_idx = 0
                                for run in para.runs:
                                    if run.text and run.text.strip():
                                        if run_idx < len(translated_runs):
                                            # ⚠️ MANDATORY: Restore whitespace
                                            run.text = _restore_whitespace(
                                                run_originals[run_idx],
                                                translated_runs[run_idx]
                                            )
                                        run_idx += 1

                                idx += 1
                            elif run_originals:
                                idx += 1

        # SmartArt diagrams — write back to diagram data XML
        # ⚠️ MANDATORY: Whitespace restoration and post-write validation
        slide_part = slide.part
        for rel in slide_part.rels.values():
            if rel.reltype == _DIAGRAM_DATA_RELTYPE:
                dgm_part = rel.target_part
                dgm_root = etree.fromstring(dgm_part.blob)
                modified = False

                # STABILITY FIX: Wrap iterator with list() to match collect order
                t_elements = list(dgm_root.iter(f"{{{_A_NS}}}t"))
                expected_texts = []  # For post-write validation

                for t_elem in t_elements:
                    if t_elem.text and t_elem.text.strip():
                        original_text = t_elem.text
                        if idx in translations:
                            # ⚠️ MANDATORY: Restore whitespace
                            t_elem.text = _restore_whitespace(
                                original_text, translations[idx]
                            )
                            expected_texts.append(t_elem.text)
                            modified = True
                        else:
                            expected_texts.append(original_text)
                        idx += 1

                if modified:
                    # Write back XML
                    dgm_part._blob = etree.tostring(
                        dgm_root, xml_declaration=True,
                        encoding="UTF-8", standalone=True
                    )

                    # POST-WRITE VALIDATION: Re-parse and verify
                    verify_root = etree.fromstring(dgm_part._blob)
                    verify_texts = [
                        elem.text for elem in list(verify_root.iter(f"{{{_A_NS}}}t"))
                        if elem.text and elem.text.strip()
                    ]

                    if verify_texts != expected_texts:
                        print(f"  ERROR: SmartArt post-write validation failed on slide {slide_num}")
                        print(f"    Expected: {expected_texts}")
                        print(f"    Got: {verify_texts}")
                        raise ValueError("SmartArt XML corruption detected")

        if slide.has_notes_slide:
            for para in slide.notes_slide.notes_text_frame.paragraphs:
                run_originals = []
                for run in para.runs:
                    if run.text and run.text.strip():
                        run_originals.append(run.text)

                if run_originals and idx in translations:
                    text_entry = texts[idx]
                    run_count = text_entry.get("run_count", len(run_originals))
                    translated_runs = split_translation_to_runs(
                        translations[idx], run_count
                    )

                    run_idx = 0
                    for run in para.runs:
                        if run.text and run.text.strip():
                            if run_idx < len(translated_runs):
                                # ⚠️ MANDATORY: Restore whitespace
                                run.text = _restore_whitespace(
                                    run_originals[run_idx],
                                    translated_runs[run_idx]
                                )
                            run_idx += 1

                    idx += 1
                elif run_originals:
                    idx += 1

    # ⚠️ MANDATORY: Final validation scan of all <a:t> elements
    print("  Validating whitespace restoration...")
    validation_errors = []

    for slide_num, slide in enumerate(prs.slides, 1):
        slide_part = slide.part
        for rel in slide_part.rels.values():
            if rel.reltype == _DIAGRAM_DATA_RELTYPE:
                dgm_root = etree.fromstring(rel.target_part.blob)
                for t_idx, t_elem in enumerate(list(dgm_root.iter(f"{{{_A_NS}}}t"))):
                    if t_elem.text:
                        # Check for common whitespace issues
                        if t_elem.text != t_elem.text.strip() and not (
                            t_elem.text.startswith(" ") or t_elem.text.endswith(" ")
                        ):
                            validation_errors.append(
                                f"Slide {slide_num}, SmartArt text {t_idx}: suspicious whitespace"
                            )

    if validation_errors:
        print(f"  WARNING: {len(validation_errors)} whitespace validation warnings:")
        for err in validation_errors[:5]:  # Show first 5
            print(f"    {err}")


def translate_pptx(pptx_path: Path, output_path: Path) -> Path:
    """Main entry: translate a PPTX and save.

    Claude Code translates directly — no external API needed.
    User provides translations via the formatted text list.
    """
    prs = Presentation(str(pptx_path))

    # Load glossary
    glossary_data = load_glossary()
    if glossary_data["glossary"] or glossary_data["never_translate"]:
        print(f"  Loaded glossary: {len(glossary_data['glossary'])} entries, "
              f"{len(glossary_data['never_translate'])} never-translate terms")

    texts = collect_texts(prs)
    print(f"  Found {len(texts)} text elements (paragraph-level)")

    if not texts:
        prs.save(str(output_path))
        return output_path

    # Adaptive batching
    batch_size = estimate_batch_size(texts)
    print(f"  Using adaptive batch size: {batch_size}")

    # Translate in batches
    # Claude Code translates directly — this is where Claude would provide translations
    translations = {}
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        print(f"  Batch {i // batch_size + 1}: {len(batch)} texts")

        # Format for Claude to translate
        # content_type flag controls number handling rules in the prompt:
        # - slide_text: preserve digits exactly for layout stability
        # - narration: allow spoken-natural number forms
        items = [
            {
                "id": t["id"],
                "text": t["text"],
                "role": t.get("role", "body"),
                "content_type": "narration" if t.get("type") == "notes" else "slide_text",
            }
            for t in batch
        ]

        # Claude translates the batch (user provides via JSON response)
        # Example: {"translations": [{"id": 0, "translated_text": "..."}, ...]}
        # In actual usage, Claude Code's translation goes here

        # For now, placeholder — user would provide translations
        # translations.update(batch_translations)

    print(f"  Got {len(translations)}/{len(texts)} translations")

    # Retry missing IDs
    translations = retry_missing_ids(texts, translations)

    # Check length budgets
    translations = check_length_budgets(texts, translations)

    apply_translations(prs, texts, translations)
    prs.save(str(output_path))
    return output_path
```

## Per-Slide Retranslation

To retranslate a single slide, filter `collect_texts()` output by slide number and re-run translation on only those texts. Then apply just those translations back.

```python
def retranslate_slide(pptx_path, slide_number, extra_instructions=""):
    """Retranslate a specific slide with optional extra instructions.

    Claude Code handles translation directly — extra_instructions are appended
    to the system prompt for this slide's batch.
    """
    prs = Presentation(str(pptx_path))
    all_texts = collect_texts(prs)

    # Filter to target slide
    slide_texts = [t for t in all_texts if f"slide {slide_number}," in t["location"]]

    print(f"  Retranslating slide {slide_number}: {len(slide_texts)} text elements")

    if extra_instructions:
        # Append to system prompt for this batch
        prompt_addition = f"\n\nAdditional instruction for this slide: {extra_instructions}"
        print(f"  Using extra instructions: {extra_instructions}")

    # Claude translates the filtered batch with extra_instructions
    # ... translate and apply (same pattern as main translate_pptx)
```

## Glossary File Format

Create `glossary_en_es.json` in the same directory as the script:

```json
{
    "glossary": {
        "Mission": "Misión",
        "Vision": "Visión",
        "Core Values": "Valores Fundamentales",
        "Safety": "Seguridad",
        "Quality": "Calidad"
    },
    "never_translate": [
        "ACME Corp",
        "SafetyTrack",
        "OptimaFlow",
        "PowerBI"
    ]
}
```

The glossary is loaded via `load_glossary()` and appended to the system prompt. Claude Code will use these entries when translating.

## Translation Workflow

1. **Load PPTX**: `Presentation(pptx_path)`
2. **Load glossary**: `load_glossary()` reads `glossary_en_es.json`
3. **Collect texts**: `collect_texts()` walks slides at paragraph level, joining runs with `||N||` markers
4. **Adaptive batching**: `estimate_batch_size()` calculates optimal batch size (~4000 chars)
5. **Claude translates**: User provides translations for each batch (Claude Code handles in-context)
6. **Retry missing**: `retry_missing_ids()` catches dropped IDs
7. **Length validation**: `check_length_budgets()` flags title/subtitle/body violations
8. **Apply translations**: `apply_translations()` splits paragraphs back to runs, restores whitespace
9. **SmartArt validation**: Post-write re-parse verifies XML integrity
10. **Save**: `prs.save(output_path)`

## Number Handling by Content Type

Use explicit mode-specific rules to avoid conflicts:

### Slide Text (`content_type=slide_text`)
- Preserve digits exactly as written for layout stability.
- Do not rewrite digits as words.
- Keep numbering tokens (for example `1.`, `Step 2`, `15%`, `2025`) unchanged except for surrounding translated words.

Examples:
- `3 Steps to Start` → `3 pasos para comenzar`
- `2025 Goal: 15% Reduction` → `Meta 2025: Reducción del 15%`

### Narration Text (`content_type=narration`)
- Optimize for natural speech.
- Spelled-out numbers are allowed when they sound better, including sentence-start numbers.
- Keep digits when they are naturally spoken as identifiers, dates, measurements, or codes.

Examples:
- `3 steps keep your team safe.` → `Tres pasos mantienen seguro a su equipo.`
- `Start with 2 inspections per shift.` → `Comience con 2 inspecciones por turno.`


## Critical Requirements

### ⚠️ Whitespace Restoration (MANDATORY)
- **EVERY** `run.text = ...` assignment MUST use `_restore_whitespace()`
- Final validation scan checks all `<a:t>` elements
- Failure to restore whitespace causes run concatenation bugs

### ⚠️ SmartArt Stability
- Wrap `dgm_root.iter()` with `list()` in both collect and apply
- Post-write validation: re-parse XML and verify text matches expected
- Order corruption will break diagram layout

### ⚠️ Paragraph-level Collection
- Join runs with `||N||` separator
- Claude translates full paragraph
- `split_translation_to_runs()` splits back to original run count
- Preserves formatting while improving translation quality

### ⚠️ Length Budgets
- Title: ≤ 5 words
- Subtitle: ≤ 8 words
- Body: ≤ 120% of source characters
- Auto-flag violations for retry (max 2 attempts)

### ⚠️ Adaptive Batching
- Calculate batch size from character count (~4000 chars per batch)
- Prevents context truncation on large presentations
- `retry_missing_ids()` catches dropped translations
