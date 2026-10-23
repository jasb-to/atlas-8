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
  const model = provider === "gemini"
    ? (process.env.FRIDAY_GEMINI_MODEL || "gemini-2.5-flash")
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
      "Identify which supplied candidate deserves the most attention and why, using its supplied strategy and signal time in priority_candidate. If no eligible candidate exists, use null.",
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
      response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
        method: "POST",
        headers: { "x-goog-api-key": geminiKey!, "Content-Type": "application/json" },
        body: JSON.stringify({
          systemInstruction: { parts: [{ text: "You are Friday's cautious, evidence-bound research reviewer. Output JSON only." }] },
          contents: [{ role: "user", parts: [{ text: JSON.stringify(prompt) }] }],
          generationConfig: { temperature: 0, responseMimeType: "application/json" }
        }),
        signal: AbortSignal.timeout(25000)
      });
    } else {
      response = await fetch(process.env.FRIDAY_HF_ENDPOINT || "https://router.huggingface.co/v1/chat/completions", {
        method: "POST",
        headers: { Authorization: `Bearer ${hfKey}`, "Content-Type": "application/json" },
        body: JSON.stringify({
          model,
          temperature: 0,
          messages: [
            { role: "system", content: "You are Friday's cautious, evidence-bound research reviewer. Output JSON only." },
            { role: "user", content: JSON.stringify(prompt) }
          ]
        }),
        signal: AbortSignal.timeout(25000)
      });
    }
    if (!response.ok) {
      const upstreamError = (await response.text()).slice(0, 180);
      return json({
        status: "error",
        error: `${provider === "gemini" ? "Gemini" : "Hugging Face"} returned HTTP ${response.status}: ${upstreamError}`,
        provider,
        model_id: model
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
    const parsed = JSON.parse(raw);
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
    return json({ status: "error", provider, model_id: model, response: null, latency_ms: Date.now() - started, error: message.slice(0, 200) }, 502);
  }
}
