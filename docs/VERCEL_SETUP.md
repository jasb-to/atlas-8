# Friday Vercel setup (temporary repository-backed trial)

Friday's dashboard lives in `dashboard/` in the `jasb-to/atlas-8` repository. Keep it separate from the CX Switch Vercel project.

## Create/link the project

1. Open the Vercel dashboard and select the intended team.
2. Add New → Project, import `jasb-to/atlas-8`, and name it `friday-research`.
3. Set **Root Directory** to `dashboard`; framework should be detected as Next.js.
4. Set the production branch to `main`.
5. Do not connect the CX Switch project, reuse its environment variables, or enable any live-trading integration.

## Temporary persistence

For the two-week proof-of-concept trial, FRIDAY uses JSON files committed under `testing/friday/data/` rather than Supabase. The scheduled GitHub Actions monitor sets `FRIDAY_STORAGE_BACKEND=repo` and commits its rolling dashboard state plus per-run research artifacts.

| Variable | Required? | Where it is used |
| --- | --- | --- |
| `FRIDAY_GITHUB_TOKEN` | Yes, for manual/cron scan dispatch | Vercel Production scan-dispatch route calls GitHub Actions; dashboard reads the public JSON file without a token |
| `FRIDAY_GITHUB_REPOSITORY` | Optional | Override for the repository containing the FRIDAY data; defaults to `jasb-to/atlas-8` |
| `FRIDAY_GITHUB_DATA_BRANCH` | Optional | Data branch; defaults to `main` |
| `FRIDAY_CRON_SECRET` | Required for cron endpoint | Vercel Production only; authenticates cron-job.org |
| `FRIDAY_TELEGRAM_BOT_TOKEN` | Required for Telegram alerts | GitHub Actions research alerts |
| `FRIDAY_TELEGRAM_CHAT_ID` | Required for Telegram alerts | GitHub Actions research alerts |
| `GROQ_API_KEY` | Required for Groq review when AI is enabled | Vercel server-side environment only; primary provider |
| `GEMINI_API_KEY` | Optional fallback | Vercel server-side environment only; fallback provider |
| `FRIDAY_GROQ_MODEL_ID` | Optional | Groq model override; defaults to the configured gpt-oss reviewer |
| `FRIDAY_GEMINI_MODEL` | Optional | Gemini model override |
| `FRIDAY_AI_REVIEW_ENABLED` | Defaults to `false` | Keep disabled unless explicitly testing research review |
| `FRIDAY_AI_REVIEW_URL` | Optional | Defaults to `/api/friday/ai-review` on the FRIDAY deployment |

The token used by `/api/friday/scan` needs permission to dispatch GitHub Actions workflows. The dashboard reader uses the public raw JSON file to avoid GitHub API rate limits on 30-second refreshes. Do not paste tokens into chat or commit them to Git. Never use `NEXT_PUBLIC_` on secrets. Friday must remain research-only; do not configure exchange API keys.

**Important:** the GitHub Actions monitor is the writer; the Vercel dashboard API is a reader. GitHub Actions commits are not instantaneous database writes, and the dashboard reflects the latest committed state. Each scan creates a commit, so this is intentionally a temporary, low-volume approach. The repository is public: only commit research data that is safe to be public. Do not store credentials, private user data, or exchange keys.

## Verify the trial

Before testing AI review, verify `FRIDAY_AI_REVIEW_ENABLED` is explicitly set to `true` only for the intended research environment, and configure `GROQ_API_KEY` plus optional `GEMINI_API_KEY` in Vercel server-side environment settings. The Python runner uses GitHub Actions OIDC to reach the proxy; it does not need provider API keys. Keep Telegram credentials in GitHub Actions secrets. Do not paste secret values into chat or commit them to the repository.

1. Run `Friday research monitor` manually from GitHub Actions.
2. Confirm the run attempted XBTUSD, ETHUSD, SOLUSD, VIRTUALUSD and HYPEUSD and produced `testing/friday/data/dashboard.json`.
3. Confirm the workflow committed files under `testing/friday/data/` and `testing/friday/runs/`.
4. Open `/api/friday` on the dashboard deployment; it should return `connected: true` and `storage_backend: github_repository_folder`.
5. Verify the radar, setup queue, run history and diagnostics show those saved records.
6. Confirm the scheduled monitor runs successfully before relying on the cadence. A successful legacy preview workflow is not enough.
7. The Stoch RSI reversal archetype now requires a structural stop, target and at least 1.5R before alert eligibility. Break/retest events are still research signals and must not be labelled fully tradeable until their alert path also consumes the risk plan and all market/persistence/deployment checks pass.

## Current limitation

Vercel deployment/runtime inspection previously returned HTTP 403. Production deployment state and runtime logs must be verified with an account/team that has permission. No CX Switch project or Supabase schema was changed by this storage migration.
