"use client";

import React, { Fragment, useEffect, useState } from "react";

type Row = Record<string, any>;
type Payload = {
  connected: boolean; status: string; message: string;
  snapshots: Row[]; weekly_theses: Row[]; events: Row[]; runs: Row[]; alerts: Row[]; paper_trades: Row[];
  fetched_at?: string;
  api_fetched_at?: string;
};
const empty: Payload = { connected: false, status: "loading", message: "Loading FRIDAY data…", snapshots: [], weekly_theses: [], events: [], runs: [], alerts: [], paper_trades: [] };
const fmtTime = (value?: string) => value ? new Date(value).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" }) : "—";
const fmtPrice = (value: unknown) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)) ? Number(value).toLocaleString("en-GB", { maximumFractionDigits: 6 }) : "—";
const payloadOf = (row?: Row) => row?.payload && typeof row.payload === "object" ? row.payload : {};
const clean = (value: unknown) => String(value ?? "—").replaceAll("_", " ");
const pairName = (value: unknown) => {
  const pair = String(value ?? "").toUpperCase();
  if (pair === "XBTUSDT") return "BTCUSDT";
  if (pair === "XBTUSD" || pair === "BTCUSD") return "BTCUSD";
  return pair;
};
const shortPair = (value: unknown) => pairName(value).replace(/USDT$/, "");
const THESIS_UNIVERSE = [
  "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "BNBUSDT", "VIRTUALUSDT",
  "HYPEUSDT", "DOGEUSDT", "LINKUSDT", "ZECUSDT", "NEARUSDT", "SUIUSDT", "UNIUSDT",
];
const direction = (value: unknown) => String(value ?? "").toLowerCase();
const isBull = (value: unknown) => ["long", "bullish", "active_long"].includes(direction(value));
const isBear = (value: unknown) => ["short", "bearish", "active_short"].includes(direction(value));

export default function Home() {
  const [data, setData] = useState<Payload>(empty);
  const [busy, setBusy] = useState(false);
  const [expandedThesis, setExpandedThesis] = useState<string | null>(null);
  const [showInsufficient, setShowInsufficient] = useState(false);

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
  const openTrades = data.paper_trades.filter(trade => ["OPEN", "ACTIVE"].includes(String(trade.status ?? "").trim().toUpperCase()) && trade.ready !== false && trade.entry != null && (trade.stop_loss ?? trade.stop) != null && (trade.tp2 ?? trade.take_profit ?? trade.target) != null);
  const closedTrades = data.paper_trades.filter(trade => String(trade.status ?? "").trim().toUpperCase() === "CLOSED").sort((a, b) => Date.parse(String(b.exit_time ?? b.last_checked_at ?? "")) - Date.parse(String(a.exit_time ?? a.last_checked_at ?? "")));
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

  const weeklyCards = THESIS_UNIVERSE.map(pair => {
    const record = latestThesisByPair.get(pair);
    const thesis = payloadOf(record);
    const scan = scanByPair.get(pair);
    const macroRegime = String(thesis.macro_regime ?? "UNKNOWN").toUpperCase();
    const waveState = String(thesis.wave_state ?? "UNKNOWN").toUpperCase();
    const tradeDirection = String(thesis.direction ?? "UNKNOWN").toUpperCase();
    const persistedStatus = record ? String(thesis.status ?? "UNKNOWN").toLowerCase() : "no_snapshot";
    const scanText = JSON.stringify(scan ?? {}).toLowerCase();
    const thesisText = JSON.stringify(thesis ?? {}).toLowerCase();
    const conflict = persistedStatus === "conflict_wait" || persistedStatus === "conflict" || scanText.includes("conflict_wait") || scanText.includes("daily_weekly_conflict") || thesisText.includes("conflict_wait");
    const btcImpulseBlocked = pair !== "BTCUSDT" && scanText.includes("btc_impulse_bullish_block");
    const thesisStatus = conflict ? "conflict_wait" : persistedStatus;
    const waveLabel = thesis.wave_state ? `${waveState} · ${tradeDirection}` : "—";
    const dailyAlignment = conflict ? "CONFLICT" : thesisStatus === "veto" ? "VETO" : (thesisStatus === "active" ? "PASS" : "—");
    const active = thesisStatus === "active";
    const recordType = String(record?.record_type ?? "MISSING");
    const sourceWeekly = String(thesis.data_source_weekly ?? "MISSING");
    const sourceDaily = String(thesis.data_source_daily ?? "MISSING");
    const quoteCurrency = String(thesis.quote_currency ?? (sourceWeekly.includes("(USD)") ? "USD" : "USDT")).toUpperCase();
    const weeklyClose = thesis.weekly_close;
    const dailyClose = thesis.daily_close;
    const statusGroup = conflict ? "conflict" : thesisStatus === "active" ? "active" : thesisStatus === "veto" ? "veto" : "insufficient";
    const openTrade = data.paper_trades.find(trade => pairName(trade.pair) === pair && ["OPEN", "ACTIVE"].includes(String(trade.status ?? "").toUpperCase()));
    const action = btcImpulseBlocked ? "SKIPPED (BTC IMPULSE BULLISH)" : conflict ? "SKIPPED" : statusGroup === "veto" ? "SKIPPED" : openTrade ? String(openTrade.direction ?? "").toUpperCase() : "WAITING";
    return { pair, thesis, scan, recordType, sourceWeekly, sourceDaily, quoteCurrency, weeklyClose, dailyClose, macroRegime, waveLabel, dailyAlignment, active, thesisStatus, tradeDirection, statusGroup, openTrade, action, conflict, btcImpulseBlocked };
  }).sort((a, b) => {
    const rank = (status: string) => status === "active" ? 0 : status === "veto" ? 1 : 2;
    return rank(a.thesisStatus) - rank(b.thesisStatus) || a.pair.localeCompare(b.pair);
  });
  const activeTheses = weeklyCards.filter(card => card.statusGroup === "active");
  const vetoedTheses = weeklyCards.filter(card => card.statusGroup === "veto");
  const conflictTheses = weeklyCards.filter(card => card.statusGroup === "conflict");
  const insufficientTheses = weeklyCards.filter(card => card.statusGroup === "insufficient");


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
      <div className="section-heading"><div><span className="eyebrow">01 / HIGHER TIMEFRAME</span><h2>WEEKLY THESIS</h2><p>One row per market · completed weekly and daily candles · click a symbol for the underlying details.</p></div></div>
      <p className="thesis-note">Active theses first · conflicts and vetoes next · insufficient or unavailable data collapsed below.</p>
      <div className="thesis-list-wrap">
        <table className="thesis-list">
          <thead><tr><th>SYMBOL</th><th>MACRO</th><th>WAVE</th><th>DAILY</th><th>STATUS</th><th>ACTION</th></tr></thead>
          <tbody>
            <tr className="list-section"><th colSpan={6}>ACTIVE <span>{activeTheses.length}</span></th></tr>
            {activeTheses.map(card => {
              const open = expandedThesis === card.pair;
              return <Fragment key={card.pair}>
                <tr className="thesis-list-row">
                  <td><button className="symbol-expander" aria-expanded={open} onClick={() => setExpandedThesis(open ? null : card.pair)}><span className="status-dot active-dot">●</span><b>{shortPair(card.pair)}</b><span className="expand-mark">{open ? "−" : "+"}</span></button></td>
                  <td className={card.macroRegime === "BULLISH" ? "positive-text" : card.macroRegime === "BEARISH" ? "negative-text" : ""}>{["BULLISH","BEARISH"].includes(card.macroRegime) ? card.macroRegime : "—"}</td>
                  <td>{card.waveLabel === "UNKNOWN" ? "—" : card.waveLabel}</td>
                  <td className="positive-text">{card.dailyAlignment}</td><td><span className="status-chip active-chip">ACTIVE</span></td><td>{card.btcImpulseBlocked ? "SKIPPED (BTC IMPULSE BULLISH)" : card.action}</td>
                </tr>
                {open && <tr className="thesis-detail-row"><td colSpan={6}><div className="thesis-detail-grid">
                  <div><span>WEEKLY CLOSE</span><b>{fmtPrice(card.weeklyClose)} {card.quoteCurrency}</b></div><div><span>DAILY CLOSE</span><b>{fmtPrice(card.dailyClose)} {card.quoteCurrency}</b></div>
                  <div><span>SOURCE W / D</span><b>{clean(card.sourceWeekly)} / {clean(card.sourceDaily)}</b></div><div><span>RECORD TYPE</span><b>{clean(card.recordType)}</b></div>
                  <div className="detail-reason"><span>REASON</span><b>{String(card.thesis.reason ?? "No reason recorded.")}</b></div>
                </div></td></tr>}
              </Fragment>;
            })}
            <tr className="list-section"><th colSpan={6}>CONFLICT <span>{conflictTheses.length}</span></th></tr>
            {conflictTheses.map(card => {
              const open = expandedThesis === card.pair;
              return <Fragment key={card.pair}>
                <tr className="thesis-list-row conflict-row">
                  <td><button className="symbol-expander" aria-expanded={open} onClick={() => setExpandedThesis(open ? null : card.pair)}><span className="status-dot veto-dot">●</span><b>{shortPair(card.pair)}</b><span className="expand-mark">{open ? "−" : "+"}</span></button></td>
                  <td className={card.macroRegime === "BULLISH" ? "positive-text" : card.macroRegime === "BEARISH" ? "negative-text" : ""}>{["BULLISH","BEARISH"].includes(card.macroRegime) ? card.macroRegime : "—"}</td>
                  <td>{card.waveLabel === "UNKNOWN" ? "—" : card.waveLabel}</td><td className="negative-text">CONFLICT</td><td><span className="status-chip veto-chip">CONFLICT_WAIT</span></td><td>{card.btcImpulseBlocked ? "SKIPPED (BTC IMPULSE BULLISH)" : "SKIPPED"}</td>
                </tr>
                {open && <tr className="thesis-detail-row"><td colSpan={6}><div className="thesis-detail-grid">
                  <div><span>WEEKLY CLOSE</span><b>{fmtPrice(card.weeklyClose)} {card.quoteCurrency}</b></div><div><span>DAILY CLOSE</span><b>{fmtPrice(card.dailyClose)} {card.quoteCurrency}</b></div>
                  <div><span>SOURCE W / D</span><b>{clean(card.sourceWeekly)} / {clean(card.sourceDaily)}</b></div><div><span>RECORD TYPE</span><b>{clean(card.recordType)}</b></div>
                  <div className="detail-reason"><span>REASON</span><b>{String(card.thesis.reason ?? card.scan?.scan_diagnostic ?? "Daily momentum conflicts with the weekly thesis.")}</b></div>
                </div></td></tr>}
              </Fragment>;
            })}
            <tr className="list-section"><th colSpan={6}>VETOED <span>{vetoedTheses.length}</span></th></tr>
            {vetoedTheses.map(card => {
              const open = expandedThesis === card.pair;
              return <React.Fragment key={card.pair}>
                <tr className="thesis-list-row">
                  <td><button className="symbol-expander" aria-expanded={open} onClick={() => setExpandedThesis(open ? null : card.pair)}><span className="status-dot veto-dot">●</span><b>{shortPair(card.pair)}</b><span className="expand-mark">{open ? "−" : "+"}</span></button></td>
                  <td className={card.macroRegime === "BULLISH" ? "positive-text" : card.macroRegime === "BEARISH" ? "negative-text" : ""}>{["BULLISH","BEARISH"].includes(card.macroRegime) ? card.macroRegime : "—"}</td>
                  <td>{card.waveLabel === "UNKNOWN" ? "—" : card.waveLabel}</td><td className="negative-text">{card.dailyAlignment}</td><td><span className="status-chip veto-chip">VETO</span></td><td>SKIPPED</td>
                </tr>
                {open && <tr className="thesis-detail-row"><td colSpan={6}><div className="thesis-detail-grid">
                  <div><span>WEEKLY CLOSE</span><b>{fmtPrice(card.weeklyClose)} {card.quoteCurrency}</b></div><div><span>DAILY CLOSE</span><b>{fmtPrice(card.dailyClose)} {card.quoteCurrency}</b></div>
                  <div><span>SOURCE W / D</span><b>{clean(card.sourceWeekly)} / {clean(card.sourceDaily)}</b></div><div><span>RECORD TYPE</span><b>{clean(card.recordType)}</b></div>
                  <div className="detail-reason"><span>REASON</span><b>{String(card.thesis.reason ?? "No reason recorded.")}</b></div>
                </div></td></tr>}
              </React.Fragment>;
            })}
          </tbody>
        </table>
        <details className="insufficient-details" open={showInsufficient} onToggle={(event) => setShowInsufficient((event.currentTarget as HTMLDetailsElement).open)}>
          <summary>INSUFFICIENT / UNAVAILABLE <span>{insufficientTheses.length}</span></summary>
          <div className="table-wrap"><table className="thesis-list"><thead><tr><th>SYMBOL</th><th>MACRO</th><th>WAVE</th><th>DAILY</th><th>STATUS</th><th>ACTION</th></tr></thead><tbody>
            {insufficientTheses.map(card => {
              const open = expandedThesis === card.pair;
              const status = card.thesisStatus === "insufficient_data" ? "INSUFFICIENT" : card.thesisStatus === "unavailable" ? "UNAVAILABLE" : "UNAVAILABLE";
              return <React.Fragment key={card.pair}>
                <tr className="thesis-list-row">
                  <td><button className="symbol-expander" aria-expanded={open} onClick={() => setExpandedThesis(open ? null : card.pair)}><span className="status-dot inactive-dot">○</span><b>{shortPair(card.pair)}</b><span className="expand-mark">{open ? "−" : "+"}</span></button></td><td>—</td><td>—</td><td>—</td><td><span className="status-chip neutral-chip">{status}</span></td><td>—</td>
                </tr>
                {open && <tr className="thesis-detail-row"><td colSpan={6}><div className="thesis-detail-grid">
                  <div><span>WEEKLY CLOSE</span><b>{fmtPrice(card.weeklyClose)} {card.quoteCurrency}</b></div><div><span>DAILY CLOSE</span><b>{fmtPrice(card.dailyClose)} {card.quoteCurrency}</b></div>
                  <div><span>SOURCE W / D</span><b>{clean(card.sourceWeekly)} / {clean(card.sourceDaily)}</b></div><div><span>RECORD TYPE</span><b>{clean(card.recordType)}</b></div>
                  <div className="detail-reason"><span>REASON</span><b>{String(card.thesis.reason ?? "No snapshot persisted for this market in the latest scan.")}</b></div>
                </div></td></tr>}
              </React.Fragment>;
            })}
          </tbody></table></div>
        </details>
      </div>
    </section>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">02 / ACTIVE POSITIONS</span><h2>OPEN PAPER TRADES</h2><p>Only open paper positions appear here. No exchange orders are placed.</p></div><span className="section-count">{openTrades.length} OPEN</span></div>
      {openTrades.length ? <div className="trade-list-wrap"><table className="trade-list"><thead><tr><th>SYMBOL</th><th>DIRECTION</th><th>ENTRY</th><th>SL</th><th>TP1</th><th>TP2</th><th>R:R</th><th>STATUS</th></tr></thead><tbody>
        {openTrades.map((trade, index) => {
          const dir = direction(trade.direction);
          const entry = Number(trade.entry);
          const stop = Number(trade.stop_loss ?? trade.stop);
          const target = Number(trade.tp2 ?? trade.take_profit ?? trade.target);
          const risk = Math.abs(entry - stop);
          const tp1 = Number(trade.tp1 ?? (dir === "long" ? entry + risk * 1.5 : entry - risk * 1.5));
          return <tr key={trade.trade_id ?? index}><td><b>{shortPair(trade.pair)}</b></td><td className={dir === "long" ? "positive-text" : "negative-text"}>{dir.toUpperCase()}</td><td>{fmtPrice(entry)}</td><td>{fmtPrice(stop)}</td><td>{fmtPrice(tp1)}</td><td>{fmtPrice(target)}</td><td>{Number.isFinite(Number(trade.reward_risk)) ? Number(trade.reward_risk).toFixed(2) + "R" : "—"}</td><td><span className="status-chip active-chip">OPEN</span></td></tr>;
        })}
      </tbody></table></div> : <div className="empty-card"><span className="empty-symbol">—</span><div><b>No open paper trades</b><p>FRIDAY will show a position here only after a valid, aligned 4H entry and structural risk plan.</p></div></div>}

    </section>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">03 / COMPLETED POSITIONS</span><h2>CLOSED PAPER TRADES</h2><p>Historical paper-trade outcomes, including stop-losses and thesis invalidations. No exchange orders are placed.</p></div><span className="section-count">{closedTrades.length} CLOSED</span></div>
      {closedTrades.length ? <div className="trade-grid">
        {closedTrades.map((trade, index) => {
          const dir = direction(trade.direction);
          const entry = Number(trade.entry);
          const stop = Number(trade.stop_loss ?? trade.stop);
          const target = Number(trade.tp2 ?? trade.take_profit ?? trade.target);
          const risk = Math.abs(entry - stop);
          const tp1 = Number(trade.tp1 ?? (dir === "long" ? entry + risk * 1.5 : entry - risk * 1.5));
          const realizedR = Number(trade.realized_r_multiple);
          return <article className="trade-card trade-closed" key={trade.trade_id ?? index}>
            <div className="trade-head"><div><b>{shortPair(trade.pair)}</b><span>{clean(trade.strategy)} · {clean(trade.timeframe ?? "4H")}</span></div><span className="direction">{clean(trade.status)}</span></div>
            <div className="trade-prices">
              <div><span>ENTRY</span><b>{fmtPrice(entry)}</b></div>
              <div><span>EXIT</span><b>{fmtPrice(trade.exit_price)}</b></div>
              <div><span>SL</span><b>{fmtPrice(stop)}</b></div>
              <div><span>TP1 · 1.5R</span><b>{fmtPrice(tp1)}</b></div>
              <div><span>TP2</span><b>{fmtPrice(target)}</b></div>
              <div><span>REALIZED R</span><b>{Number.isFinite(realizedR) ? (realizedR > 0 ? "+" : "") + realizedR.toFixed(2) + "R" : "—"}</b></div>
            </div>
            <div className="trade-foot"><span>EXIT · {fmtTime(trade.exit_time)}</span><b>{clean(trade.exit_reason ?? "CLOSED")}</b></div>
            <p>{trade.invalidation_reason ?? trade.reason ?? trade.thesis_reason ?? "Paper trade closed; see exit reason and realized R above."}</p>
          </article>;
        })}
      </div> : <div className="empty-card"><span className="empty-symbol">—</span><div><b>No closed paper trades yet</b><p>Closed trades will remain visible here with their exit reason and realized R multiple.</p></div></div>}
    </section>

    <section className="section-block">
      <div className="section-heading"><div><span className="eyebrow">04 / WAITING FOR TRIGGER</span><h2>WATCHLIST</h2><p>Active weekly and daily thesis; waiting for a valid 4H trigger. No entry has been recorded.</p></div><span className="section-count">{watchlist.length} WATCHING</span></div>
      {watchlist.length ? <div className="watchlist-list-wrap"><table className="watchlist-list"><thead><tr><th>SYMBOL</th><th>BIAS</th><th>WAITING FOR</th></tr></thead><tbody>
        {watchlist.map(card => <tr key={card.pair}><td><b>{shortPair(card.pair)}</b></td><td className={isBull(card.tradeDirection) ? "positive-text" : "negative-text"}>{isBull(card.tradeDirection) ? "LONG" : isBear(card.tradeDirection) ? "SHORT" : "—"}</td><td>{card.conflict ? "SKIPPED — DAILY/WEEKLY CONFLICT" : card.btcImpulseBlocked ? "SKIPPED — BTC IMPULSE BULLISH" : "WAITING FOR 4H TRIGGER"}</td></tr>)}
      </tbody></table></div> : <div className="empty-card"><span className="empty-symbol">◎</span><div><b>No active thesis waiting for a trigger</b><p>Inactive, vetoed, expired and invalidated states are kept out of the main dashboard.</p></div></div>}

    </section>

    <section className="section-block">
      <details className="debug-details">
        <summary>DEBUG · Scan trace, AI errors and invalidations</summary>
        <div className="debug-grid">
          <div><span>DATA LINK</span><b>{data.connected ? "ONLINE" : clean(data.status).toUpperCase()}</b></div>
          <div><span>LAST RUN</span><b>{["success", "scan_complete"].includes(String(latestRun?.status ?? "").toLowerCase()) ? "SCAN COMPLETE" : String(latestRun?.status ?? "").toLowerCase() === "scan_complete_ai_unavailable" ? "SCAN COMPLETE · AI ERROR" : clean(latestRun?.status).toUpperCase()}</b></div>
          <div><span>MARKETS SCANNED</span><b>{latestRun?.pairs_checked ?? pairScans.length}</b></div>
          <div><span>AI REVIEWS OK</span><b>{pairScans.filter(scan => scan.ai_review_status === "ok").length} / {aiReviewAttempted.length}</b></div>
          <div><span>LAST DATA RUN</span><b>{fmtTime(latestRun?.finished_at ?? data.fetched_at)}</b></div>
          <div><span>API READ</span><b>{fmtTime(data.api_fetched_at)}</b></div>
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
