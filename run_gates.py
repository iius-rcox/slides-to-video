#!/usr/bin/env python3
"""Run authoritative pipeline quality gates against produced artifacts.

This script validates translation payloads, notes, slide PNGs, per-slide WAV files,
and the final MP4. It emits both a machine-readable JSON report and a readable
terminal summary. Any CRITICAL failure exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class GateResult:
    gate: str
    check: str
    severity: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "check": self.check,
            "severity": self.severity,
            "passed": self.passed,
            "detail": self.detail,
        }


class GateRunner:
    def __init__(self) -> None:
        self.results: list[GateResult] = []

    def add(self, gate: str, check: str, severity: str, passed: bool, detail: str) -> None:
        self.results.append(GateResult(gate, check, severity, passed, detail))

    def critical_failures(self) -> list[GateResult]:
        return [r for r in self.results if r.severity == "CRITICAL" and not r.passed]

    def warnings(self) -> list[GateResult]:
        return [r for r in self.results if r.severity == "WARNING" and not r.passed]

    def to_report(self) -> dict[str, Any]:
        by_gate: dict[str, list[dict[str, Any]]] = {}
        for r in self.results:
            by_gate.setdefault(r.gate, []).append(r.as_dict())

        return {
            "ok": len(self.critical_failures()) == 0,
            "summary": {
                "total_checks": len(self.results),
                "critical_failures": len(self.critical_failures()),
                "warnings": len(self.warnings()),
            },
            "gates": by_gate,
        }


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def ffprobe_json(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-print_format",
        "json",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr.strip()}")
    return json.loads(res.stdout)


def read_wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wavf:
        frames = wavf.getnframes()
        rate = wavf.getframerate()
        if rate <= 0:
            return 0.0
        return frames / float(rate)


def check_translation_payload(runner: GateRunner, payload_path: Path | None) -> None:
    gate = "post_translation"
    if payload_path is None:
        runner.add(gate, "translation_payload_present", "WARNING", False, "No translated payload provided; translation gate skipped.")
        return

    if not payload_path.exists():
        runner.add(gate, "translation_payload_exists", "CRITICAL", False, f"Missing payload: {payload_path}")
        return

    try:
        payload = load_json(payload_path)
    except Exception as exc:
        runner.add(gate, "translation_payload_valid_json", "CRITICAL", False, f"Invalid JSON: {exc}")
        return

    runner.add(gate, "translation_payload_valid_json", "CRITICAL", True, "Valid JSON payload.")

    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        runner.add(gate, "translation_payload_schema", "CRITICAL", False, "Expected object with an 'items' list.")
        return

    missing_translation = 0
    role_budget_failures = 0
    for item in items:
        src = str(item.get("source", ""))
        trans = str(item.get("translation", ""))
        role = str(item.get("role", "body"))

        if src.strip() and not trans.strip():
            missing_translation += 1

        trans_words = len(trans.split())
        if role == "title" and trans_words > 5:
            role_budget_failures += 1
        if role == "subtitle" and trans_words > 8:
            role_budget_failures += 1

    runner.add(
        gate,
        "no_empty_translations",
        "CRITICAL",
        missing_translation == 0,
        "All non-empty source items have translations." if missing_translation == 0 else f"{missing_translation} translated items were empty.",
    )
    runner.add(
        gate,
        "title_subtitle_length_budget",
        "CRITICAL",
        role_budget_failures == 0,
        "Title/subtitle budgets respected." if role_budget_failures == 0 else f"{role_budget_failures} title/subtitle items exceeded budget.",
    )


def check_notes(runner: GateRunner, notes_path: Path) -> list[dict[str, Any]]:
    gate = "post_extraction"
    if not notes_path.exists():
        runner.add(gate, "notes_exists", "CRITICAL", False, f"Missing notes JSON: {notes_path}")
        return []

    try:
        notes = load_json(notes_path)
    except Exception as exc:
        runner.add(gate, "notes_valid_json", "CRITICAL", False, f"Invalid JSON: {exc}")
        return []

    if not isinstance(notes, list):
        runner.add(gate, "notes_valid_json_array", "CRITICAL", False, "notes.json is not a JSON array.")
        return []

    runner.add(gate, "notes_valid_json_array", "CRITICAL", True, "notes.json is a valid JSON array.")

    slide_numbers: list[int] = []
    schema_ok = True
    for i, item in enumerate(notes):
        if not isinstance(item, dict) or not isinstance(item.get("slide"), int) or "text" not in item:
            schema_ok = False
            break
        slide_numbers.append(item["slide"])

    runner.add(
        gate,
        "notes_schema",
        "CRITICAL",
        schema_ok,
        "All entries contain integer 'slide' and 'text'." if schema_ok else "One or more entries do not match expected schema.",
    )

    sequential = slide_numbers == list(range(1, len(slide_numbers) + 1))
    runner.add(
        gate,
        "slide_index_sequence",
        "CRITICAL",
        sequential,
        "Slide indices are sequential starting at 1." if sequential else "Slide indices are not sequential from 1..N.",
    )

    non_empty_notes = sum(1 for item in notes if str(item.get("text", "")).strip())
    runner.add(
        gate,
        "at_least_one_note",
        "WARNING",
        non_empty_notes > 0,
        f"{non_empty_notes} slides contain notes." if non_empty_notes > 0 else "No non-empty notes found.",
    )

    return notes


def check_slides(runner: GateRunner, slides_dir: Path, slide_count: int) -> None:
    gate = "post_export"
    if not slides_dir.exists() or not slides_dir.is_dir():
        runner.add(gate, "slides_dir_exists", "CRITICAL", False, f"Missing slides dir: {slides_dir}")
        return

    pngs = sorted(slides_dir.glob("slide_*.png"))
    runner.add(
        gate,
        "png_count_matches_slides",
        "CRITICAL",
        len(pngs) == slide_count,
        f"Found {len(pngs)} PNG files for {slide_count} slides.",
    )

    empty_files = [p.name for p in pngs if p.stat().st_size <= 0]
    runner.add(
        gate,
        "png_files_nonempty",
        "CRITICAL",
        len(empty_files) == 0,
        "All slide PNG files are non-empty." if not empty_files else f"Empty PNG files: {empty_files}",
    )


def check_tts_audio(runner: GateRunner, audio_dir: Path, notes: list[dict[str, Any]]) -> None:
    gate = "post_tts"
    if not audio_dir.exists() or not audio_dir.is_dir():
        runner.add(gate, "audio_dir_exists", "CRITICAL", False, f"Missing audio dir: {audio_dir}")
        return

    missing_audio: list[int] = []
    invalid_header: list[str] = []
    short_files: list[str] = []
    long_files: list[str] = []

    for item in notes:
        slide = int(item["slide"])
        text = str(item.get("text", "")).strip()
        wav_path = audio_dir / f"slide_{slide:02d}.wav"

        if not text:
            continue

        if not wav_path.exists():
            missing_audio.append(slide)
            continue

        try:
            with open(wav_path, "rb") as f:
                header = f.read(12)
            is_wav = header.startswith(b"RIFF") and header[8:12] == b"WAVE"
            if not is_wav:
                invalid_header.append(wav_path.name)
                continue

            dur = read_wav_duration(wav_path)
            if dur < 0.5:
                short_files.append(f"{wav_path.name} ({dur:.2f}s)")
            if dur > 120.0:
                long_files.append(f"{wav_path.name} ({dur:.2f}s)")
        except Exception:
            invalid_header.append(wav_path.name)

    runner.add(
        gate,
        "wav_exists_for_nonempty_notes",
        "CRITICAL",
        len(missing_audio) == 0,
        "Audio exists for every narrated slide." if not missing_audio else f"Missing WAV for slides: {missing_audio}",
    )
    runner.add(
        gate,
        "wav_header_valid",
        "CRITICAL",
        len(invalid_header) == 0,
        "All WAV files have RIFF/WAVE headers." if not invalid_header else f"Invalid WAV files: {invalid_header}",
    )
    runner.add(
        gate,
        "wav_duration_min",
        "WARNING",
        len(short_files) == 0,
        "All WAV files are at least 0.5s." if not short_files else f"Suspiciously short audio: {short_files}",
    )
    runner.add(
        gate,
        "wav_duration_max",
        "WARNING",
        len(long_files) == 0,
        "All WAV files are under 120s." if not long_files else f"Suspiciously long audio: {long_files}",
    )


def check_final_video(runner: GateRunner, video_path: Path) -> None:
    gate = "post_assembly"
    exists = video_path.exists()
    runner.add(gate, "mp4_exists", "CRITICAL", exists, f"Output exists: {video_path}" if exists else f"Missing MP4: {video_path}")
    if not exists:
        return

    size_ok = video_path.stat().st_size > 0
    runner.add(gate, "mp4_nonempty", "CRITICAL", size_ok, "MP4 file size is > 0 bytes." if size_ok else "MP4 file is empty.")
    if not size_ok:
        return

    try:
        probe = ffprobe_json(video_path)
    except Exception as exc:
        runner.add(gate, "ffprobe_readable", "CRITICAL", False, str(exc))
        return

    streams = probe.get("streams", [])
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    runner.add(gate, "has_video_stream", "CRITICAL", has_video, "Video stream found." if has_video else "No video stream in MP4.")
    runner.add(gate, "has_audio_stream", "CRITICAL", has_audio, "Audio stream found." if has_audio else "No audio stream in MP4.")


def print_summary(report: dict[str, Any]) -> None:
    print("\n=== Pipeline Gate Summary ===")
    for gate, checks in report["gates"].items():
        critical_failed = sum(1 for c in checks if c["severity"] == "CRITICAL" and not c["passed"])
        warning_failed = sum(1 for c in checks if c["severity"] == "WARNING" and not c["passed"])
        status = "PASSED" if critical_failed == 0 else "FAILED"
        print(f"- {gate}: {status} (critical_failures={critical_failed}, warnings={warning_failed})")
        for check in checks:
            if check["passed"]:
                continue
            sev = check["severity"]
            print(f"    {sev}: {check['check']} -> {check['detail']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run authoritative quality gates on generated artifacts.")
    parser.add_argument("--notes", required=True, type=Path, help="Path to notes.json or notes_refined.json")
    parser.add_argument("--slides-dir", required=True, type=Path, help="Directory with slide_XX.png files")
    parser.add_argument("--audio-dir", required=True, type=Path, help="Directory with slide_XX.wav files")
    parser.add_argument("--video", required=True, type=Path, help="Path to final MP4")
    parser.add_argument("--translated-payload", type=Path, default=None, help="Optional translation payload JSON")
    parser.add_argument("--report-json", type=Path, default=Path("gate_report.json"), help="Where to write JSON report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = GateRunner()

    check_translation_payload(runner, args.translated_payload)
    notes = check_notes(runner, args.notes)
    if notes:
        check_slides(runner, args.slides_dir, len(notes))
        check_tts_audio(runner, args.audio_dir, notes)
    else:
        runner.add("post_export", "skipped_due_to_notes_failure", "CRITICAL", False, "Cannot verify slides/audio without valid notes.")
        runner.add("post_tts", "skipped_due_to_notes_failure", "CRITICAL", False, "Cannot verify slides/audio without valid notes.")

    check_final_video(runner, args.video)

    report = runner.to_report()
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    with open(args.report_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print_summary(report)
    print(f"\nWrote gate report JSON: {args.report_json}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
