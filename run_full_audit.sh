#!/usr/bin/env bash
# FRIDAY full audit helper. Safe to run locally; no deploys and monitor is read-only.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "======================================"
echo "FRIDAY FULL AUDIT"
echo "AI review and Telegram delivery are disabled for this run."
echo "======================================"

echo
echo ">>> AUDIT 1: PYTEST"
pytest tests/ -v

echo
echo ">>> AUDIT 2: SOURCE REFERENCES (excluding documentation)"
python - <<'PY'
from pathlib import Path
import re
patterns = [re.compile(r"(?i)huggingface"), re.compile(r"(?i)HF_"), re.compile(r"(?i)vercel.*ai-review")]
root = Path(".")
extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".yml", ".yaml", ".sh", ".toml", ".txt"}
matches = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    if path.suffix.lower() not in extensions and not path.name.startswith(".env"):
        continue
    if path.as_posix() in {".github/workflows/friday-full-audit.yml", "run_full_audit.sh"}:
        continue
    if any(part.lower() in {".git", "docs", "documentation", "node_modules", ".next", "dist", "build"} for part in path.parts):
        continue
    for line_no, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if any(pattern.search(line) for pattern in patterns):
                matches.append(f"{path}:{line_no}:{line.strip()}")
if matches:
    print("\n".join(matches))
    raise SystemExit("FAIL: forbidden provider references found in source")
print("PASS: no forbidden provider references in scanned source")
PY

echo
echo ">>> AUDIT 3: HISTORICAL WEEKLY THESIS (last 20 completed weeks)"
FRIDAY_AI_REVIEW_ENABLED=false FRIDAY_TELEGRAM_ENABLED=false python - <<'PY'
import pandas as pd
from research.weekly_thesis import evaluate_sheldon_thesis
from research.friday_market_data import fetch_ohlc

pairs = ["XBTUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT"]
for pair in pairs:
    weekly = fetch_ohlc(pair=pair, interval=10080)
    daily = fetch_ohlc(pair=pair, interval=1440)
    print(f"\n{pair}: weekly candles={len(weekly)} daily candles={len(daily)}")
    if len(weekly) < 200:
        raise RuntimeError(f"{pair}: need 200 weekly candles; got {len(weekly)}")
    results = []
    for i in range(max(199, len(weekly) - 20), len(weekly)):
        week_open = pd.Timestamp(weekly.iloc[i]["timestamp"])
        as_of = week_open + pd.Timedelta(days=7, seconds=1)
        w_slice = weekly.iloc[:i + 1].copy()
        d_slice = daily.loc[pd.to_datetime(daily["timestamp"], utc=True) < as_of].copy()
        thesis = evaluate_sheldon_thesis(
            pair, weekly_candles=w_slice, daily_candles=d_slice, as_of=as_of
        )
        results.append({
            "date": week_open.date().isoformat(),
            "macro": thesis.get("macro_regime") or "n/a",
            "wave": thesis.get("wave_state") or "n/a",
            "direction": thesis.get("direction") or "none",
            "status": thesis.get("status") or "n/a",
        })
    print("date       macro    wave       direction status")
    for row in results[-20:]:
        print("{date} {macro:8} {wave:10} {direction:9} {status}".format(**row))
    if not results:
        raise RuntimeError(f"{pair}: no historical weekly rows evaluated")
print("\nPASS: historical weekly thesis evaluated from supplied candle slices")
PY

echo
echo ">>> AUDIT 4: ALERT MATH"
python - <<'PY'
entry, stop, target, direction = 8.822, 9.046, 7.833, "short"
risk = abs(entry - stop)
tp1 = entry - risk * 1.5 if direction == "short" else entry + risk * 1.5
rr = abs(target - entry) / risk
print(f"Entry={entry:.3f} Stop={stop:.3f} Target={target:.3f} TP1={tp1:.3f} RR={rr:.2f}")
assert round(tp1, 3) == 8.486, f"TP1 mismatch: {tp1}"
assert abs(rr - 4.40) < 0.05, f"RR outside expected tolerance: {rr}"
print("PASS: alert math")
PY

echo
echo ">>> AUDIT 5: DASHBOARD BUILD"
cd dashboard
if [[ ! -f package-lock.json ]]; then
  npm install --package-lock-only --ignore-scripts
fi
npm ci
npm run build
cd "$ROOT"

echo
echo ">>> AUDIT 6: READ-ONLY BTC SMOKE TEST"
FRIDAY_AI_REVIEW_ENABLED=false FRIDAY_TELEGRAM_ENABLED=false python -m research.friday_monitor --pairs XBTUSDT --dry-run | tee audit_smoke.log

echo
echo "======================================"
echo "AUDIT COMPLETE"
echo "======================================"
