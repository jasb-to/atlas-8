import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type DashboardPayload = {
  connected?: boolean;
  status?: string;
  message?: string;
  snapshots?: unknown[];
  events?: unknown[];
  runs?: unknown[];
  alerts?: unknown[];
  paper_trades?: unknown[];
  [key: string]: unknown;
};

function response(body: DashboardPayload, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}

export async function GET() {
  const repository = process.env.FRIDAY_GITHUB_REPOSITORY ?? "jasb-to/atlas-8";
  const branch = process.env.FRIDAY_GITHUB_DATA_BRANCH ?? "main";
  const path = "testing/friday/data/dashboard.json";
  const url = `https://raw.githubusercontent.com/${repository}/${encodeURIComponent(branch)}/${path}?t=${Date.now()}`;

  try {
    const result = await fetch(url, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    if (result.status === 404) {
      return response({
        connected: false,
        status: "waiting_for_first_run",
        message: "Waiting for the first successful FRIDAY run to publish dashboard data to the repository.",
        snapshots: [], events: [], runs: [], alerts: [], paper_trades: [],
      });
    }
    if (!result.ok) {
      throw new Error(`Repository data fetch returned HTTP ${result.status}.`);
    }

    const payload = await result.json() as DashboardPayload;
    return response({
      ...payload,
      connected: true,
      status: "connected",
      storage_backend: "github_repository_folder",
      fetched_at: new Date().toISOString(),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    console.error("[FRIDAY dashboard] Repository read failed:", message);
    return response({
      connected: false,
      status: "error",
      message: "Could not read FRIDAY's temporary repository data. Check the public repository data file and branch settings.",
      snapshots: [], events: [], runs: [], alerts: [],
    }, 503);
  }
}
