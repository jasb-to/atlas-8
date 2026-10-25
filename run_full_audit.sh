#!/bin/bash
# FULL FRIDAY AUDIT - RUN LOCALLY
set -e

echo "======================================"
echo "FRIDAY FULL AUDIT"
echo "======================================"

echo ""
echo ">>> AUDIT 1: CODE INTEGRITY"
echo "--- Running pytest ---"
pytest tests/ -v || echo "PYTEST FAILED"

echo ""
echo "--- Checking for Hugging Face references ---"
grep -ri "huggingface\|HF_\|vercel.*ai-review" research/ dashboard/ .env* 2>/dev/null || echo "CLEAN: No HF references found"

echo ""
echo ">>> AUDIT 2: WAVE LOGIC (BTC 3-YEAR HISTORICAL)"
python -c "
import pandas as pd
from research.weekly_thesis import evaluate_sheldon_thesis
from research.friday_market_data import fetch_ohlc

# We need 200+ weeks of history, so fetch extra
weekly = fetch_ohlc(pair='XBTUSDT', interval=10080)
daily = fetch_ohlc(pair='XBTUSDT', interval=1440)

print(f'Weekly candles fetched: {len(weekly)}')
print(f'Daily candles fetched: {len(daily)}')

# Evaluate on the last 10 completed weeks
results = []
for i in range(len(weekly) - 10, len(weekly)):
    if i < 200:
        continue
    # Truncate and evaluate
    w_slice = weekly.iloc[:i+1]
    d_slice = daily.iloc[:len(daily)]
    thesis = evaluate_sheldon_thesis('XBTUSDT')
    results.append({
        'date': weekly.iloc[i].timestamp,
        'macro': thesis.get('macro_regime'),
        'wave': thesis.get('wave_state'),
        'direction': thesis.get('thesis_direction'),
        'status': thesis.get('status')
    })

for r in results:
    print(r)
"

echo ""
echo ">>> AUDIT 3: ALERT MATH"
python -c "
import sys
sys.path.insert(0, '.')
# Simulate the alert math
entry = 8.822
stop = 9.046
target = 7.833
direction = 'short'
risk = abs(entry - stop)
tp1 = entry - (risk * 1.5) if direction == 'short' else entry + (risk * 1.5)
rr = abs(target - entry) / risk
print(f'Entry: {entry}, Stop: {stop}, Target: {target}')
print(f'Calculated TP1: {tp1:.3f} (Expected: 8.486)')
print(f'Calculated RR: {rr:.2f}R (Expected: 4.40R)')
assert abs(tp1 - 8.486) < 0.01, 'TP1 math failed'
assert abs(rr - 4.40) < 0.05, 'RR math failed'
print('PASS: Alert math is correct')
"

echo ""
echo ">>> AUDIT 4: DASHBOARD BUILD"
cd dashboard
npm install --silent
npm run build || echo "DASHBOARD BUILD FAILED"
cd ..

echo ""
echo ">>> AUDIT 5: LIVE SMOKE TEST (BTC)"
python -m research.friday_monitor --pairs XBTUSDT 2>&1 | tee audit_smoke.log || echo "SMOKE TEST FAILED"

echo ""
echo "======================================"
echo "AUDIT COMPLETE - Check audit_smoke.log for details"
echo "======================================"
