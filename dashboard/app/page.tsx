"use client";

import { useEffect, useState } from "react";

type Row = Record<string, any>;
type Payload = {
  connected: boolean; status: string; message: string;
  snapshots: Row[]; weekly_theses: Row[]; events: Row[]; runs: Row[]; alerts: Row[]; paper_trades: Row[];
  fetched_at?: string;
};
const empty: Payload = { connected: false, status: "loading", message: "Loading FRIDAY data…", snapshots: [], weekly_theses: [], events: [], runs: [], alerts: [], paper_trades: [] };
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

export default function Home() {
  const [data, setData] = useState<Payload>(empty);
  const [busy, setBusy] = useState(false);
  const [thesisFilter, setThesisFilter] = useState("ALL");

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
  const openTrades = data.paper_trades.filter(trade => String(trade.status).toUpperCase() === "OPEN" && trade.ready !== false && trade.entry != null && trade.stop_loss != null && trade.take_profit != null);
  const scanByPair = new Map<string, Row>(pairScans.map(scan => [pairName(scan.pair), scan] as [string, Row]));
  const aiStatuses = pairScans.map(scan => String(scan.ai_review_status ?? "missing").trim().toLowerCase());
  const aiReviewAttempted = aiStatuses.filter(status => !["disabled", "not_run", "missing", ""].includes(status));
  const aiReviewSucceeded = aiReviewAttempted.some(status => status === "ok");
  const aiReviewFailed = aiReviewAttempted.some(status => status !== "ok");
  const aiStatusLabel = aiReviewFailed ? "ERROR" : aiReviewSucceeded ? "ONLINE" : "DISABLED";

  // Use only persisted weekly_thesis_snapshot records from the latest completed run.
  // The records are newest-first in the payload; the run window prevents older snapshots
  // from replacing a market that was skipped or unavailable in the current run.
  const runStarted = Date.parse(String(latestRun?.started_at ?? ""));
  const runFinished = Date.parse(String(latestRun?.finished_at ?? ""));
  const thesisRecords = (data.weekly_theses ?? []).filter(record =>
    record.record_type === "weekly_thesis_snapshot" && Boolean(record.pair)
  );
  const latestRunRecords = Number.isFinite(runStarted) && Number.isFinite(runFinished)
    ? thesisRecords.filter(record => {
        const recordedAt = Date.parse(String(record.signal_time ?? ""));
        return Number.isFinite(recordedAt) && recordedAt >= runStarted && recordedAt <= runFinished;
      })
    : thesisRecords;
  const latestThesisByPair = new Map<string, Row>();
  for (const record of latestRunRecords) {
    const pair = pairName(record.pair);
    if (!latestThesisByPair.has(pair)) latestThesisByPair.set(pair, record);
  }

  const weeklyCards = Array.from(latestThesisByPair.entries()).map(([pair, record]) => {
    const thesis = payloadOf(record);
    const scan = scanByPair.get(pair);
    // Strictly display the persisted thesis values; never infer macro/wave state from EMAs.
    const macroRegime = String(thesis.macro_regime ?? "UNKNOWN").toUpperCase();
    const waveState = String(thesis.wave_state ?? "UNKNOWN").toUpperCase();
    const tradeDirection = String(thesis.direction ?? "UNKNOWN").toUpperCase();
    const thesisStatus = String(thesis.status ?? "UNKNOWN").toLowerCase();
    const waveLabel = thesis.wave_state ? `${waveState} (${tradeDirection})` : "UNKNOWN";
    const dailyAlignment = thesisStatus === "veto" ? "VETO" : (thesisStatus === "active" ? "PASS" : "UNKNOWN");
    const active = thesisStatus === "active";
    return { pair, thesis, scan, macroRegime, waveLabel, dailyAlignment, active, thesisStatus, tradeDirection };
  }).sort((a, b) => {
    const rank = (status: string) => status === "active" ? 0 : status === "veto" ? 1 : 2;
    return rank(a.thesisStatus) - rank(b.thesisStatus) || a.pair.localeCompare(b.pair);
  });

  const visibleWeeklyCards = weeklyCards.filter(card => thesisFilter === "ALL" || (thesisFilter === "ACTIVE" ? card.thesisStatus === "active" : thesisFilter === "VETO" ? card.thesisStatus === "veto" : thesisFilter === "WAITING" ? !["active", "veto"].includes(card.thesisStatus) : false));
  const watchlist = weeklyCards.filter(card =>
    card.active && !openTrades.some(trade => pairName(trade.pair) === card.pair)
  );
  const recentEvents = data.events.slice(0, 40);

  return <main className="shell">
    <header className="topbar">
      <a className="brand" href="#"><span className="brand-mark">F</span><span>FRIDAY<span className="brand-sub">RESEARCH ONLY</span></span></a>
      <div className="top-actions">
        <span className={data.connected ? "pill positive" : "pill neutral"}><i /> DATA: {data.connected ? "ONLINE" : "OFFLINE"}</span>
        <span className={aiStatusLabel === "ONLINE" ? "pill positive" : aiStatusLabel === "ERROR" ? "pill negative" : "pill neutral"}><i /> AI: {aiStatusLabel}</span>
        <button className="refresh" onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "↻ Refresh"}</button>
      </div>
    </header>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">01 / HIGHER TIMEFRAME</span><h2>WEEKLY THESIS</h2><p>Completed weekly and daily candles. The 4H engine only runs when the thesis is active.</p></div></div>
      <p className="thesis-note">All scanned markets · Completed weekly and daily candles · The 4H engine only runs when the thesis is ACTIVE.</p>
      <div className="thesis-filter-row"><div className="thesis-filter-group">{[["ALL","ALL MARKETS"],["ACTIVE","ACTIVE"],["VETO","VETOED"],["WAITING","WAITING / OTHER"]].map(([value,label]) => <button key={value} className={thesisFilter === value ? "thesis-filter active" : "thesis-filter"} onClick={() => setThesisFilter(value)}>{label}</button>)}</div><span className="thesis-scroll-hint">SCROLL ↔</span></div>
      {weeklyCards.length ? <div className="thesis-strip">
        {visibleWeeklyCards.map(card => {
          const statusLabel = card.thesisStatus === "active" ? "ACTIVE" : card.thesisStatus === "veto" ? "VETO" : card.thesisStatus === "insufficient_data" ? "INSUFFICIENT DATA" : card.thesisStatus === "unavailable" ? "UNAVAILABLE" : "INACTIVE";
          const macroBull = card.macroRegime === "BULLISH";
          const macroBear = card.macroRegime === "BEARISH";
          const dirBull = isBull(card.tradeDirection);
          const dirBear = isBear(card.tradeDirection);
          const thesis = card.thesis as Row;

          return <article className="thesis-card" key={card.pair}>
            <div className="thesis-top"><b>{shortPair(card.pair)}</b><span className={card.active ? "pill positive" : card.thesisStatus === "veto" ? "pill negative" : "pill neutral"}>{statusLabel}</span></div>
            <div className="thesis-row"><span>MACRO</span><b className={macroBull ? "positive-text" : macroBear ? "negative-text" : ""}>{card.macroRegime}</b></div>
            <div className="thesis-row"><span>WAVE</span><b className={dirBear ? "negative-text" : dirBull ? "positive-text" : ""}>{card.waveLabel}</b></div>
            <div className="thesis-row"><span>WEEKLY CLOSE</span><b>{fmtPrice(thesis.weekly_close)}</b></div>
            <div className="thesis-row"><span>DAILY CLOSE</span><b>{fmtPrice(thesis.daily_close)}</b></div>
            <div className="thesis-row"><span>DAILY ALIGNMENT</span><b className={card.dailyAlignment === "PASS" ? "positive-text" : card.dailyAlignment === "VETO" ? "negative-text" : ""}>{card.dailyAlignment}</b></div>
            <div className="thesis-bottom"><span>THESIS STATUS</span><b>{statusLabel}</b></div>
            {thesis.reason && <p className="thesis-reason">{String(thesis.reason)}</p>}
          </article>;
        })}
      </div> : <div className="empty-card"><span className="empty-symbol">—</span><div><b>No weekly thesis snapshots in the latest run</b><p>Cards appear here when FRIDAY persists a weekly_thesis_snapshot for a market.</p></div></div>}
    </section>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">02 / ACTIVE POSITIONS</span><h2>OPEN PAPER TRADES</h2><p>Only open paper positions appear here. No exchange orders are placed.</p></div><span className="section-count">{openTrades.length} OPEN</span></div>
      {openTrades.length ? <div className="trade-grid">
        {openTrades.map((trade, index) => {
          const dir = direction(trade.direction);
          const entry = Number(trade.entry);
          const stop = Number(trade.stop_loss);
          const target = Number(trade.tp2 ?? trade.take_profit);
          const risk = Math.abs(entry - stop);
          const tp1 = Number(trade.tp1 ?? (dir === "long" ? entry + risk * 1.5 : entry - risk * 1.5));
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
          <div className="watch-token"><b>{shortPair(card.pair)}</b><span className={isBull(card.tradeDirection) ? "direction long" : "direction short"}>{isBull(card.tradeDirection) ? "LONG BIAS" : "SHORT BIAS"}</span></div>
          <div><span>WEEKLY THESIS</span><b>{clean(card.thesis?.reason ?? "No reason recorded")}</b></div>
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
          <div><span>LAST RUN</span><b>{["success", "scan_complete"].includes(String(latestRun?.status ?? "").toLowerCase()) ? "SCAN COMPLETE" : String(latestRun?.status ?? "").toLowerCase() === "scan_complete_ai_unavailable" ? "SCAN COMPLETE · AI ERROR" : clean(latestRun?.status).toUpperCase()}</b></div>
          <div><span>MARKETS SCANNED</span><b>{latestRun?.pairs_checked ?? pairScans.length}</b></div>
          <div><span>AI REVIEWS OK</span><b>{pairScans.filter(scan => scan.ai_review_status === "ok").length} / {aiReviewAttempted.length}</b></div>
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
