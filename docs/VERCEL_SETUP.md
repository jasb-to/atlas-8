# Friday Vercel setup (preview-first)

Friday's dashboard lives in `dashboard/` in the `jasb-to/atlas-8` repository. Keep it separate from the CX Switch Vercel project.

## Create/link the project

1. Open the Vercel dashboard and select the intended team.
2. Add New → Project, import `jasb-to/atlas-8`, and name it `friday-research`.
3. Set **Root Directory** to `dashboard`; framework should be detected as Next.js.
4. Set the production branch to `main`. Keep Friday changes on `feature/price-touch-trendlines` until reviewed; its Git deployment should be a preview.
5. Do not connect the CX Switch project, reuse its environment variables, or enable any live-trading integration.

## Environment variables

Add these under Project → Settings → Environment Variables. Apply to **Preview** and **Development** first; add **Production** only when intentionally releasing Friday.

| Variable | Required? | Value |
| --- | --- | --- |
| `UPSTASH_REDIS_REST_URL` | Yes for persistence/dashboard | Upstash database REST URL |
| `UPSTASH_REDIS_REST_TOKEN` | Yes for persistence/dashboard | Upstash REST token; mark encrypted/sensitive |
| `HUGGINGFACE_API_KEY` | Optional | Hugging Face token for advisory-only review |
| `TELEGRAM_BOT_TOKEN` | Optional | Telegram bot token for research alerts |
| `TELEGRAM_CHAT_ID` | Optional | Destination chat ID for research alerts |

Never use `NEXT_PUBLIC_` on secrets. Never paste tokens into chat or commit them to Git. Friday must remain research-only; do not configure exchange API keys.

The GitHub Actions workflow separately needs repository Actions secrets `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` to run the scheduled monitor. If Telegram alerts are wanted, add `FRIDAY_TELEGRAM_BOT_TOKEN` and `FRIDAY_TELEGRAM_CHAT_ID` as GitHub secrets (workflow maps them to `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`). Add `HUGGINGFACE_API_KEY` as a GitHub secret only after rotating the exposed token. Do not configure paid inference or buy credits if zero spend is required.

## Verify the preview

1. Wait for the feature-branch preview build to finish.
2. Open `/api/friday`; with valid Upstash credentials it should return `connected: true`. Without credentials it should clearly return `not_configured`, not leak secrets.
3. Open the dashboard page and verify recent snapshots, up to 50 setup events, cron runs, and alert states.
4. Trigger the GitHub Actions workflow manually after adding Upstash GitHub secrets. Confirm the run and `cron_run.json` report successful persistence.
5. Only promote/release after the preview is verified. This project does not place exchange orders.

## Current limitation

Creating the Vercel project through the connected Vercel account was denied with HTTP 403 (permission denied). Create/link the project in the Vercel dashboard using an account/team with project-creation permission. This repository contains the project settings checklist; no CX Switch project was changed.
