import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type JsonRow = Record<string, unknown>;
const redisUrl = process.env.UPSTASH_REDIS_REST_URL?.replace(/\/$/, "");
const redisToken = process.env.UPSTASH_REDIS_REST_TOKEN;

async function command(...parts: (string | number)[]): Promise<unknown> {
  if (!redisUrl || !redisToken) throw new Error("Upstash Redis environment variables are not configured.");
  const response = await fetch(redisUrl, {
    method: "POST",
    headers: { Authorization: `Bearer ${redisToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(parts),
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Upstash returned HTTP ${response.status}`);
  const body = await response.json();
  if (body.error) throw new Error("Upstash Redis command failed.");
  return body.result;
}

async function listRows(key: string, limit: number): Promise<JsonRow[]> {
  const result = await command("LRANGE", key, "0", String(limit - 1));
  if (!Array.isArray(result)) return [];
  return result.flatMap((value) => {
    try {
      const parsed = JSON.parse(String(value));
      return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? [parsed as JsonRow] : [];
    } catch {
      return [];
    }
  });
}

async function archiveRows(): Promise<JsonRow[]> {
  const index = await listRows("FRIDAY:archive_index", 1000);
  const selected = index.filter((row) => row.record_type === "decision_snapshot").slice(0, 12);
  const rows: JsonRow[] = [];
  for (const item of selected) {
    if (typeof item.record_key !== "string") continue;
    const value = await command("GET", `FRIDAY:archive:${item.record_key}`);
    if (typeof value === "string") {
      try { rows.push(JSON.parse(value) as JsonRow); } catch { /* skip malformed legacy data */ }
    }
  }
  return rows;
}

async function alertRows(): Promise<JsonRow[]> {
  return listRows("FRIDAY:alerts", 10);
}

export async function GET() {
  if (!redisUrl || !redisToken) {
    return NextResponse.json({
      connected: false, status: "not_configured",
      message: "Add UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN to the Vercel project environment.",
      snapshots: [], events: [], runs: [], alerts: [],
    });
  }
  try {
    const [snapshots, events, runs, alerts] = await Promise.all([
      archiveRows(),
      listRows("FRIDAY:trades", 50),
      listRows("FRIDAY:runs", 10),
      alertRows(),
    ]);
    return NextResponse.json({
      connected: true, status: "connected",
      message: "Connected to Friday's Upstash research persistence.",
      snapshots, events, runs, alerts, fetched_at: new Date().toISOString(),
    }, { headers: { "Cache-Control": "no-store, max-age=0" } });
  } catch {
    return NextResponse.json({
      connected: false, status: "error",
      message: "Could not read Friday's Upstash research persistence. Check the URL, token and Redis availability.",
      snapshots: [], events: [], runs: [], alerts: [],
    }, { status: 503, headers: { "Cache-Control": "no-store, max-age=0" } });
  }
}
