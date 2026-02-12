# Translation QA Check Patterns

Post-translation quality assurance checks run automatically after `apply_translations()`. Claude executes these checks in-context and auto-retranslates ERROR items (max 2 attempts). WARNING items are logged but do not block the pipeline.

## QA Checks

### 1. Never-Translate Preservation (ERROR)

Verify that every term in the glossary's `never_translate` list appears unchanged in the translated text wherever it appeared in the source.

```python
def check_never_translate(original_texts, translated_texts, never_translate_list):
    """ERROR if a never-translate term was altered."""
    errors = []
    for item in original_texts:
        orig = item["text"]
        trans = translated_texts.get(item["id"], "")
        for term in never_translate_list:
            if term in orig and term not in trans:
                errors.append({
                    "check": "never_translate",
                    "severity": "ERROR",
                    "id": item["id"],
                    "location": item["location"],
                    "detail": f"Term '{term}' was altered. Original: '{orig}' → Translated: '{trans}'"
                })
    return errors
```

### 2. Number Preservation (WARNING)

All numbers (integers, decimals, percentages) in the source must appear in the translation. Different formatting is acceptable (e.g., "1,000" vs "1.000").

```python
import re

def check_number_preservation(original_texts, translated_texts):
    """WARNING if numbers differ between source and translation."""
    warnings = []
    num_pattern = re.compile(r'\d+(?:[.,]\d+)?%?')
    for item in original_texts:
        orig_nums = set(num_pattern.findall(item["text"]))
        trans_nums = set(num_pattern.findall(translated_texts.get(item["id"], "")))
        # Normalize: strip commas/periods for comparison
        orig_normalized = {n.replace(",", "").replace(".", "") for n in orig_nums}
        trans_normalized = {n.replace(",", "").replace(".", "") for n in trans_nums}
        missing = orig_normalized - trans_normalized
        if missing:
            warnings.append({
                "check": "number_preservation",
                "severity": "WARNING",
                "id": item["id"],
                "location": item["location"],
                "detail": f"Missing numbers: {missing}"
            })
    return warnings
```

### 3. Paragraph/List Structure Preservation (WARNING)

Bullet QA must use **PPTX paragraph metadata**, not glyph regexes in translated text. Compare source and translated files at the paragraph level for each text frame:

- paragraph count
- bullet on/off (`<a:buNone>` vs inherited/default bullet)
- indentation/list level (`paragraph.level`)

```python
from collections import defaultdict


def _iter_text_frame_paragraphs(prs):
    """Yield paragraph metadata from slide text frames and table cells."""
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape_idx, shape in enumerate(slide.shapes):
            # Standard text frame
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                tf = shape.text_frame
                for para_idx, para in enumerate(tf.paragraphs):
                    yield {
                        "frame_key": f"slide {slide_idx}, shape {shape_idx}",
                        "paragraph_index": para_idx,
                        "level": para.level or 0,
                        "bullet_enabled": not _paragraph_has_bu_none(para),
                    }

            # Table text frames
            if getattr(shape, "has_table", False) and shape.has_table:
                for row_idx, row in enumerate(shape.table.rows):
                    for col_idx, cell in enumerate(row.cells):
                        if not cell.text_frame:
                            continue
                        for para_idx, para in enumerate(cell.text_frame.paragraphs):
                            yield {
                                "frame_key": f"slide {slide_idx}, table {shape_idx}, cell {row_idx},{col_idx}",
                                "paragraph_index": para_idx,
                                "level": para.level or 0,
                                "bullet_enabled": not _paragraph_has_bu_none(para),
                            }


def _paragraph_has_bu_none(paragraph):
    """True when paragraph explicitly disables bullets (<a:buNone/>)."""
    pPr = paragraph._p.pPr
    if pPr is None:
        return False
    return pPr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}buNone") is not None


def collect_paragraph_structure(prs):
    """Group per-paragraph metadata by text-frame key."""
    grouped = defaultdict(list)
    for meta in _iter_text_frame_paragraphs(prs):
        grouped[meta["frame_key"]].append(meta)
    return grouped


def check_paragraph_structure(source_prs, translated_prs):
    """WARNING if list/paragraph structure differs between source and translation."""
    warnings = []
    src = collect_paragraph_structure(source_prs)
    trg = collect_paragraph_structure(translated_prs)

    frame_keys = sorted(set(src.keys()) | set(trg.keys()))
    for frame_key in frame_keys:
        src_paras = src.get(frame_key, [])
        trg_paras = trg.get(frame_key, [])

        if len(src_paras) != len(trg_paras):
            warnings.append({
                "check": "paragraph_count",
                "severity": "WARNING",
                "location": frame_key,
                "detail": f"Paragraph count changed: {len(src_paras)} → {len(trg_paras)}",
            })
            continue

        for para_idx, (s, t) in enumerate(zip(src_paras, trg_paras)):
            if s["bullet_enabled"] != t["bullet_enabled"]:
                warnings.append({
                    "check": "bullet_state",
                    "severity": "WARNING",
                    "location": f"{frame_key}, paragraph {para_idx}",
                    "detail": (
                        f"Bullet enabled changed: {s['bullet_enabled']} → {t['bullet_enabled']}"
                    ),
                })

            if s["level"] != t["level"]:
                warnings.append({
                    "check": "list_level",
                    "severity": "WARNING",
                    "location": f"{frame_key}, paragraph {para_idx}",
                    "detail": f"List level changed: {s['level']} → {t['level']}",
                })

    return warnings
```

#### Legacy Regex Bullet Check (ADVISORY)

Text-only bullet glyph checks are useful for debugging plaintext exports, but not reliable for PPTX QA. Keep them as non-blocking advisory output only.

```python
def check_bullet_glyphs_advisory(original_texts, translated_texts):
    """ADVISORY: Heuristic check only, never used as blocking QA."""
    bullet_pattern = re.compile(r'^[\s]*[•\-\*\d+\.\)]\s', re.MULTILINE)
    advisories = []
    for item in original_texts:
        orig_bullets = len(bullet_pattern.findall(item["text"]))
        trans_bullets = len(bullet_pattern.findall(translated_texts.get(item["id"], "")))
        if orig_bullets > 0 and orig_bullets != trans_bullets:
            advisories.append({
                "check": "bullet_glyphs_advisory",
                "severity": "ADVISORY",
                "id": item["id"],
                "location": item["location"],
                "detail": f"Glyph bullet count changed: {orig_bullets} → {trans_bullets}",
            })
    return advisories
```

### 4. Title Length Budget (ERROR)

Titles (role="title") must not exceed 5 words. Subtitles (role="subtitle") must not exceed 8 words. Body text must not exceed 120% of source word count.

```python
def check_length_budgets(original_texts, translated_texts):
    """ERROR if translated text exceeds length budget for its role."""
    errors = []
    for item in original_texts:
        trans = translated_texts.get(item["id"], "")
        role = item.get("role", "body")
        trans_words = len(trans.split())
        orig_words = len(item["text"].split())

        if role == "title" and trans_words > 5:
            errors.append({
                "check": "title_length",
                "severity": "ERROR",
                "id": item["id"],
                "location": item["location"],
                "detail": f"Title has {trans_words} words (max 5): '{trans}'"
            })
        elif role == "subtitle" and trans_words > 8:
            errors.append({
                "check": "subtitle_length",
                "severity": "ERROR",
                "id": item["id"],
                "location": item["location"],
                "detail": f"Subtitle has {trans_words} words (max 8): '{trans}'"
            })
        elif role == "body" and orig_words > 0:
            ratio = trans_words / orig_words
            if ratio > 1.2:
                errors.append({
                    "check": "body_length",
                    "severity": "ERROR",
                    "id": item["id"],
                    "location": item["location"],
                    "detail": f"Body text {ratio:.0%} of original ({trans_words} vs {orig_words} words)"
                })
    return errors
```

### 5. Glossary Compliance (WARNING)

If a glossary term's English source appears in the original text, verify the canonical Spanish translation appears in the translated text.

```python
def check_glossary_compliance(original_texts, translated_texts, glossary):
    """WARNING if glossary term not used consistently."""
    warnings = []
    for item in original_texts:
        orig_lower = item["text"].lower()
        trans_lower = translated_texts.get(item["id"], "").lower()
        for en_term, es_term in glossary.items():
            if en_term.lower() in orig_lower and es_term.lower() not in trans_lower:
                warnings.append({
                    "check": "glossary_compliance",
                    "severity": "WARNING",
                    "id": item["id"],
                    "location": item["location"],
                    "detail": f"Expected '{es_term}' for '{en_term}', not found in translation"
                })
    return warnings
```

### 6. Empty Translation Detection (ERROR)

If the source text is non-empty (after stripping whitespace), the translation must also be non-empty.

```python
def check_empty_translations(original_texts, translated_texts):
    """ERROR if a non-empty source produced an empty translation."""
    errors = []
    for item in original_texts:
        if item["text"].strip():
            trans = translated_texts.get(item["id"], "")
            if not trans.strip():
                errors.append({
                    "check": "empty_translation",
                    "severity": "ERROR",
                    "id": item["id"],
                    "location": item["location"],
                    "detail": f"Non-empty source got empty translation: '{item['text'][:60]}...'"
                })
    return errors
```

## Auto-Retranslation

When ERROR items are detected:

1. Collect all ERROR item IDs
2. Re-run translation for just those items with stricter instructions appended to the prompt:
   - "These items failed QA. Pay special attention to: [list specific failures]"
3. Re-run QA checks on the retranslated items
4. If errors persist after 2 retranslation attempts, log as CRITICAL and continue (do not block the pipeline indefinitely)

## Running QA

```python
def run_translation_qa(original_texts, translated_texts, glossary, never_translate, source_prs, translated_prs):
    """Run all QA checks and return categorized results."""
    results = []
    results.extend(check_never_translate(original_texts, translated_texts, never_translate))
    results.extend(check_number_preservation(original_texts, translated_texts))
    results.extend(check_paragraph_structure(source_prs, translated_prs))
    results.extend(check_length_budgets(original_texts, translated_texts))
    results.extend(check_glossary_compliance(original_texts, translated_texts, glossary))
    results.extend(check_empty_translations(original_texts, translated_texts))

    advisories = check_bullet_glyphs_advisory(original_texts, translated_texts)

    errors = [r for r in results if r["severity"] == "ERROR"]
    warnings = [r for r in results if r["severity"] == "WARNING"]

    print(f"  QA: {len(errors)} errors, {len(warnings)} warnings")
    for e in errors:
        print(f"    ERROR [{e['check']}] {e['location']}: {e['detail']}")
    for w in warnings:
        print(f"    WARN  [{w['check']}] {w['location']}: {w['detail']}")
    for a in advisories:
        print(f"    NOTE  [{a['check']}] {a['location']}: {a['detail']}")

    return {"errors": errors, "warnings": warnings, "advisories": advisories}
```

## Regression Fixtures

Use formatting-based fixtures (not glyph-only text fixtures) to validate list QA behavior. See:

- `fixtures/translation_qa/bullet_structure_fixtures.json`
- `fixtures/translation_qa/test_bullet_structure_fixture.py`
