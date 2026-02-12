# Translation QA Check Patterns

Post-translation quality assurance checks run automatically after `apply_translations()`. Claude executes these checks in-context and auto-retranslates ERROR items (max 2 attempts). WARNING items are logged and evaluated with `content_type`-specific thresholds.

## Content Types

Every source item should include a `content_type` so QA can apply the correct translation policy:

- `slide_text` → on-slide text (titles, subtitles, body, labels)
- `narration` → speaker notes / voiceover script

If `content_type` is missing, default to `slide_text`.

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
        content_type = item.get("content_type", "slide_text")
        for term in never_translate_list:
            if term in orig and term not in trans:
                errors.append({
                    "check": "never_translate",
                    "severity": "ERROR",
                    "id": item["id"],
                    "location": item["location"],
                    "content_type": content_type,
                    "detail": f"Term '{term}' was altered. Original: '{orig}' → Translated: '{trans}'"
                })
    return errors
```

### 2. Slide Text Number Digit Preservation (ERROR)

For `slide_text`, preserve digit-based numbers exactly as numbers (integers, decimals, percentages). Locale punctuation differences are acceptable (`1,000` vs `1.000`), but converting digits to words fails this check.

```python
import re

DIGIT_NUM_PATTERN = re.compile(r'\d+(?:[.,]\d+)?%?')


def normalize_digit_token(token):
    return token.replace(",", "").replace(".", "")


def check_slide_number_digit_preservation(original_texts, translated_texts):
    """ERROR if slide_text numbers are not preserved as digits."""
    errors = []
    for item in original_texts:
        if item.get("content_type", "slide_text") != "slide_text":
            continue

        orig_nums = {normalize_digit_token(n) for n in DIGIT_NUM_PATTERN.findall(item["text"])}
        trans_nums = {normalize_digit_token(n) for n in DIGIT_NUM_PATTERN.findall(translated_texts.get(item["id"], ""))}
        missing = orig_nums - trans_nums

        if missing:
            errors.append({
                "check": "slide_number_digit_preservation",
                "severity": "ERROR",
                "id": item["id"],
                "location": item["location"],
                "content_type": "slide_text",
                "detail": f"Missing digit tokens in slide text: {sorted(missing)}"
            })
    return errors
```

### 3. Narration Number Safety (WARNING)

For `narration`, allow number-word equivalents (e.g., `20` ↔ `veinte`) while still flagging likely numeric meaning loss.

If your narration policy allows spelling out numbers, use equivalence matching. If not, skip this check and rely on strict digit preservation.

```python
import re

# Minimal Spanish support for 0-100 and common ordinals used in narration.
ES_NUMBER_EQUIVALENTS = {
    0: {"0", "cero"},
    1: {"1", "uno", "una", "primer", "primero", "primera"},
    2: {"2", "dos", "segundo", "segunda"},
    3: {"3", "tres", "tercer", "tercero", "tercera"},
    4: {"4", "cuatro", "cuarto", "cuarta"},
    5: {"5", "cinco", "quinto", "quinta"},
    6: {"6", "seis", "sexto", "sexta"},
    7: {"7", "siete", "séptimo", "séptima"},
    8: {"8", "ocho", "octavo", "octava"},
    9: {"9", "nueve", "noveno", "novena"},
    10: {"10", "diez", "décimo", "décima"},
    11: {"11", "once"},
    12: {"12", "doce"},
    13: {"13", "trece"},
    14: {"14", "catorce"},
    15: {"15", "quince"},
    16: {"16", "dieciséis"},
    17: {"17", "diecisiete"},
    18: {"18", "dieciocho"},
    19: {"19", "diecinueve"},
    20: {"20", "veinte"},
    21: {"21", "veintiuno", "veintiuna"},
    22: {"22", "veintidós"},
    23: {"23", "veintitrés"},
    24: {"24", "veinticuatro"},
    25: {"25", "veinticinco"},
    26: {"26", "veintiséis"},
    27: {"27", "veintisiete"},
    28: {"28", "veintiocho"},
    29: {"29", "veintinueve"},
    30: {"30", "treinta"},
    40: {"40", "cuarenta"},
    50: {"50", "cincuenta"},
    60: {"60", "sesenta"},
    70: {"70", "setenta"},
    80: {"80", "ochenta"},
    90: {"90", "noventa"},
    100: {"100", "cien", "ciento"},
}

for tens in (30, 40, 50, 60, 70, 80, 90):
    for ones in range(1, 10):
        value = tens + ones
        tens_word = next(w for w in ES_NUMBER_EQUIVALENTS[tens] if w.isalpha())
        ones_word = next(w for w in ES_NUMBER_EQUIVALENTS[ones] if w.isalpha())
        ES_NUMBER_EQUIVALENTS[value] = {str(value), f"{tens_word} y {ones_word}"}


def extract_numbers_and_words(text):
    digits = {int(n) for n in re.findall(r'\b\d{1,3}\b', text)}
    lowered = text.lower()
    word_hits = set()
    for value, forms in ES_NUMBER_EQUIVALENTS.items():
        for form in forms:
            if re.search(rf'\b{re.escape(form)}\b', lowered):
                word_hits.add(value)
                break
    return digits | word_hits


def check_narration_number_safety(original_texts, translated_texts, allow_number_words=True):
    """WARNING if narration appears to lose number meaning."""
    warnings = []
    if not allow_number_words:
        return warnings

    for item in original_texts:
        if item.get("content_type") != "narration":
            continue

        orig_number_values = extract_numbers_and_words(item["text"])
        trans_number_values = extract_numbers_and_words(translated_texts.get(item["id"], ""))
        missing_values = orig_number_values - trans_number_values

        if missing_values:
            warnings.append({
                "check": "narration_number_safety",
                "severity": "WARNING",
                "id": item["id"],
                "location": item["location"],
                "content_type": "narration",
                "detail": f"Narration may have lost numeric meaning: {sorted(missing_values)}"
            })
    return warnings
```

### 4. Bullet Structure Preservation (WARNING)

If the source text contains bullet characters or numbered list markers, the translation must contain the same count.

```python
def check_bullet_structure(original_texts, translated_texts):
    """WARNING if bullet/list structure changed."""
    bullet_pattern = re.compile(r'^[\s]*[•\-\*\d+\.\)]\s', re.MULTILINE)
    warnings = []
    for item in original_texts:
        orig_bullets = len(bullet_pattern.findall(item["text"]))
        trans_bullets = len(bullet_pattern.findall(translated_texts.get(item["id"], "")))
        if orig_bullets > 0 and orig_bullets != trans_bullets:
            warnings.append({
                "check": "bullet_structure",
                "severity": "WARNING",
                "id": item["id"],
                "location": item["location"],
                "content_type": item.get("content_type", "slide_text"),
                "detail": f"Bullet count changed: {orig_bullets} → {trans_bullets}"
            })
    return warnings
```

### 4b. PPTX-Native Paragraph/List Structure Preservation (WARNING)

For higher accuracy, use **PPTX paragraph metadata** instead of glyph regexes. Compare source and translated PPTX files at the paragraph level for each text frame — checking paragraph count, bullet on/off state (`<a:buNone>` vs inherited/default bullet), and indentation/list level.

```python
from collections import defaultdict


def _iter_text_frame_paragraphs(prs):
    """Yield paragraph metadata from slide text frames and table cells."""
    for slide_idx, slide in enumerate(prs.slides, start=1):
        for shape_idx, shape in enumerate(slide.shapes):
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                tf = shape.text_frame
                for para_idx, para in enumerate(tf.paragraphs):
                    yield {
                        "frame_key": f"slide {slide_idx}, shape {shape_idx}",
                        "paragraph_index": para_idx,
                        "level": para.level or 0,
                        "bullet_enabled": not _paragraph_has_bu_none(para),
                    }
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
                    "detail": f"Bullet enabled changed: {s['bullet_enabled']} → {t['bullet_enabled']}",
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

Test fixtures for paragraph structure checks are in `fixtures/translation_qa/`.

### 5. Title Length Budget (ERROR)

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
                "content_type": item.get("content_type", "slide_text"),
                "detail": f"Title has {trans_words} words (max 5): '{trans}'"
            })
        elif role == "subtitle" and trans_words > 8:
            errors.append({
                "check": "subtitle_length",
                "severity": "ERROR",
                "id": item["id"],
                "location": item["location"],
                "content_type": item.get("content_type", "slide_text"),
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
                    "content_type": item.get("content_type", "slide_text"),
                    "detail": f"Body text {ratio:.0%} of original ({trans_words} vs {orig_words} words)"
                })
    return errors
```

### 6. Glossary Compliance (WARNING)

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
                    "content_type": item.get("content_type", "slide_text"),
                    "detail": f"Expected '{es_term}' for '{en_term}', not found in translation"
                })
    return warnings
```

### 7. Empty Translation Detection (ERROR)

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
                    "content_type": item.get("content_type", "slide_text"),
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
QA_THRESHOLDS = {
    "slide_text": {"max_errors": 0, "max_warnings": 2},
    "narration": {"max_errors": 0, "max_warnings": 5},
}


def run_translation_qa(original_texts, translated_texts, glossary, never_translate):
    """Run all QA checks and return categorized + thresholded results."""
    results = []
    results.extend(check_never_translate(original_texts, translated_texts, never_translate))
    results.extend(check_slide_number_digit_preservation(original_texts, translated_texts))
    results.extend(check_narration_number_safety(original_texts, translated_texts, allow_number_words=True))
    results.extend(check_bullet_structure(original_texts, translated_texts))
    results.extend(check_length_budgets(original_texts, translated_texts))
    results.extend(check_glossary_compliance(original_texts, translated_texts, glossary))
    results.extend(check_empty_translations(original_texts, translated_texts))

    errors = [r for r in results if r["severity"] == "ERROR"]
    warnings = [r for r in results if r["severity"] == "WARNING"]

    by_type = {"slide_text": {"errors": 0, "warnings": 0}, "narration": {"errors": 0, "warnings": 0}}
    for item in results:
        ct = item.get("content_type", "slide_text")
        if ct not in by_type:
            by_type[ct] = {"errors": 0, "warnings": 0}
        key = "errors" if item["severity"] == "ERROR" else "warnings"
        by_type[ct][key] += 1

    threshold_failures = []
    for ct, counts in by_type.items():
        threshold = QA_THRESHOLDS.get(ct, QA_THRESHOLDS["slide_text"])
        if counts["errors"] > threshold["max_errors"] or counts["warnings"] > threshold["max_warnings"]:
            threshold_failures.append({
                "content_type": ct,
                "counts": counts,
                "threshold": threshold,
            })

    print(f"  QA: {len(errors)} errors, {len(warnings)} warnings")
    for e in errors:
        print(f"    ERROR [{e['check']}] ({e['content_type']}) {e['location']}: {e['detail']}")
    for w in warnings:
        print(f"    WARN  [{w['check']}] ({w['content_type']}) {w['location']}: {w['detail']}")

    return {
        "errors": errors,
        "warnings": warnings,
        "counts_by_content_type": by_type,
        "threshold_failures": threshold_failures,
    }
```

## Test Fixtures / Examples

Use these fixtures to validate numeric behavior for slide text vs narration.

```python
def test_narration_allows_number_words_equivalence():
    original = [{
        "id": "n1",
        "location": "slide 2 notes",
        "content_type": "narration",
        "text": "Complete 20 tasks before launch."
    }]
    translated = {
        "n1": "Completa veinte tareas antes del lanzamiento."
    }

    warnings = check_narration_number_safety(original, translated, allow_number_words=True)
    assert warnings == []  # accepted: 20 <-> veinte


def test_slide_text_requires_digits():
    original = [{
        "id": "s1",
        "location": "slide 2 title",
        "content_type": "slide_text",
        "text": "20 Tasks Remaining"
    }]
    translated = {
        "s1": "Veinte tareas restantes"
    }

    errors = check_slide_number_digit_preservation(original, translated)
    assert len(errors) == 1  # strict digit preservation for slide text
```
