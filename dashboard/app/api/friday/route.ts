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
  [key: string]: unknown;
};

function response(body: DashboardPayload, status = 200) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}

export async function GET() {
  const token = process.env.FRIDAY_GITHUB_TOKEN;
  const repository = process.env.FRIDAY_GITHUB_REPOSITORY ?? "jasb-to/atlas-8";
  const branch = process.env.FRIDAY_GITHUB_DATA_BRANCH ?? "main";

  if (!token) {
    return response({
      connected: false,
      status: "not_configured",
      message: "Temporary repository storage is selected, but FRIDAY_GITHUB_TOKEN is missing from the Vercel Production environment.",
      snapshots: [], events: [], runs: [], alerts: [],
    });
  }

  try {
    const path = "testing/friday/data/dashboard.json";
    const url = `https://api.github.com/repos/${repository}/contents/${path}?ref=${encodeURIComponent(branch)}`;
    const result = await fetch(url, {
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      cache: "no-store",
    });

    if (result.status === 404) {
      return response({
        connected: false,
        status: "waiting_for_first_run",
        message: "Repository storage is configured. Waiting for the first successful FRIDAY run to publish dashboard data.",
        snapshots: [], events: [], runs: [], alerts: [],
      });
    }
    if (!result.ok) {
      throw new Error(`GitHub returned HTTP ${result.status} while reading FRIDAY's repository data.`);
    }

    const file = await result.json() as { content?: string; encoding?: string; sha?: string };
    if (file.encoding !== "base64" || !file.content) {
      throw new Error("FRIDAY dashboard data file was not returned as base64 content.");
    }
    const payload = JSON.parse(Buffer.from(file.content.replace(/\n/g, ""), "base64").toString("utf8")) as DashboardPayload;
    return response({
      ...payload,
      connected: true,
      status: "connected",
      storage_backend: "github_repository_folder",
      fetched_at: new Date().toISOString(),
      repository_data_sha: file.sha,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown error";
    console.error("[FRIDAY dashboard] Repository read failed:", message);
    return response({
      connected: false,
      status: "error",
      message: "Could not read FRIDAY's temporary repository data. Check FRIDAY_GITHUB_TOKEN access and the testing/friday/data/dashboard.json file.",
      snapshots: [], events: [], runs: [], alerts: [],
    }, 503);
  }
}
