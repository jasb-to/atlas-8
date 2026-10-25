"""Standalone byte-for-byte comparison of baseline and candidate trade-replay JSONL."""
from __future__ import annotations

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Compare canonical trade_replay.jsonl outputs exported from main and PR."
    )
    parser.add_argument("--baseline", required=True, help="JSONL generated from main with the filter disabled")
    parser.add_argument("--candidate", required=True, help="JSONL generated from the PR with weekly_thesis=None")
    args = parser.parse_args()
    baseline_path, candidate_path = Path(args.baseline), Path(args.candidate)
    for path in (baseline_path, candidate_path):
        if not path.is_file():
            raise SystemExit(f"BLOCKED: required replay artifact does not exist: {path}")
    baseline = baseline_path.read_bytes()
    candidate = candidate_path.read_bytes()
    if baseline == candidate:
        print("| Comparison | Baseline bytes | Candidate bytes | Result |")
        print("|---|---:|---:|---|")
        print(f"| main vs PR, filter disabled | {len(baseline)} | {len(candidate)} | PASS — byte-for-byte identical |")
        return
    a_lines, b_lines = baseline.splitlines(), candidate.splitlines()
    first = next((i for i, pair in enumerate(zip(a_lines, b_lines), 1) if pair[0] != pair[1]), None)
    if first is None and len(a_lines) != len(b_lines):
        first = min(len(a_lines), len(b_lines)) + 1
    print("| Comparison | Baseline lines | Candidate lines | Result |")
    print("|---|---:|---:|---|")
    print(f"| main vs PR, filter disabled | {len(a_lines)} | {len(b_lines)} | FAIL — discrepancies found |")
    print(f"\nFirst differing line: {first}")
    if first and first <= len(a_lines):
        print(f"Baseline: {a_lines[first - 1][:500]!r}")
    if first and first <= len(b_lines):
        print(f"Candidate: {b_lines[first - 1][:500]!r}")
    raise SystemExit(1)


if __name__ == "__main__":
    main()
