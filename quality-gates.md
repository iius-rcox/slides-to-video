# Pipeline Quality Gates

Quality gates are validation checkpoints at each stage of the PPTX-to-video pipeline. CRITICAL failures stop the pipeline. WARNING issues are logged and the pipeline continues.

## Gate 1: Post-Translation

**Runs after:** `apply_translations()` and QA checks (see `translation-qa.md`)

| Check | Severity | Criteria |
|-------|----------|----------|
| Never-translate terms preserved | CRITICAL | All `never_translate` terms unchanged |
| No empty translations for non-empty source | CRITICAL | Every non-empty source has non-empty translation |
| Title length within budget | CRITICAL | Titles ≤ 5 words, subtitles ≤ 8 words |
| Body length within budget | WARNING | Body text ≤ 120% of source word count |
| Glossary terms used consistently | WARNING | Canonical translations used where applicable |
| Numbers preserved | WARNING | All numbers from source appear in translation |
| Paragraph/list structure preserved | WARNING | Per text frame: paragraph count, bullet state, and list levels match source |
| Whitespace restored on all runs | CRITICAL | `_restore_whitespace()` applied to every write-back |
| SmartArt text order preserved | CRITICAL | Post-write re-parse matches expected text sequence |

**On CRITICAL failure:** Auto-retranslate failed items (max 2 attempts). If still failing, stop pipeline and report.

## Gate 2: Post-Extraction

**Runs after:** `extract_notes.py`

| Check | Severity | Criteria |
|-------|----------|----------|
| Output JSON is valid | CRITICAL | `notes.json` parses as valid JSON array |
| Slide count matches PPTX | CRITICAL | Number of entries equals number of slides |
| At least 1 slide has notes | WARNING | At least one non-empty text field |

**On CRITICAL failure:** Re-run extraction. If fails again, stop pipeline.

## Gate 3: Post-Refinement

**Runs after:** Claude refines notes (Step 2.5). See `narration-refinement.md` for validation rules.

| Check | Severity | Criteria |
|-------|----------|----------|
| Output JSON is valid | CRITICAL | `notes_refined.json` parses as valid JSON array |
| Slide count preserved | CRITICAL | Same number of entries as `notes.json` |
| Empty slides stay empty | CRITICAL | Slides empty in original are empty in refined |
| Per-slide word cap (10-55) | WARNING | Each non-empty slide has 10-55 words |
| No new steps introduced | WARNING | Step count ≤ original per slide |
| Content word overlap ≥ 30% | WARNING | Refined text maintains semantic similarity |

**On CRITICAL failure:** Fall back to original `notes.json` for the entire pipeline.
**On WARNING failure:** Fall back to original note for that specific slide only.

## Gate 4: Post-Export

**Runs after:** `export_slides.ps1`

| Check | Severity | Criteria |
|-------|----------|----------|
| PNG count matches slide count | CRITICAL | One PNG per slide in the PPTX |
| All PNGs are 1920x1080 | WARNING | Consistent dimensions for video assembly |
| PNG file sizes > 0 bytes | CRITICAL | No corrupt/empty exports |

**On CRITICAL failure:** Kill PowerPoint COM, re-run export. If fails again, try LibreOffice fallback.

## Gate 5: Post-TTS

**Runs after:** `synthesize_tts.py`

| Check | Severity | Criteria |
|-------|----------|----------|
| WAV exists for every non-empty slide | CRITICAL | Audio file produced for each slide with notes |
| WAV duration > 0.5s | WARNING | Audio isn't suspiciously short (possible TTS error) |
| WAV duration < 120s | WARNING | Audio isn't suspiciously long (possible TTS loop) |
| WAV is valid (has proper header) | CRITICAL | File starts with RIFF/WAVE header |
| No slides with notes but missing audio | CRITICAL | Every narrated slide has its audio file |

**On CRITICAL failure:** Delete the corrupt WAV and re-synthesize that slide. If fails again, stop pipeline.

## Gate 6: Post-Assembly

**Runs after:** `assemble_video.py`

| Check | Severity | Criteria |
|-------|----------|----------|
| Output MP4 exists | CRITICAL | File was created |
| MP4 file size > 0 bytes | CRITICAL | Not an empty file |
| MP4 has video stream | CRITICAL | `ffprobe` detects a video stream |
| MP4 has audio stream | CRITICAL | `ffprobe` detects an audio stream |
| Video resolution is 1920x1080 | WARNING | Matches expected dimensions |
| Duration within 10% of expected | WARNING | `sum(slide_durations)` ± 10% |
| AAC bitrate ≥ 200kbps | WARNING | Audio quality meets 256k target |

**On CRITICAL failure:** Delete output, clear `v2/` intermediates, re-run assembly. If fails again, stop pipeline.

## Implementation Pattern

Claude checks quality gates inline during pipeline execution. The pattern for each gate:

```python
def check_gate(gate_name, checks):
    """Run quality gate checks. Returns (passed, critical_failures, warnings)."""
    critical = []
    warnings = []

    for check in checks:
        result = check["fn"]()
        if not result["passed"]:
            if check["severity"] == "CRITICAL":
                critical.append({"check": check["name"], "detail": result["detail"]})
            else:
                warnings.append({"check": check["name"], "detail": result["detail"]})

    passed = len(critical) == 0
    if not passed:
        print(f"  GATE {gate_name}: FAILED — {len(critical)} critical, {len(warnings)} warnings")
        for c in critical:
            print(f"    CRITICAL: {c['check']} — {c['detail']}")
    else:
        print(f"  GATE {gate_name}: PASSED ({len(warnings)} warnings)")

    for w in warnings:
        print(f"    WARNING: {w['check']} — {w['detail']}")

    return passed, critical, warnings
```
