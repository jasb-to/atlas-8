# Friday Vercel setup (preview-first)

Friday's dashboard lives in `dashboard/` in the `jasb-to/atlas-8` repository. Keep it separate from the CX Switch Vercel project.

## Create/link the project

1. Open the Vercel dashboard and select the intended team.
2. Add New → Project, import `jasb-to/atlas-8`, and name it `friday-research`.
3. Set **Root Directory** to `dashboard`; framework should be detected as Next.js.
4. Set the production branch to `main`. Keep Friday changes on `feature/price-touch-trendlines` until reviewed; its Git deployment should be a preview.
5. Do not connect the CX Switch project, reuse its environment variables, or enable any live-trading integration.

## Environment variables

The current proof-of-concept persistence backend is **Supabase** (not Upstash). Apply `supabase/friday_research_schema.sql` only to a dedicated FRIDAY research project. Do not reuse the CX Switch database.

| Variable | Required? | Where it is used |
| --- | --- | --- |
| `FRIDAY_SUPABASE_URL` | Yes | Vercel dashboard API and GitHub Actions monitor |
| `FRIDAY_SUPABASE_SERVICE_ROLE_KEY` | Yes | Server-side Vercel API and GitHub Actions monitor; sensitive service-role secret |
| `FRIDAY_CRON_SECRET` | Required for cron endpoint | Vercel Production only; authenticates cron-job.org |
| `FRIDAY_GITHUB_TOKEN` | Required for cron dispatch route | Vercel Production only; GitHub Actions workflow-dispatch permission |
| `FRIDAY_TELEGRAM_BOT_TOKEN` | Optional | GitHub Actions research alerts |
| `FRIDAY_TELEGRAM_CHAT_ID` | Optional | GitHub Actions research alerts |
| `HUGGINGFACE_API_KEY` | Optional | Advisory-only model review |

Never use `NEXT_PUBLIC_` on secrets. Never paste tokens into chat or commit them to Git. Friday must remain research-only; do not configure exchange API keys.

**Important:** Vercel Production environment variables and GitHub repository Actions secrets are separate stores. Configure `FRIDAY_SUPABASE_URL` and `FRIDAY_SUPABASE_SERVICE_ROLE_KEY` in both places. The GitHub workflow also accepts `FRIDAY_HF_MODEL_ID` and `FRIDAY_HF_ENDPOINT` as repository variables. Telegram credentials are optional. Do not configure paid inference or buy credits if zero spend is required.

## Verify the preview

1. Wait for the feature-branch preview build to finish.
2. Open `/api/friday`; with valid Upstash credentials it should return `connected: true`. Without credentials it should clearly return `not_configured`, not leak secrets.
3. Open the dashboard page and verify recent snapshots, up to 50 setup events, cron runs, and alert states.
4. Trigger the GitHub Actions workflow manually after adding Upstash GitHub secrets. Confirm the run and `cron_run.json` report successful persistence.
5. Only promote/release after the preview is verified. This project does not place exchange orders.

## Current limitation

The connected Vercel API has returned HTTP 403 for deployment inspection. Production deployment state and runtime logs must be verified with an account/team that has permission. No CX Switch project was changed.
