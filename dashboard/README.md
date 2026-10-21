# Friday Control Room

A standalone Next.js dashboard for the research-only Friday engine. It reads the existing Friday Supabase research tables through a server-side API route; the service-role key is never sent to the browser.

## Local development

```bash
cd dashboard
cp .env.example .env.local
npm install
npm run dev
```

Set `FRIDAY_SUPABASE_URL` and `FRIDAY_SUPABASE_SERVICE_ROLE_KEY` in `.env.local` only after applying `../supabase/friday_research_schema.sql` to a dedicated research Supabase project. Without these values the dashboard shows a clear not-configured state and no invented live signals.

## Vercel

Create a dedicated Vercel project linked to `jasb-to/atlas-8` with Root Directory set to `dashboard`. Do this after the dashboard scaffold is ready to deploy. Configure the two server-only environment variables in Vercel Project Settings. Do not reuse the existing CX Switch project and do not expose the service role key as a `NEXT_PUBLIC_*` variable.

The UI is research-only and does not submit exchange orders. It refreshes the API every 30 seconds. Missing persistence is displayed as missing data, not a healthy state.
