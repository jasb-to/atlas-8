# CX Switch strategy and Hugging Face integration audit

Date: 2026-10-21

## Confirmed from the inspected production source

- The production repository is `jasb-to/cxswitch3`; its current `main` commit inspected for this audit is `21c7995611751317bb82b58bfe511bbcc4a161da`.
- `lib/strategy.ts` contains the deterministic signal engine, including ENTRY_1 / ENTRY_2 evaluation, regime alignment, exhaustion checks, stop sizing, pivot-based targets, and trade management.
- `lib/jarvis.ts` is explicitly a deterministic observation/review layer. Its current `runJarvis` and `narratePairState` paths use local rule logic and store a snapshot; the inspected source does not make a Hugging Face inference request.
- The Vercel production environment has a secret variable named `HUGGINGFACE_API_KEY`. The variable name alone does not establish that application code calls Hugging Face, nor does it identify a model, task, or endpoint.
- Repository-wide code searches for `HUGGINGFACE_API_KEY`, `huggingface`, `InferenceClient`, and `HF_TOKEN` did not locate a source call path in the inspected repositories.

## Consequence for Friday

Do not pretend that Friday is already using the production Hugging Face model. First identify the real inference call path (if it exists outside the inspected repository/source), including provider endpoint, model ID, prompt/input contract, output schema, timeout, and failure behavior. Never copy a secret into the repository or logs.

Friday's current work remains research-only and isolated on `feature/price-touch-trendlines`. Its decision journal can explicitly represent the model as `not_configured` or `skipped`, so model output is never fabricated and model failure never silently turns into a trade.

## Integration acceptance criteria

1. Capture the exact candle timestamp and the causal strategy evidence supplied to the model.
2. Persist provider/model identifiers, request status, parsed response, raw response where safe, latency/error class, and final rule decision.
3. Record no-trade decisions as well as candidates.
4. Keep model interpretation advisory until walk-forward tests demonstrate whether it improves early-entry quality.
5. Replay with no future-candle leakage; evaluate subsequent +10%/+20% movement only after the decision record is frozen.
6. Keep all changes in Friday's research branch. Do not change or deploy CX Switch production as part of this research integration.
