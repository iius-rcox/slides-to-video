# Translation QA Check Patterns

Post-translation quality assurance checks run automatically after `apply_translations()`. Claude executes these checks in-context and auto-retranslates ERROR items (max 2 attempts). WARNING items are logged but do not block the pipeline.

> **Authority split:**
> - **LLM QA in this file is advisory** and improves translation quality during generation.
> - **`run_gates.py` checks are authoritative** for CRITICAL pass/fail enforcement on artifacts.

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

### 3. Bullet Structure Preservation (WARNING)

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
                "detail": f"Bullet count changed: {orig_bullets} → {trans_bullets}"
            })
    return warnings
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
def run_translation_qa(original_texts, translated_texts, glossary, never_translate):
    """Run all QA checks and return categorized results."""
    results = []
    results.extend(check_never_translate(original_texts, translated_texts, never_translate))
    results.extend(check_number_preservation(original_texts, translated_texts))
    results.extend(check_bullet_structure(original_texts, translated_texts))
    results.extend(check_length_budgets(original_texts, translated_texts))
    results.extend(check_glossary_compliance(original_texts, translated_texts, glossary))
    results.extend(check_empty_translations(original_texts, translated_texts))

    errors = [r for r in results if r["severity"] == "ERROR"]
    warnings = [r for r in results if r["severity"] == "WARNING"]

    print(f"  QA: {len(errors)} errors, {len(warnings)} warnings")
    for e in errors:
        print(f"    ERROR [{e['check']}] {e['location']}: {e['detail']}")
    for w in warnings:
        print(f"    WARN  [{w['check']}] {w['location']}: {w['detail']}")

    return {"errors": errors, "warnings": warnings}
```
