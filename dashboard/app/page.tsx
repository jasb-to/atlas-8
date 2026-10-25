"use client";

import { useEffect, useMemo, useState } from "react";

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
  const [view, setView] = useState<"overview" | "debug">("overview");

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
  const closedTrades = data.paper_trades.filter(trade => String(trade.status).toUpperCase() === "CLOSED").slice(0, 6);
  const scanByPair = new Map<string, Row>(pairScans.map(scan => [pairName(scan.pair), scan] as [string, Row]));
  const aiQuotaError = pairScans.some(scan => /402|payment required|quota exceeded/i.test(String(scan.ai_review_error ?? "")));
  const aiReviewActive = pairScans.some(scan => String(scan.ai_review_status ?? "").toLowerCase() === "ok");
  const aiProvider = pairScans.find(scan => scan.ai_review_status === "ok" && (scan.ai_review_provider || scan.ai_provider))?.ai_review_provider ?? pairScans.find(scan => scan.ai_review_status === "ok" && scan.ai_provider)?.ai_provider;
  const weeklyCards = WATCH_PAIRS.map(pair => {
    const thesis = thesisFor(data.weekly_theses ?? [], pair) ?? thesisFor(snapshots, pair);
    const scan = scanByPair.get(pair);
    const weeklyTrend = thesis?.weekly_trend ?? thesis?.weekly_status ?? thesis?.weekly_direction ??
      (thesis?.weekly_close != null && thesis?.weekly_sma != null
        ? Number(thesis.weekly_close) > Number(thesis.weekly_sma) ? "bullish" : "bearish"
        : undefined);
    const dailyTrend = thesis?.daily_alignment ?? thesis?.daily_status ?? thesis?.daily_direction ??
      (thesis?.daily_close != null && thesis?.daily_sma != null &&
       thesis?.daily_stoch_k != null && thesis?.daily_stoch_d != null
        ? Number(thesis.daily_close) > Number(thesis.daily_sma) && Number(thesis.daily_stoch_k) > Number(thesis.daily_stoch_d)
          ? "bullish"
          : Number(thesis.daily_close) < Number(thesis.daily_sma) && Number(thesis.daily_stoch_k) < Number(thesis.daily_stoch_d)
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
  const watchlist = useMemo(() => {
    return data.events.filter(event => {
      const pair = pairName(event.pair);
      const thesis = thesisFor(data.weekly_theses ?? [], pair) ?? thesisFor(snapshots, pair);
      const outcome = String(event.outcome ?? "").toLowerCase();
      const open = openTrades.some(trade => pairName(trade.pair) === pair);
      return thesis?.status === "active" && !open &&
        ["pending", "candidate", "active", "open", "retest_held_waiting_continuation", "entry_probe_waiting_retest"].includes(outcome);
    }).slice(0, 8);
  }, [data.events, data.weekly_theses, snapshots, openTrades]);
  const recentEvents = data.events.slice(0, 40);
  const lastScan = latestRun?.finished_at ?? latestRun?.started_at;
  const headerDate = new Date().toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short"
  }).toUpperCase();

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#"><span className="brand-mark">F</span><span>FRIDAY<span className="brand-sub">RESEARCH INTELLIGENCE</span></span></a>
      <div className="top-actions">
        <span className="mode-chip"><i /> RESEARCH ONLY · NO LIVE ORDERS</span>
        <span className={aiQuotaError ? "ai-status ai-error" : "ai-status"}><i /> {aiQuotaError ? "AI OFFLINE" : aiReviewActive ? `AI: ${String(aiProvider ?? "ONLINE").toUpperCase()}` : "AI OFFLINE"}</span>
        <button className="refresh" onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "↻ Refresh"}</button>
      </div>
    </header>

    <nav className="view-tabs" aria-label="Dashboard views">
      <button className={view === "overview" ? "tab active" : "tab"} onClick={() => setView("overview")}>Overview</button>
      <button className={view === "debug" ? "tab active" : "tab"} onClick={() => setView("debug")}>Debug</button>
      <span className="header-date">{headerDate}</span>
    </nav>

    {view === "overview" ? <>
      <section className="hero">
        <div><span className="eyebrow">THE MARKET, WITHOUT THE NOISE</span><h1>Thesis first.<br /><em>Execution second.</em></h1><p>Completed-candle research · weekly context · 4H structure · paper trades only.</p></div>
        <div className="hero-state"><span className={data.connected ? "led on" : "led"} /><div><b>{data.connected ? "DATA LINK CONNECTED" : "DATA LINK NOT VERIFIED"}</b><small>{data.connected ? `Last scan ${fmtTime(lastScan)}` : data.message}</small></div></div>
      </section>

      <section className="section-block">
        <div className="section-heading"><div><span className="eyebrow">01 / BIG PICTURE</span><h2>Weekly thesis</h2><p>Only completed weekly and daily candles count. Missing thesis data is shown as unavailable—not guessed.</p></div><span className="section-count">{weeklyCards.filter(card => card.active).length} ACTIVE</span></div>
        <div className="thesis-grid">
          {weeklyCards.map(card => {
            const trend = card.weeklyTrend ?? "Not evaluated";
            const daily = card.dailyTrend ?? "Not evaluated";
            const status = card.thesis?.status ?? "awaiting weekly data";
            const price = card.scan?.last_close;
            return <article className="thesis-card" key={card.pair}>
              <div className="thesis-top"><b>{shortPair(card.pair)}</b><span className={card.active ? "pill positive" : "pill neutral"}>{clean(status)}</span></div>
              <div className="thesis-row"><span>WEEKLY TREND</span><b className={isBull(trend) ? "positive-text" : isBear(trend) ? "negative-text" : ""}>{clean(trend)}</b></div>
              <div className="thesis-row"><span>WEEKLY MOMENTUM</span><b className={card.thesis?.weekly_stoch_aligned === true ? "positive-text" : card.thesis?.weekly_stoch_aligned === false ? "negative-text" : ""}>{card.weeklyMomentum}</b></div>
              <div className="thesis-row"><span>DAILY ALIGNMENT</span><b className={isBull(daily) ? "positive-text" : isBear(daily) ? "negative-text" : ""}>{clean(daily)}</b></div>
              <div className="thesis-bottom"><span>LAST PRICE</span><b>{fmtPrice(price)}</b></div>
              <div className={card.active ? "thesis-status ready" : "thesis-status"}>{card.active ? "READY FOR 4H EXECUTION" : card.thesis?.status ? "WAITING FOR ALIGNMENT" : "WEEKLY FEED NOT CONNECTED"}</div>
            </article>;
          })}
        </div>
      </section>

      <section className="section-block">
        <div className="section-heading"><div><span className="eyebrow">02 / SCOREBOARD</span><h2>Open paper trades</h2><p>Risk-defined positions only. Directional border colour is not a live order status.</p></div><span className="section-count">{openTrades.length} OPEN</span></div>
        {openTrades.length ? <div className="trade-grid">
          {openTrades.map((trade, index) => {
            const dir = direction(trade.direction);
            const risk = Math.abs(Number(trade.entry) - Number(trade.stop_loss));
            const reward = dir === "long" ? Number(trade.take_profit) - Number(trade.entry) : Number(trade.entry) - Number(trade.take_profit);
            const rr = risk > 0 && Number.isFinite(reward) ? reward / risk : Number(trade.reward_risk);
            return <article className={`trade-card ${dir === "long" ? "trade-long" : "trade-short"}`} key={trade.trade_id ?? index}>
              <div className="trade-head"><div><b>{shortPair(trade.pair)}</b><span>{clean(trade.strategy)}</span></div><span className={dir === "long" ? "direction long" : "direction short"}>{dir.toUpperCase()}</span></div>
              <div className="trade-prices"><div><span>ENTRY</span><b>{fmtPrice(trade.entry)}</b></div><div><span>STOP</span><b>{fmtPrice(trade.stop_loss)}</b></div><div><span>TARGET</span><b>{fmtPrice(trade.take_profit)}</b></div></div>
              <div className="trade-foot"><span>MONITORING · PAPER ONLY</span><b>{Number.isFinite(rr) ? rr.toFixed(2) + "R" : "—"} R:R</b></div>
              <p>{trade.reason ?? trade.thesis_reason ?? "Deterministic structure and risk plan recorded. Thesis explanation is not persisted for this trade."}</p>
            </article>;
          })}
        </div> : <div className="empty-card"><span className="empty-symbol">—</span><div><b>No open paper trades</b><p>Nothing is currently being monitored as an open paper position. A quiet scoreboard is preferable to invented trades.</p></div></div>}
      </section>

      <section className="section-block">
        <div className="section-heading"><div><span className="eyebrow">03 / ACTION CENTER</span><h2>Watchlist</h2><p>Weekly-approved candidates waiting for the original 4H break/retest lifecycle to complete.</p></div><span className="section-count">{watchlist.length} WATCHING</span></div>
        {watchlist.length ? <div className="watchlist">
          {watchlist.map((event, index) => <article className="watch-row" key={event.event_key ?? index}>
            <div className="watch-token"><b>{shortPair(event.pair)}</b><span className={direction(event.direction) === "long" ? "direction long" : "direction short"}>{String(event.direction ?? "—").toUpperCase()}</span></div>
            <div><span>THESIS</span><b>{clean(thesisFor(snapshots, pairName(event.pair))?.direction ?? "Active")}</b></div>
            <div><span>WAITING FOR</span><b>{String(event.outcome ?? "").toLowerCase().includes("retest") ? "4H continuation confirmation (Entry 2)" : "4H trendline break / retest (Entry 1)"}</b></div>
            <div><span>REFERENCE</span><b>{fmtPrice(event.entry_reference ?? event.line_price)}</b></div>
            <div><span>LAST EVENT</span><b>{fmtTime(event.signal_time)}</b></div>
          </article>)}
        </div> : <div className="empty-card"><span className="empty-symbol">◎</span><div><b>No weekly-approved setups waiting</b><p>There is no persisted active weekly thesis attached to a waiting 4H setup. Candidates will appear here when both records exist.</p></div></div>}
      </section>

    </> : <>
      <details className="section-block debug-page debug-section"><summary>🔧 Debug: Scan Trace & Diagnostics</summary>
        <div className="section-heading"><div><span className="eyebrow">DIAGNOSTICS / HIDDEN FROM OVERVIEW</span><h2>Debug & scan evidence</h2><p>Operational details remain available here without overwhelming the scoreboard.</p></div></div>
        <div className="debug-grid">
          <div><span>DATA LINK</span><b className={data.connected ? "positive-text" : "negative-text"}>{data.connected ? "CONNECTED" : clean(data.status).toUpperCase()}</b></div>
          <div><span>LAST RUN</span><b>{clean(latestRun?.status).toUpperCase()}</b></div>
          <div><span>MARKETS SCANNED</span><b>{latestRun?.pairs_checked ?? pairScans.length}</b></div>
          <div><span>AI REVIEWS OK</span><b>{pairScans.filter(scan => scan.ai_review_status === "ok").length} / {pairScans.length}</b></div>
          <div><span>ALERT RECORDS</span><b>{data.alerts.length}</b></div>
          <div><span>LAST REFRESH</span><b>{fmtTime(data.fetched_at)}</b></div>
        </div>
        <h3>Latest scan trace</h3>
        <div className="table-wrap"><table><thead><tr><th>MARKET</th><th>SCAN</th><th>SETUP / DECISION</th><th>AI REVIEW</th><th>ALERT OUTCOME</th><th>LAST CANDLE</th></tr></thead><tbody>
          {pairScans.length ? pairScans.map(scan => <tr key={scan.pair}><td><b>{shortPair(scan.pair)}</b></td><td>{clean(scan.status)}</td><td>{clean(scan.latest_setup_state)} / {clean(scan.friday_decision)}</td><td>{clean(scan.ai_review_status)}{scan.ai_review_error ? <small>{String(scan.ai_review_error)}</small> : null}</td><td>{Array.isArray(scan.alert_results) ? scan.alert_results.map((item: Row) => `${clean(item.type)}: ${clean(item.status)}${item.reason ? " — " + clean(item.reason) : ""}`).join(" · ") : clean(scan.scan_diagnostic)}</td><td>{fmtTime(scan.last_completed_candle_utc)}</td></tr>) : <tr><td colSpan={6} className="table-empty">No scan trace has been published yet.</td></tr>}
        </tbody></table></div>
        <h3>Recent lifecycle events</h3>
        <div className="table-wrap"><table><thead><tr><th>MARKET / TIME</th><th>EVENT</th><th>DIRECTION</th><th>STATE</th><th>ENTRY REF.</th></tr></thead><tbody>
          {recentEvents.length ? recentEvents.map((event, index) => <tr key={event.event_key ?? index}><td><b>{shortPair(event.pair)}</b><small>{fmtTime(event.signal_time)}</small></td><td>{clean(event.event_type)}</td><td>{clean(event.direction)}</td><td>{clean(event.outcome)}</td><td>{fmtPrice(event.entry_reference)}</td></tr>) : <tr><td colSpan={5} className="table-empty">No lifecycle events recorded.</td></tr>}
        </tbody></table></div>
      </details>
    </>}
    <footer><span className="footer-brand">FRIDAY <i>/ RESEARCH CONTROL ROOM</i></span><span>Research only · No exchange orders</span><span>Auto-refresh · 30 sec</span></footer>
  </main>;
}
