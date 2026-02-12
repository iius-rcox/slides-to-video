#!/usr/bin/env python3
"""Validate glossary consistency rules.

Fails if a term appears in both `glossary` and `never_translate` unless the glossary
mapping is identity (e.g., "LOTO" -> "LOTO").
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


DEFAULT_GLOSSARY_PATH = Path("glossary_en_es.json")


def load_glossary(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise SystemExit(f"ERROR: glossary file not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: invalid JSON in {path}: {exc}")

    if not isinstance(data.get("glossary"), dict):
        raise SystemExit("ERROR: 'glossary' must be an object/map")
    if not isinstance(data.get("never_translate"), list):
        raise SystemExit("ERROR: 'never_translate' must be an array")

    return data


def find_conflicts(glossary: dict[str, str], never_translate: list[str]) -> list[tuple[str, str]]:
    never_set = set(never_translate)
    conflicts: list[tuple[str, str]] = []

    for key, value in glossary.items():
        if key in never_set and key != value:
            conflicts.append((key, value))

    return sorted(conflicts, key=lambda item: item[0].lower())


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_GLOSSARY_PATH
    data = load_glossary(path)

    glossary = data["glossary"]
    never_translate = data["never_translate"]
    conflicts = find_conflicts(glossary, never_translate)

    if conflicts:
        print("ERROR: glossary/never_translate conflicts found:")
        for key, value in conflicts:
            print(f"  - '{key}' is in never_translate but maps to '{value}'")
        print("Fix by removing the key from never_translate or using an identity mapping (X -> X).")
        return 1

    print(f"OK: glossary validation passed for {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
