import { NextResponse } from "next/server";
import { createPublicKey, verify as verifySignature } from "node:crypto";

export const dynamic = "force-dynamic";
export const revalidate = 0;
export const maxDuration = 30;

const ISSUER = "https://token.actions.githubusercontent.com";
const AUDIENCE = "friday-huggingface-review";
const REPOSITORY = "jasb-to/atlas-8";
const WORKFLOW = "jasb-to/atlas-8/.github/workflows/friday-monitor.yml@refs/heads/main";
const DEFAULT_MODEL = "openai/gpt-oss-120b:cheapest";
let jwksCache: { expiresAt: number; keys: any[] } | undefined;

// Retry only transient transport/provider failures. Each attempt gets its own
// timeout so an expired AbortSignal cannot make every retry fail immediately.
// Two bounded attempts fit within this route's 30-second Vercel duration.
async function fetchWithTransientRetry(
  input: RequestInfo | URL,
  init: RequestInit,
  provider: string,
): Promise<Response> {
  const retryableStatuses = new Set([408, 429, 500, 502, 503, 504]);
  const maxAttempts = 2;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(input, {
        ...init,
        signal: AbortSignal.timeout(12000),
      });
      const retryAfter = Number(response.headers.get("retry-after") ?? "0");
      const canRetry = attempt < maxAttempts
        && retryableStatuses.has(response.status)
        && !(response.status === 429 && Number.isFinite(retryAfter) && retryAfter > 1);
      if (!canRetry) return response;
      console.warn("[FRIDAY AI review] transient provider response; retrying", {
        provider, attempt, status: response.status,
      });
      await response.body?.cancel().catch(() => undefined);
    } catch (error) {
      const name = error instanceof Error ? error.name : "UnknownError";
      const retryable = name === "TypeError" || name === "TimeoutError" || name === "AbortError";
      if (attempt >= maxAttempts || !retryable) throw error;
      console.warn("[FRIDAY AI review] transient provider transport failure; retrying", {
        provider, attempt, error_class: name,
      });
    }
    await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
  }
  throw new Error("Provider retry loop exhausted");
}

function json(body: unknown, status = 200) {
  return NextResponse.json(body, { status, headers: { "Cache-Control": "no-store, max-age=0" } });
}

async function verifyActionsToken(token: string) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("Malformed OIDC token");
  const decode = (value: string) => JSON.parse(Buffer.from(value, "base64url").toString("utf8"));
  const header = decode(parts[0]);
  const claims = decode(parts[1]);
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new Error("Unsupported OIDC signing key");
  if (claims.iss !== ISSUER || claims.aud !== AUDIENCE) throw new Error("OIDC issuer or audience mismatch");
  const now = Math.floor(Date.now() / 1000);
  if (typeof claims.exp !== "number" || claims.exp <= now || (typeof claims.nbf === "number" && claims.nbf > now + 30)) {
    throw new Error("OIDC token is expired or not yet valid");
  }
  if (claims.repository !== REPOSITORY || claims.ref !== "refs/heads/main" || claims.workflow_ref !== WORKFLOW) {
    throw new Error("OIDC identity is not the approved FRIDAY main workflow");
  }
  if (!jwksCache || jwksCache.expiresAt < Date.now()) {
    const response = await fetch(`${ISSUER}/.well-known/jwks`, { cache: "no-store" });
    if (!response.ok) throw new Error("Could not retrieve GitHub OIDC signing keys");
    const body = await response.json() as { keys?: any[] };
    jwksCache = { keys: body.keys ?? [], expiresAt: Date.now() + 10 * 60 * 1000 };
  }
  const jwk = jwksCache.keys.find((key) => key.kid === header.kid && key.kty === "RSA");
  if (!jwk) throw new Error("OIDC signing key not found");
  const publicKey = createPublicKey({ key: jwk, format: "jwk" });
  const signed = Buffer.from(`${parts[0]}.${parts[1]}`);
  const signature = Buffer.from(parts[2], "base64url");
  if (!verifySignature("RSA-SHA256", signed, publicKey, signature)) throw new Error("Invalid OIDC signature");
  return claims;
}

export async function POST(request: Request) {
  const authorization = request.headers.get("authorization") ?? "";
  const token = authorization.startsWith("Bearer ") ? authorization.slice(7) : "";
  if (!token) return json({ status: "error", error: "Missing GitHub Actions identity" }, 401);
  try {
    await verifyActionsToken(token);
  } catch (error) {
    const message = error instanceof Error ? error.message : "OIDC verification failed";
    return json({ status: "error", error: message }, 401);
  }

  // Prefer Gemini when configured; retain Hugging Face as a compatibility fallback.
  const geminiKey = process.env.GEMINI_API_KEY;
  const hfKey = process.env.HUGGINGFACE_API_KEY;
  const provider = geminiKey ? "gemini" : hfKey ? "huggingface" : null;
  if (!provider) return json({ status: "error", error: "Neither GEMINI_API_KEY nor HUGGINGFACE_API_KEY is configured in Vercel" }, 503);
  // Gemini 2.5 Flash is restricted for new users and returns HTTP 404 on this key.
  // Migrate the legacy default/override to the supported, cost-efficient Flash-Lite model.
  const configuredGeminiModel = process.env.FRIDAY_GEMINI_MODEL;
  const geminiModel = !configuredGeminiModel || configuredGeminiModel === "gemini-2.5-flash"
    ? "gemini-3.5-flash-lite"
    : configuredGeminiModel;
  const model = provider === "gemini"
    ? geminiModel
    : (process.env.FRIDAY_HF_MODEL_ID || "openai/gpt-oss-120b:cheapest");
  let input: any;
  try { input = await request.json(); } catch { return json({ status: "error", error: "Invalid JSON request" }, 400); }
  if (!input?.decision || typeof input.decision !== "object" || Array.isArray(input.decision)) {
    return json({ status: "error", error: "A frozen decision object is required" }, 400);
  }

  const prompt = {
    task: "Review and rank the supplied causal crypto trade candidates as an independent research analyst. This is AI-assisted discovery/ranking over a deterministic candidate universe, not free-form price prediction.",
    rules: [
      "Use only the supplied evidence; do not infer missing chart data.",
      "Do not invent prices, indicators, probabilities, or future outcomes.",
      "Do not invent candidates, prices, direction, stops, targets, or future outcomes. Only discuss candidates explicitly present in candidate_universe or frozen_decision.",
      "Evaluate candidate_universe independently from rule_decision. A NO_TRADE rule_decision does not automatically disqualify a separate candidate that is explicitly marked eligible in candidate_universe.",
      "Set priority_candidate to the exact identifier of the supplied candidate: structural_trade_plans.long, structural_trade_plans.short, momentum_reversal_candidate.long, or momentum_reversal_candidate.short. Use null if no eligible candidate exists. Do not return a bare direction or free-form explanation in this field.",
      "Treat deterministic eligibility, directional momentum, exhaustion filters, stop/target geometry, and >=1.5R as hard gates.",
      "A supportive AI opinion cannot promote a blocked candidate or bypass any hard gate.",
      "Identify missing evidence, contradictory evidence, and likely false-break risks.",
      "Return a JSON object with keys: assessment (supportive, mixed, caution, insufficient_evidence), priority_candidate (string or null), confidence (low, medium, high; qualitative only), supporting_evidence (array), counter_evidence (array), missing_evidence (array), rationale (string).",
      "This is shadow research, not financial advice and not an execution instruction."
    ],
    frozen_decision: input.decision
  };
  const started = Date.now();
  try {
    let response: Response;
    let body: any;
    let raw: any;
    if (provider === "gemini") {
      response = await fetchWithTransientRetry(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
        method: "POST",
        headers: { "x-goog-api-key": geminiKey!, "Content-Type": "application/json" },
        body: JSON.stringify({
          systemInstruction: { parts: [{ text: "You are Friday's cautious, evidence-bound research reviewer. Output JSON only." }] },
          contents: [{ role: "user", parts: [{ text: JSON.stringify(prompt) }] }],
          generationConfig: {
            temperature: 0,
            responseMimeType: "application/json",
            responseSchema: {
              type: "OBJECT",
              properties: {
                assessment: { type: "STRING", enum: ["supportive", "mixed", "caution", "insufficient_evidence"] },
                priority_candidate: { type: "STRING", nullable: true },
                confidence: { type: "STRING", enum: ["low", "medium", "high"] },
                supporting_evidence: { type: "ARRAY", items: { type: "STRING" } },
                counter_evidence: { type: "ARRAY", items: { type: "STRING" } },
                missing_evidence: { type: "ARRAY", items: { type: "STRING" } },
                rationale: { type: "STRING" }
              },
              required: ["assessment", "priority_candidate", "confidence", "supporting_evidence", "counter_evidence", "missing_evidence", "rationale"]
            }
          }
        }),
        signal: AbortSignal.timeout(12000)
      }, provider);
    } else {
      response = await fetchWithTransientRetry(process.env.FRIDAY_HF_ENDPOINT || "https://router.huggingface.co/v1/chat/completions", {
        method: "POST",
        headers: { Authorization: `Bearer ${hfKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model,
          temperature: 0,
          response_format: { type: "json_object" },
          messages: [
            { role: "system", content: "You are Friday's cautious, evidence-bound research reviewer. Output JSON only." },
            { role: "user", content: JSON.stringify(prompt) }
          ]
        }),
        signal: AbortSignal.timeout(12000)
      }, provider);
    }
    if (!response.ok) {
      // Do not expose provider response bodies to the caller; record actionable,
      // non-secret diagnostics in Vercel logs and return a stable error category.
      const status = response.status;
      const category = status === 402 ? "quota_or_billing"
        : status === 401 || status === 403 ? "provider_auth"
        : status === 404 ? "model_or_endpoint_not_found"
        : status === 408 ? "provider_timeout"
        : status === 429 ? "rate_limit_or_quota"
        : status >= 500 ? "provider_unavailable"
        : "provider_rejected_request";
      const upstreamError = (await response.text()).slice(0, 180).replace(/(Bearer\s+)[^\s,"]+/gi, "$1[REDACTED]");
      console.error("[FRIDAY AI review] upstream request failed", {
        provider, model_id: model, upstream_status: status, category,
        latency_ms: Date.now() - started, response_excerpt: upstreamError,
      });
      return json({
        status: "error",
        error: `${provider === "gemini" ? "Gemini" : "Hugging Face"} returned HTTP ${status}`,
        error_category: category,
        upstream_status: status,
        provider,
        model_id: model,
        latency_ms: Date.now() - started,
      }, 502);
    }
    body = await response.json();
    if (provider === "gemini") {
      raw = body?.candidates?.[0]?.content?.parts?.map((part: any) => typeof part.text === "string" ? part.text : "").join("");
      if (typeof raw !== "string" || !raw.trim()) throw new Error("Gemini response had no text content");
    } else {
      raw = body?.choices?.[0]?.message?.content;
      if (Array.isArray(raw)) raw = raw.filter((part: any) => part && typeof part.text === "string").map((part: any) => part.text).join("");
    }
    if (typeof raw !== "string") throw new Error("Model response had no text content");
    raw = raw.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
    let parsed: any;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Some compatible routers wrap a valid JSON object in explanatory text.
      // Recover only a complete object; never infer or repair individual fields.
      const firstObject = raw.indexOf("{");
      const lastObject = raw.lastIndexOf("}");
      if (firstObject < 0 || lastObject <= firstObject) throw new Error("Model response was not valid JSON");
      parsed = JSON.parse(raw.slice(firstObject, lastObject + 1));
    }
    const assessments = ["supportive", "mixed", "caution", "insufficient_evidence"];
    if (!parsed || typeof parsed !== "object" || !assessments.includes(parsed.assessment)) throw new Error("Model response assessment failed schema validation");
    for (const field of ["supporting_evidence", "counter_evidence", "missing_evidence"]) {
      if (!Array.isArray(parsed[field])) throw new Error(`Model response field ${field} must be an array`);
    }
    if (parsed.confidence !== "low" && parsed.confidence !== "medium" && parsed.confidence !== "high") parsed.confidence = "low";
    if (parsed.priority_candidate !== null && typeof parsed.priority_candidate !== "string") parsed.priority_candidate = null;
    if (typeof parsed.rationale !== "string") parsed.rationale = "";
    return json({ status: "ok", provider, model_id: model, response: parsed, latency_ms: Date.now() - started, error: null });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Model review failed";
    const category = error instanceof Error && error.name === "TimeoutError"
      ? "provider_timeout"
      : error instanceof Error && error.name === "SyntaxError"
        ? "invalid_provider_response"
        : "provider_transport_or_parse_error";
    console.error("[FRIDAY AI review] request failed", {
      provider, model_id: model, category,
      error_class: error instanceof Error ? error.name : "UnknownError",
      latency_ms: Date.now() - started,
      message: message.slice(0, 160),
    });
    return json({
      status: "error", provider, model_id: model, response: null,
      latency_ms: Date.now() - started, error_category: category,
      error: message.slice(0, 200),
    }, 502);
  }
}
