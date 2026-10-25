"""Generate and replay a frozen OHLC fixture for main-vs-PR regression checks.

The fetch command runs once on the candidate branch and saves raw candles. The
replay command is then run from each checked-out repository with PYTHONPATH
pointing at that checkout, so each version's causal_replay consumes identical
CSV bytes. Outputs are canonical JSONL for a byte-for-byte comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd


def fetch_fixture(args: argparse.Namespace) -> None:
    # Fetching is done from the candidate checkout; replay imports are instead
    # resolved exclusively from the repository selected by PYTHONPATH.
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from audits._historical_data import fetch_history

    frame = fetch_history(args.pair, "4H", args.start, args.end)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output, index=False, date_format="%Y-%m-%dT%H:%M:%S.%fZ")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"fixture={output} pair={args.pair} candles={len(frame)} sha256={digest}")


def replay_fixture(args: argparse.Namespace) -> None:
    # Do not add this script's checkout to sys.path: the workflow invokes this
    # same script from both worktrees and sets PYTHONPATH to select code version.
    from research.strategy_preview import causal_replay

    source = Path(args.input)
    raw = pd.read_csv(source, parse_dates=["timestamp"])
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    events, status, frame = causal_replay(raw)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(
        f"replay={output} candles={len(frame)} events={len(events)} "
        f"sha256={digest} latest_state={status.get('state', 'unknown')}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="Fetch the fixture exactly once")
    fetch.add_argument("--pair", default="AVAXUSDT")
    fetch.add_argument("--start", default="2026-04-01")
    fetch.add_argument("--end", default="2026-10-06")
    fetch.add_argument("--output", default="data/raw_test.csv")
    fetch.set_defaults(func=fetch_fixture)
    replay = commands.add_parser("replay", help="Replay the frozen fixture with the checked-out code")
    replay.add_argument("--input", default="data/raw_test.csv")
    replay.add_argument("--output", required=True)
    replay.set_defaults(func=replay_fixture)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
