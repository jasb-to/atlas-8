import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type JsonRow = Record<string, unknown>;
const base = process.env.FRIDAY_SUPABASE_URL?.replace(/\/$/, "");
const key = process.env.FRIDAY_SUPABASE_SERVICE_ROLE_KEY;

async function readTable(table: string, query: string): Promise<{ rows: JsonRow[]; error?: string }> {
  if (!base || !key) return { rows: [], error: "Supabase server environment variables are not configured." };
  try {
    const response = await fetch(`${base}/rest/v1/${table}?${query}`, {
      headers: { apikey: key, Authorization: `Bearer ${key}`, Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return { rows: [], error: `${table} returned HTTP ${response.status}` };
    const data = await response.json();
    return { rows: Array.isArray(data) ? data : [] };
  } catch {
    return { rows: [], error: `Could not reach ${table}.` };
  }
}

export async function GET() {
  if (!base || !key) {
    return NextResponse.json({ connected: false, status: "not_configured", message: "Connect Friday's dedicated research Supabase project to enable persisted data.", snapshots: [], events: [], runs: [], alerts: [] });
  }
  const [snapshots, events, runs, alerts] = await Promise.all([
    readTable("friday_research_archive", "select=record_key,pair,timeframe,signal_time,payload,updated_at&record_type=eq.decision_snapshot&order=signal_time.desc&limit=12"),
    readTable("friday_trade_events", "select=event_key,pair,timeframe,event_type,direction,signal_time,entry_reference,line_kind,line_price,outcome,retest_confirmed,continuation_add_time,continuation_add_close,failure_time,evidence,created_at&order=signal_time.desc&limit=50"),
    readTable("friday_cron_runs", "select=run_id,started_at,finished_at,status,pairs_checked,pair_results,error&order=started_at.desc&limit=10"),
    readTable("friday_alert_deliveries", "select=alert_key,pair,alert_type,signal_time,status,error,updated_at&order=created_at.desc&limit=10"),
  ]);
  const errors = [snapshots.error, events.error, runs.error, alerts.error].filter(Boolean);
  return NextResponse.json({
    connected: errors.length === 0,
    status: errors.length ? "partial_or_error" : "connected",
    message: errors.length ? errors.join(" ") : "Connected to Friday research persistence.",
    snapshots: snapshots.rows, events: events.rows, runs: runs.rows, alerts: alerts.rows,
    fetched_at: new Date().toISOString(),
  }, { headers: { "Cache-Control": "no-store, max-age=0" } });
}
