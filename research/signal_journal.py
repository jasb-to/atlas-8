"""Append-only research journal for Friday's causal strategy replay.

Research only. This module never places orders and never calls an inference API.
It records both candidate and no-trade decisions so Friday can be audited before
any Hugging Face adapter is wired in.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1


def _json_safe(value: Any) -> Any:
    """Normalize common scientific-Python values and reject non-finite floats."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    # numpy scalars expose item(); keep this optional and dependency-free.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item())
        except (TypeError, ValueError):
            pass
    return str(value)


def build_decision_record(
    *,
    pair: str,
    timeframe: str,
    candle_timestamp: int,
    strategy_version: str,
    status: str,
    direction: str | None = None,
    entry: float | None = None,
    stop: float | None = None,
    targets: list[Mapping[str, Any]] | None = None,
    evidence: Mapping[str, Any] | None = None,
    blockers: list[str] | None = None,
    model: Mapping[str, Any] | None = None,
    outcome: Mapping[str, Any] | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Create one reproducible decision record from information known at signal time.

    candle_timestamp is the candle being evaluated, not a later outcome candle.
    Outcome data is kept in a separate field and must be attached only after
    signal generation to prevent it leaking into the decision itself.
    """
    if not pair.strip():
        raise ValueError("pair must not be empty")
    if not timeframe.strip():
        raise ValueError("timeframe must not be empty")
    if candle_timestamp <= 0:
        raise ValueError("candle_timestamp must be a positive Unix timestamp")
    if not strategy_version.strip():
        raise ValueError("strategy_version must not be empty")
    if not status.strip():
        raise ValueError("status must not be empty")
    if direction not in (None, "LONG", "SHORT", "NEUTRAL"):
        raise ValueError("direction must be LONG, SHORT, NEUTRAL, or None")

    return _json_safe({
        "schema_version": SCHEMA_VERSION,
        "recorded_at": recorded_at or datetime.now(timezone.utc).isoformat(),
        "pair": pair,
        "timeframe": timeframe,
        "candle_timestamp": int(candle_timestamp),
        "strategy_version": strategy_version,
        "status": status,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "targets": targets or [],
        "evidence": evidence or {},
        "blockers": blockers or [],
        "model": model or {
            "status": "not_configured",
            "provider": None,
            "model_id": None,
            "response": None,
            "error": None,
        },
        "outcome": outcome,
    })


def append_decision(path: str | Path, record: Mapping[str, Any]) -> None:
    """Append exactly one JSON object as a JSONL row, creating parent dirs."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(_json_safe(dict(record)), sort_keys=True, separators=(",", ":")))
        stream.write("\n")


def read_decisions(path: str | Path) -> list[dict[str, Any]]:
    """Read a journal; malformed rows fail loudly rather than being silently skipped."""
    source = Path(path)
    if not source.exists():
        return []
    records = []
    with source.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"journal row {line_number} is not an object")
            records.append(value)
    return records
