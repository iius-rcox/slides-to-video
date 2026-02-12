"""Fixture-driven regression checks for paragraph/list structure QA.

Run:
    python fixtures/translation_qa/test_bullet_structure_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path


FIXTURE_PATH = Path(__file__).with_name("bullet_structure_fixtures.json")


def check_paragraph_structure(source: dict, translated: dict) -> list[dict]:
    """Compare source/translation frame metadata and emit warnings."""
    warnings = []
    frame_keys = sorted(set(source.keys()) | set(translated.keys()))

    for frame_key in frame_keys:
        src_paras = source.get(frame_key, [])
        trg_paras = translated.get(frame_key, [])

        if len(src_paras) != len(trg_paras):
            warnings.append(
                {
                    "check": "paragraph_count",
                    "location": frame_key,
                    "detail": f"Paragraph count changed: {len(src_paras)} → {len(trg_paras)}",
                }
            )
            continue

        for para_idx, (src_para, trg_para) in enumerate(zip(src_paras, trg_paras)):
            if src_para["bullet_enabled"] != trg_para["bullet_enabled"]:
                warnings.append(
                    {
                        "check": "bullet_state",
                        "location": f"{frame_key}, paragraph {para_idx}",
                        "detail": (
                            "Bullet enabled changed: "
                            f"{src_para['bullet_enabled']} → {trg_para['bullet_enabled']}"
                        ),
                    }
                )
            if src_para["level"] != trg_para["level"]:
                warnings.append(
                    {
                        "check": "list_level",
                        "location": f"{frame_key}, paragraph {para_idx}",
                        "detail": f"List level changed: {src_para['level']} → {trg_para['level']}",
                    }
                )

    return warnings


def main() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = fixtures["cases"]

    failures = []
    for case in cases:
        warnings = check_paragraph_structure(case["source"], case["translated"])
        warning_checks = sorted({w["check"] for w in warnings})
        expected = sorted(case["expected_warning_checks"])
        if warning_checks != expected:
            failures.append(
                {
                    "case": case["name"],
                    "expected": expected,
                    "actual": warning_checks,
                }
            )

    if failures:
        print("FAIL: fixture mismatches detected")
        for failure in failures:
            print(
                f"  - {failure['case']}: expected {failure['expected']}, "
                f"actual {failure['actual']}"
            )
        raise SystemExit(1)

    print(f"PASS: {len(cases)} bullet-structure fixture cases")


if __name__ == "__main__":
    main()
