"use client";

import { useEffect, useState } from "react";

type Row = Record<string, any>;
type Payload = {
  connected: boolean; status: string; message: string;
  snapshots: Row[]; weekly_theses: Row[]; events: Row[]; runs: Row[]; alerts: Row[]; paper_trades: Row[];
  fetched_at?: string;
};
const empty: Payload = { connected: false, status: "loading", message: "Loading FRIDAY data…", snapshots: [], weekly_theses: [], events: [], runs: [], alerts: [], paper_trades: [] };
const WATCH_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT"];
const fmtTime = (value?: string) => value ? new Date(value).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }) : "—";
const fmtPrice = (value: unknown) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-GB", { maximumFractionDigits: 6 }) : "—";
const payloadOf = (row?: Row) => row?.payload && typeof row.payload === "object" ? row.payload : {};
const clean = (value: unknown) => String(value ?? "—").replaceAll("_", " ");
const pairName = (value: unknown) => {
  const pair = String(value ?? "").toUpperCase();
  if (["XBTUSDT", "XBTUSD", "BTCUSD"].includes(pair)) return "BTCUSDT";
  return pair.endsWith("USD") && !pair.endsWith("USDT") ? pair.slice(0, -3) + "USDT" : pair;
};
const shortPair = (value: unknown) => pairName(value).replace(/USDT$/, "");
const direction = (value: unknown) => String(value ?? "").toLowerCase();
const isBull = (value: unknown) => ["long", "bullish", "active_long"].includes(direction(value));
const isBear = (value: unknown) => ["short", "bearish", "active_short"].includes(direction(value));
const thesisFor = (snapshots: Row[], pair: string) => {
  for (const snap of snapshots) {
    if (pairName(snap.pair) !== pair) continue;
    const p = payloadOf(snap);
    const thesis = snap.record_type === "weekly_thesis_snapshot" ? p : (p.weekly_thesis ?? p.sheldon_weekly_thesis ?? p.weeklyThesis ?? p.multitimeframe_scan?.weekly_thesis);
    if (thesis && typeof thesis === "object") return thesis as Row;
  }
  return null;
};

export default function Home() {
  const [data, setData] = useState<Payload>(empty);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setBusy(true);
    try {
      const response = await fetch("/api/friday", { cache: "no-store" });
      const body = await response.json();
      setData({ ...empty, ...body });
    } catch {
      setData({ ...empty, status: "unreachable", message: "Dashboard API could not be reached." });
    } finally { setBusy(false); }
  }
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30000);
    return () => window.clearInterval(timer);
  }, []);

  const latestRun = data.runs[0];
  const pairScans: Row[] = Array.isArray(latestRun?.pair_results) ? latestRun.pair_results : [];
  const snapshots = data.snapshots ?? [];
  const openTrades = data.paper_trades.filter(trade => String(trade.status).toUpperCase() === "OPEN");
  const scanByPair = new Map<string, Row>(pairScans.map(scan => [pairName(scan.pair), scan] as [string, Row]));
  const successfulAiReview = pairScans.find(scan => String(scan.ai_review_status ?? "").toLowerCase() === "ok");
  const aiReviewActive = Boolean(successfulAiReview);
  const aiProvider = String(successfulAiReview?.ai_provider ?? "GROQ").toUpperCase();
  const weeklyCards = WATCH_PAIRS.map(pair => {
    const thesis = thesisFor(data.weekly_theses ?? [], pair) ?? thesisFor(snapshots, pair);
    const scan = scanByPair.get(pair);
    const weeklyTrend = thesis?.weekly_trend ?? thesis?.weekly_status ?? thesis?.weekly_direction ??
      (thesis?.weekly_ema8 != null && thesis?.weekly_ema21 != null
        ? Number(thesis.weekly_ema8) > Number(thesis.weekly_ema21) ? "bullish" : "bearish"
        : undefined);
    const dailyTrend = thesis?.daily_alignment ?? thesis?.daily_status ?? thesis?.daily_direction ??
      (thesis?.daily_ema8 != null && thesis?.daily_ema21 != null &&
       thesis?.daily_stoch_k != null && thesis?.daily_stoch_d != null
        ? Number(thesis.daily_ema8) > Number(thesis.daily_ema21) && Number(thesis.daily_stoch_k) > Number(thesis.daily_stoch_d)
          ? "bullish"
          : Number(thesis.daily_ema8) < Number(thesis.daily_ema21) && Number(thesis.daily_stoch_k) < Number(thesis.daily_stoch_d)
            ? "bearish"
            : "misaligned"
        : undefined);
    const weeklyMomentum = thesis?.weekly_stoch_aligned === true
      ? "K > D"
      : thesis?.weekly_stoch_aligned === false
        ? "K < D"
        : thesis?.weekly_stoch_k != null && thesis?.weekly_stoch_d != null
          ? Number(thesis.weekly_stoch_k) > Number(thesis.weekly_stoch_d) ? "K > D" : "K < D"
          : "Not available";
    const active = String(thesis?.status ?? "").toLowerCase() === "active";
    return { pair, thesis, scan, weeklyTrend, dailyTrend, weeklyMomentum, active };
  });
  const watchlist = weeklyCards.filter(card =>
    card.active && !openTrades.some(trade => pairName(trade.pair) === card.pair)
  );
  const recentEvents = data.events.slice(0, 40);

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#"><span className="brand-mark">F</span><span>FRIDAY<span className="brand-sub">RESEARCH ONLY</span></span></a>
      <div className="top-actions">
        <span className={data.connected ? "pill positive" : "pill neutral"}><i /> DATA: {data.connected ? "ONLINE" : "OFFLINE"}</span>
        <span className={aiReviewActive && aiProvider === "GROQ" ? "pill positive" : "pill neutral"}><i /> AI: GROQ {aiReviewActive && aiProvider === "GROQ" ? "ONLINE" : "OFFLINE"}</span>
        <button className="refresh" onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "↻ Refresh"}</button>
      </div>
    </header>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">01 / HIGHER TIMEFRAME</span><h2>WEEKLY THESIS</h2><p>Completed weekly and daily candles. The 4H engine only runs when the thesis is active.</p></div></div>
      <div className="thesis-grid">
        {weeklyCards.map(card => {
          const trend = card.weeklyTrend ?? "Unavailable";
          const dailyPass = String(card.dailyTrend ?? "").toLowerCase() === "pass" ||
            (String(card.dailyTrend ?? "").toLowerCase() !== "veto" && card.active);
          const status = String(card.thesis?.status ?? "").toLowerCase() === "active"
            ? "ACTIVE"
            : String(card.thesis?.status ?? "").toLowerCase() === "veto" ? "VETO" : "SKIPPED";
          const waveLabel = card.thesis?.wave_state
            ? `${clean(card.thesis.wave_state)} (${String(card.thesis.direction ?? "").toUpperCase()})`
            : card.weeklyMomentum;
          return <article className="thesis-card" key={card.pair}>
            <div className="thesis-top"><b>{shortPair(card.pair)}</b><span className={card.active ? "pill positive" : "pill neutral"}>{status}</span></div>
            <div className="thesis-row"><span>MACRO</span><b className={isBull(trend) ? "positive-text" : isBear(trend) ? "negative-text" : ""}>{isBull(trend) ? "BULLISH" : isBear(trend) ? "BEARISH" : clean(trend).toUpperCase()}</b></div>
            <div className="thesis-row"><span>WAVE</span><b className={isBear(card.thesis?.direction) ? "negative-text" : isBull(card.thesis?.direction) ? "positive-text" : ""}>{clean(waveLabel).replace(/^./, value => value.toUpperCase())}</b></div>
            <div className="thesis-row"><span>DAILY ALIGNMENT</span><b className={dailyPass ? "positive-text" : "negative-text"}>{dailyPass ? "PASS" : "VETO"}</b></div>
            <div className="thesis-bottom"><span>THESIS STATUS</span><b>{status}</b></div>
          </article>;
        })}
      </div>
    </section>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">02 / ACTIVE POSITIONS</span><h2>OPEN PAPER TRADES</h2><p>Only open paper positions appear here. No exchange orders are placed.</p></div><span className="section-count">{openTrades.length} OPEN</span></div>
      {openTrades.length ? <div className="trade-grid">
        {openTrades.map((trade, index) => {
          const dir = direction(trade.direction);
          const entry = Number(trade.entry);
          const stop = Number(trade.stop_loss);
          const target = Number(trade.take_profit);
          const risk = Math.abs(entry - stop);
          const tp1 = dir === "long" ? entry + risk * 1.5 : entry - risk * 1.5;
          return <article className={dir === "long" ? "trade-card trade-long" : "trade-card trade-short"} key={trade.trade_id ?? index}>
            <div className="trade-head"><div><b>{shortPair(trade.pair)}</b><span>{clean(trade.strategy)}</span></div><span className={dir === "long" ? "direction long" : "direction short"}>{dir.toUpperCase()}</span></div>
            <div className="trade-prices">
              <div><span>ENTRY</span><b>{fmtPrice(entry)}</b></div>
              <div><span>SL</span><b>{fmtPrice(stop)}</b></div>
              <div><span>TP1 · 1.5R</span><b>{fmtPrice(tp1)}</b></div>
              <div><span>TP2</span><b>{fmtPrice(target)}</b></div>
            </div>
            <div className="trade-foot"><span>RESEARCH · PAPER ONLY</span><b>{Number.isFinite(Number(trade.reward_risk)) ? Number(trade.reward_risk).toFixed(2) + "R" : "—"}</b></div>
            <p>{trade.reason ?? trade.thesis_reason ?? "Confirmed 4H structure aligned with the active higher-timeframe thesis."}</p>
          </article>;
        })}
      </div> : <div className="empty-card"><span className="empty-symbol">—</span><div><b>No open paper trades</b><p>FRIDAY will show a position here only after a valid, aligned 4H entry and structural risk plan.</p></div></div>}
    </section>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">03 / WAITING FOR TRIGGER</span><h2>WATCHLIST</h2><p>Active weekly and daily thesis; waiting for a valid 4H trigger. No entry has been recorded.</p></div><span className="section-count">{watchlist.length} WATCHING</span></div>
      {watchlist.length ? <div className="watchlist">
        {watchlist.map(card => <article className="watch-row" key={card.pair}>
          <div className="watch-token"><b>{shortPair(card.pair)}</b><span className={isBull(card.thesis?.direction) ? "direction long" : "direction short"}>{isBull(card.thesis?.direction) ? "LONG BIAS" : "SHORT BIAS"}</span></div>
          <div><span>WEEKLY THESIS</span><b>{clean(card.thesis?.reason ?? (isBull(card.weeklyTrend) ? "Bullish EMA 8/21" : "Bearish EMA 8/21"))}</b></div>
          <div><span>STATUS</span><b>WAITING FOR 4H TRIGGER</b></div>
          <div><span>ENTRY</span><b>—</b></div>
        </article>)}
      </div> : <div className="empty-card"><span className="empty-symbol">◎</span><div><b>No active thesis waiting for a trigger</b><p>Inactive, vetoed, expired and invalidated states are kept out of the main dashboard.</p></div></div>}
    </section>

    <section className="section-block">
      <details className="debug-details">
        <summary>DEBUG · Scan trace, AI errors and invalidations</summary>
        <div className="debug-grid">
          <div><span>DATA LINK</span><b>{data.connected ? "ONLINE" : clean(data.status).toUpperCase()}</b></div>
          <div><span>LAST RUN</span><b>{clean(latestRun?.status).toUpperCase()}</b></div>
          <div><span>MARKETS SCANNED</span><b>{latestRun?.pairs_checked ?? pairScans.length}</b></div>
          <div><span>AI REVIEWS OK</span><b>{pairScans.filter(scan => scan.ai_review_status === "ok").length} / {pairScans.length}</b></div>
          <div><span>LAST REFRESH</span><b>{fmtTime(data.fetched_at)}</b></div>
        </div>
        <h3>Scan trace and errors</h3>
        <div className="table-wrap"><table><thead><tr><th>MARKET</th><th>SCAN</th><th>DECISION</th><th>AI ERROR</th><th>DETAIL</th><th>LAST CANDLE</th></tr></thead><tbody>
          {pairScans.length ? pairScans.map(scan => <tr key={scan.pair}><td><b>{shortPair(scan.pair)}</b></td><td>{clean(scan.status)}</td><td>{clean(scan.latest_setup_state)} / {clean(scan.friday_decision)}</td><td>{scan.ai_review_error ? String(scan.ai_review_error) : "—"}</td><td>{clean(scan.scan_diagnostic ?? scan.weekly_thesis?.reason)}</td><td>{fmtTime(scan.last_completed_candle_utc)}</td></tr>) : <tr><td colSpan={6} className="table-empty">No scan trace has been published yet.</td></tr>}
        </tbody></table></div>
        <h3>Invalidations and lifecycle history</h3>
        <div className="table-wrap"><table><thead><tr><th>MARKET / TIME</th><th>EVENT</th><th>DIRECTION</th><th>STATE</th><th>ENTRY REF.</th></tr></thead><tbody>
          {recentEvents.length ? recentEvents.map((event, index) => <tr key={event.event_key ?? index}><td><b>{shortPair(event.pair)}</b><small>{fmtTime(event.signal_time)}</small></td><td>{clean(event.event_type)}</td><td>{clean(event.direction)}</td><td>{clean(event.outcome)}</td><td>{fmtPrice(event.entry_reference)}</td></tr>) : <tr><td colSpan={5} className="table-empty">No lifecycle events recorded.</td></tr>}
        </tbody></table></div>
      </details>
    </section>
    <footer><span className="footer-brand">FRIDAY <i>/ RESEARCH ONLY</i></span><span>No exchange orders</span><span>Auto-refresh · 30 sec</span></footer>
  </main>;
}
