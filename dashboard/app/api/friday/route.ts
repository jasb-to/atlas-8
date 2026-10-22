import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type JsonRow = Record<string, unknown>;
const supabaseUrl = (process.env.FRIDAY_SUPABASE_URL ?? "").replace(/\/$/, "");
const serviceRoleKey = process.env.FRIDAY_SUPABASE_SERVICE_ROLE_KEY;

function response(body: Record<string, unknown>, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}

async function selectRows(table: string, query: Record<string, string>): Promise<JsonRow[]> {
  if (!supabaseUrl || !serviceRoleKey) {
    throw new Error("FRIDAY Supabase environment variables are not configured.");
  }
  const params = new URLSearchParams({ select: "*", ...query });
  const result = await fetch(`${supabaseUrl}/rest/v1/${table}?${params.toString()}`, {
    headers: {
      apikey: serviceRoleKey,
      Authorization: `Bearer ${serviceRoleKey}`,
    },
    cache: "no-store",
  });
  if (!result.ok) throw new Error(`Supabase returned HTTP ${result.status} for ${table}.`);
  const data: unknown = await result.json();
  return Array.isArray(data) ? data as JsonRow[] : [];
}

export async function GET() {
  if (!supabaseUrl || !serviceRoleKey) {
    return response({
      connected: false,
      status: "not_configured",
      message: "Add FRIDAY_SUPABASE_URL and FRIDAY_SUPABASE_SERVICE_ROLE_KEY to the Vercel Production environment, and apply supabase/friday_research_schema.sql to that research database.",
      snapshots: [],
      events: [],
      runs: [],
      alerts: [],
    });
  }

  try {
    const [snapshots, events, runs, alerts] = await Promise.all([
      selectRows("friday_research_archive", {
        record_type: "eq.decision_snapshot",
        order: "signal_time.desc",
        limit: "12",
      }),
      selectRows("friday_trade_events", {
        order: "created_at.desc",
        limit: "50",
      }),
      selectRows("friday_cron_runs", {
        order: "started_at.desc",
        limit: "10",
      }),
      selectRows("friday_alert_deliveries", {
        order: "updated_at.desc",
        limit: "10",
      }),
    ]);

    return response({
      connected: true,
      status: "connected",
      message: "Connected to FRIDAY's Supabase research persistence.",
      snapshots,
      events,
      runs,
      alerts,
      fetched_at: new Date().toISOString(),
    });
  } catch (error) {
    console.error("[FRIDAY dashboard] Supabase read failed:", error instanceof Error ? error.message : "Unknown error");
    return response({
      connected: false,
      status: "error",
      message: "Could not read FRIDAY's Supabase research data. Check the Production URL/key and confirm the research schema has been applied.",
      snapshots: [],
      events: [],
      runs: [],
      alerts: [],
    }, 503);
  }
}
