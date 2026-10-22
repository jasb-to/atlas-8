# FRIDAY system audit — 22 October 2026

## Executive verdict

**Status: NOT VERIFIED END TO END. Do not treat the production scanner, persistence, or tradeable setup queue as operational yet.** The research code and UI exist, and the legacy setup workflow has run, but the new persisted FRIDAY monitor has not yet been observed completing successfully. Access to Vercel deployment/runtime diagnostics was denied during this audit, so the production deployment and environment cannot be independently confirmed.

## Evidence checked

- Repository: `jasb-to/atlas-8`, branch `main`.
- New monitor workflow: `.github/workflows/friday-monitor.yml`; now has a GitHub schedule of `17 */4 * * *` plus manual dispatch.
- Current GitHub Actions history at approximately 21:34 UTC: the latest observed FRIDAY-specific successful scheduled run was the **legacy** `friday-setup-monitor.yml` at 20:09 UTC. It only builds BTC and VIRTUAL 4H previews and publishes a workflow summary; it does not run the full multi-timeframe + Supabase monitor.
- No run of the new `friday-monitor.yml` appeared in the inspected recent Actions history. The first scheduled opportunity after the schedule commit is 00:17 UTC on 23 October (01:17 UK time). Scheduled jobs can start late.
- The push-triggered `Atlas-8 Backtest` workflow is not a dashboard build check. It was in progress after the latest UI commits.
- The connected Supabase project list showed projects named `CXSwitch` and `supabase-shadowsignalsv2`, but no clearly dedicated FRIDAY research project. Read-only SQL against CXSwitch showed none of the four expected FRIDAY tables. The shadow project SQL check timed out. **No FRIDAY schema was applied to either project.**
- Vercel deployment listing/runtime verification returned 403 permission errors; production commit, environment variables, and runtime logs remain unverified.

## System map

1. **Trigger:** cron-job.org calls `/api/friday/scan`.
2. **Dispatch route:** `dashboard/app/api/friday/scan/route.ts` authenticates `FRIDAY_CRON_SECRET`, then asks GitHub Actions to dispatch `friday-monitor.yml` using `FRIDAY_GITHUB_TOKEN`. This extra GitHub API hop is the likely failure boundary for the reported 502, but the precise upstream status/message has not been obtained.
3. **Independent fallback:** GitHub Actions now schedules `friday-monitor.yml` every four hours. This avoids the cron-job.org → Vercel → GitHub dispatch chain for scheduled scans, but has not yet been proven by a completed run.
4. **Engine:** `research/friday_monitor.py` runs `build_preview` and `analyse_pair` for each configured pair, writes local run artifacts, records decision and lifecycle archive rows, records alerts, and records a cron run in Supabase when configured. It exits nonzero unless the run status is `success`.
5. **Persistence:** `research/friday_persistence.py` currently selects Supabase only. Required tables are defined in `supabase/friday_research_schema.sql`.
6. **Dashboard:** `dashboard/app/api/friday/route.ts` reads `friday_research_archive`, `friday_trade_events`, `friday_cron_runs`, and `friday_alert_deliveries`. The browser refreshes that API every 30 seconds; it does not run the scan itself.

## Findings and severity

### P0 — Persistence destination is not proven / schema absent from checked project
The dashboard and engine depend on four Supabase tables. The accessible project list does not identify a dedicated FRIDAY project, and the CXSwitch database does not contain those tables. The engine workflow uses **GitHub repository secrets** `FRIDAY_SUPABASE_URL` and `FRIDAY_SUPABASE_SERVICE_ROLE_KEY`; Vercel variables do not automatically populate those GitHub secrets. Their actual values and existence cannot be inspected through the available permissions.

**Effect:** the monitor can fail on database writes, or persistence can be unconfigured; the dashboard then cannot show the scan history. Do not reuse CXSwitch's database as a workaround.

### P0 — cron-job.org dispatch 502 remains unproven
The Vercel route returns 502 if GitHub rejects the workflow dispatch or the fetch fails. The code now exposes a short upstream status/message in the response, but production deployment of that diagnostic version and the actual response have not been verified.

**Effect:** the original cron-job.org trigger may still fail even if the new GitHub schedule eventually works.

### P1 — no end-to-end acceptance run yet
A successful legacy setup monitor is not evidence that the new persistence-enabled workflow succeeded. The required evidence is a completed `friday-monitor.yml` run with both pairs processed, a successful `friday_cron_runs` row, archive/event rows, and dashboard reads.

### P1 — “tradeable” plan generation is not wired into the scanner
`research/friday_trade_plan.py` contains a causal plan builder with structural stops, targets, a minimum 1.5 reward/risk check, and blockers. However, `research/friday_monitor.py` does not import or call it. The persisted events mainly carry break/continuation references and evidence; the dashboard cannot currently prove that every displayed signal has a valid stop, target, and acceptable reward/risk.

**Effect:** an Entry 1 / Entry 2 event must not be represented as a fully risk-defined trade plan. The redesigned UI therefore labels these as setup signals and explicitly warns about missing risk-plan validation.

### P1 — dashboard CI coverage gap (mitigation committed; not yet verified)
The push workflow runs Python backtests, not the Next.js dashboard build/typecheck. A dedicated `.github/workflows/friday-dashboard-check.yml` has now been added to type-check and build the dashboard on pushes/PRs affecting `dashboard/**`. Its first run has not yet been confirmed successful. The UI commits are also not yet confirmed by a live production deployment.

### P2 — configuration documentation is inconsistent
`docs/VERCEL_SETUP.md` still describes Upstash as the required persistence backend, while the current persistence factory explicitly chooses Supabase for the POC. The main README/setup notes should consistently describe the Supabase-first design.

### P2 — overlapping monitor workflows cause ambiguity
There are two scheduled workflows: the older `friday-setup-monitor.yml` and the new `friday-monitor.yml`. They do different jobs but have similar schedules/names. The legacy one can appear “green” while the persistence-enabled engine is not running. Their roles should be made unmistakable, or the old workflow should be retired once the new path is verified.

## UI work committed

- Replaced the previous marketing-style hero, metrics and learning cards with a radar-first control room.
- Preserved the header, brand, palette and fonts.
- Added a central radar, setup signal queue, recent lifecycle ledger and compact engine diagnostics.
- The radar animation now only runs when the dashboard has a successful, recent run record and a working data connection. No demo signals are fabricated.
- The queue distinguishes setup events from fully risk-defined trade plans.

Relevant commits:
- `50911db5` — add dashboard typecheck/build CI workflow.
- `67aa0eb4` — radar-first dashboard layout.
- `5e0f1222` — simplified radar styling.
- `fe3fb65e` — stale run cannot be shown as healthy; queue language clarifies signal vs trade plan.
- `e6a93e71` — radar animation tied to verified health.

## Required closure plan

1. Confirm the intended dedicated FRIDAY Supabase project. Do not touch CXSwitch.
2. Apply `supabase/friday_research_schema.sql` to that project; verify all four tables and read/write access.
3. Confirm repository Actions secrets `FRIDAY_SUPABASE_URL` and `FRIDAY_SUPABASE_SERVICE_ROLE_KEY` exist and point to that same project. Confirm GitHub token scope for the dispatch route without sharing secrets in chat.
4. Observe a successful `friday-monitor.yml` run; inspect per-pair results and workflow artifacts. Confirm `friday_cron_runs`, `friday_research_archive`, `friday_trade_events`, and alert rows are written.
5. Confirm the deployed dashboard API reads those same records.
6. Test the cron-job.org endpoint end to end and capture the HTTP result. If 502 persists, use its `upstream_status` and `upstream_message` to fix the actual GitHub dispatch failure; do not infer it from the fallback schedule.
7. Wire `build_trade_plan` into the causal scan, persist eligibility/blockers/stop/target/reward-risk, and show only eligible plans in the tradeable panel.
8. Confirm the new dashboard build/typecheck gate passes and make one workflow the clearly designated full monitor.
9. Verify the production deployment commit and runtime logs once Vercel access is available.

## Acceptance criteria

FRIDAY is only **GREEN** when all are true: a recent successful full monitor run; both expected pairs scanned; same-run Supabase records confirmed; dashboard displays those records; tradeable items have a valid risk plan; cron-job.org trigger returns success; and production serves the expected commit. Until then, status is **AMBER/RED — not end-to-end verified**.

## Audit limits

This is a source/configuration and accessible-service audit, not a claim that every production runtime path was exercised. Vercel access denied deployment/runtime inspection, GitHub secrets cannot be read, and the dedicated FRIDAY database could not be identified from the accessible project list. No secrets were read or printed; no database schema was changed.


## Temporary repository-folder persistence change (22 October 2026, later update)

The user chose to defer Supabase during the initial testing period. The code now includes `RepoFolderFridayStore`, selected by `FRIDAY_STORAGE_BACKEND=repo` in `.github/workflows/friday-monitor.yml`. It maintains rolling JSON files under `testing/friday/data/` and a dashboard payload at `testing/friday/data/dashboard.json`. The workflow commits that state plus per-run research outputs under `testing/friday/runs/`. The dashboard API now reads the latest JSON payload through the GitHub Contents API, using Vercel's `FRIDAY_GITHUB_TOKEN`.

Commits for this change:
- `a39c8934` — temporary repo-folder store and backend selector.
- `acafc12c` — dashboard API reads repo-backed state.
- `0cec3c6a` — scheduled monitor selects repo backend and commits data.
- `94992571` — repository-store unit test.
- `82ed2531` — setup docs updated for the trial.
- `3b2891a3` — folder README and trial limitations.

**Not yet proven:** no successful full monitor run using the new backend has been observed yet; `testing/friday/data/dashboard.json` is generated by that run, not pre-populated. The dashboard deployment must also include the latest route change, and Vercel's `FRIDAY_GITHUB_TOKEN` must be able to read repository contents. The repo is public, so only public-safe research output may be committed. Git-based storage is a temporary single-writer workaround, not a replacement for a database: commits are delayed relative to the scan, rolling files have bounded retention, and the immutable per-run folders provide the audit trail. Supabase has not been modified.
