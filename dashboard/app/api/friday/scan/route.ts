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

function json(body: Record<string, unknown>, status: number) {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}

async function triggerScan(request: NextRequest) {
  // Authenticate before checking configuration so unauthenticated callers
  // always receive the same response and cannot probe configured secrets.
  const expectedSecret = process.env.FRIDAY_CRON_SECRET;
  const authorization = request.headers.get("authorization") ?? "";
  const headerSecret = authorization.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length)
    : "";
  const suppliedSecret =
    headerSecret || request.nextUrl.searchParams.get("secret") || "";

  if (!expectedSecret || !suppliedSecret || !safeEqual(suppliedSecret, expectedSecret)) {
    return json({ ok: false, error: "unauthorized" }, 401);
  }

  const githubToken = process.env.FRIDAY_GITHUB_TOKEN;
  const missing = [
    !expectedSecret ? "FRIDAY_CRON_SECRET" : null,
    !githubToken ? "FRIDAY_GITHUB_TOKEN" : null,
  ].filter((name): name is string => Boolean(name));

  // expectedSecret is already verified above; this identifies a missing GitHub
  // token without ever returning environment variable values.
  if (missing.length > 0) {
    console.error("[FRIDAY scan] Required production environment variables are missing:", missing);
    return json({
      ok: false,
      error: "scan_trigger_not_configured",
      missing,
      message: "Required FRIDAY production environment variables are not available to this deployment.",
    }, 503);
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
      // GitHub's response body is deliberately not reflected to the caller;
      // status codes are enough to distinguish permissions/configuration errors.
      console.error("[FRIDAY scan] GitHub workflow dispatch rejected", {
        status: upstream.status,
      });
      return json({
        ok: false,
        error: "workflow_dispatch_failed",
        upstream_status: upstream.status,
        hint:
          upstream.status === 401 || upstream.status === 403
            ? "Check that FRIDAY_GITHUB_TOKEN is valid, scoped to jasb-to/atlas-8, and has Actions: Read and write."
            : upstream.status === 404
              ? "Check repository access and that .github/workflows/friday-monitor.yml exists on main."
              : upstream.status === 422
                ? "Check that the workflow supports workflow_dispatch and that the requested input is valid."
                : "Check the GitHub Actions workflow and repository access.",
      }, 502);
    }

    return json({
      ok: true,
      status: "accepted",
      message: "FRIDAY research workflow dispatch accepted by GitHub Actions.",
      workflow: "friday-monitor.yml",
      ref: "main",
      pairs: ["XBTUSD", "VIRTUALUSD"],
    }, 202);
  } catch (error) {
    console.error("[FRIDAY scan] GitHub API request failed", {
      message: error instanceof Error ? error.message : "Unknown fetch error",
    });
    return json({ ok: false, error: "workflow_dispatch_unreachable" }, 502);
  }
}

export async function GET(request: NextRequest) {
  return triggerScan(request);
}

export async function POST(request: NextRequest) {
  return triggerScan(request);
}
