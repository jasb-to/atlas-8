import { timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const runtime = "nodejs";

const GITHUB_DISPATCH_URL =
  "https://api.github.com/repos/jasb-to/atlas-8/actions/workflows/friday-monitor.yml/dispatches";

function safeEqual(left: string, right: string): boolean {
  const a = Buffer.from(left, "utf8");
  const b = Buffer.from(right, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

async function triggerScan(request: NextRequest) {
  const expectedSecret = process.env.FRIDAY_CRON_SECRET;
  const githubToken = process.env.FRIDAY_GITHUB_TOKEN;

  if (!expectedSecret || !githubToken) {
    return NextResponse.json(
      {
        ok: false,
        error: "scan_trigger_not_configured",
        message:
          "Set FRIDAY_CRON_SECRET and FRIDAY_GITHUB_TOKEN in the Vercel production environment.",
      },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  // Support both cron services that can send a Bearer header and URL-based
  // schedulers. Prefer the header when present; query-string secrets can appear
  // in scheduler history and request logs, so keep the URL private.
  const authorization = request.headers.get("authorization") ?? "";
  const headerSecret = authorization.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length)
    : "";
  const suppliedSecret =
    headerSecret || request.nextUrl.searchParams.get("secret") || "";

  if (!suppliedSecret || !safeEqual(suppliedSecret, expectedSecret)) {
    return NextResponse.json(
      { ok: false, error: "unauthorized" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }

  try {
    const upstream = await fetch(GITHUB_DISPATCH_URL, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${githubToken}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: { pairs: "XBTUSD,VIRTUALUSD" },
      }),
      cache: "no-store",
    });

    if (upstream.status !== 204) {
      return NextResponse.json(
        {
          ok: false,
          error: "workflow_dispatch_failed",
          upstream_status: upstream.status,
        },
        { status: 502, headers: { "Cache-Control": "no-store" } },
      );
    }

    return NextResponse.json(
      {
        ok: true,
        status: "accepted",
        message: "FRIDAY research workflow dispatch accepted by GitHub Actions.",
        workflow: "friday-monitor.yml",
        ref: "main",
        pairs: ["XBTUSD", "VIRTUALUSD"],
      },
      { status: 202, headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json(
      { ok: false, error: "workflow_dispatch_unreachable" },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}

export async function GET(request: NextRequest) {
  return triggerScan(request);
}

export async function POST(request: NextRequest) {
  return triggerScan(request);
}
