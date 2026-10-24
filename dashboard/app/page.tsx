"use client";

import { useEffect, useMemo, useState } from "react";

type Row = Record<string, any>;
type Payload = { connected: boolean; status: string; message: string; snapshots: Row[]; events: Row[]; runs: Row[]; alerts: Row[]; paper_trades: Row[]; fetched_at?: string };
const empty: Payload = { connected: false, status: "loading", message: "Checking FRIDAY's data link…", snapshots: [], events: [], runs: [], alerts: [], paper_trades: [] };
const fmtTime = (v?: string) => v ? new Date(v).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
const fmtPrice = (v: unknown) => typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : typeof v === "string" ? v : "—";
const payloadOf = (row?: Row) => row?.payload && typeof row.payload === "object" ? row.payload : {};
const clean = (v: unknown) => String(v ?? "unknown").replaceAll("_", " ");
const displayPair = (value: unknown) => { const pair = String(value ?? "").toUpperCase(); return ["XBTUSDT", "XBTUSD", "BTCUSD"].includes(pair) ? "BTCUSDT" : pair; };

export default function Home() {
  const [data, setData] = useState<Payload>(empty);
  const [busy, setBusy] = useState(false);
  const [selectedPair, setSelectedPair] = useState("ALL");

  async function refresh() {
    setBusy(true);
    try {
      const res = await fetch("/api/friday", { cache: "no-store" });
      const body = await res.json();
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

  const pairs = useMemo(() => ["ALL", ...Array.from(new Set([...data.events.map(x => displayPair(x.pair)), ...((data.runs[0]?.pair_results ?? []).map((x: Row) => displayPair(x.pair)))].filter(Boolean)))], [data.events, data.runs]);
  const events = useMemo(() => {
    const filtered = selectedPair === "ALL" ? data.events : data.events.filter(x => displayPair(x.pair) === selectedPair);
    const groups = new Map<string, Row>();
    for (const event of filtered) {
      const pair = displayPair(event.pair);
      const key = [pair, event.timeframe, event.event_type, event.direction, event.signal_time, event.entry_reference].join("|");
      const existing = groups.get(key);
      const line = { line_kind: event.line_kind, line_price: event.line_price, touch_count: event.evidence?.touch_count ?? event.evidence?.touch_count };
      if (existing) {
        existing.supporting_line_count += 1;
        existing.supporting_lines.push(line);
      } else {
        groups.set(key, { ...event, pair, supporting_line_count: 1, supporting_lines: [line] });
      }
    }
    return Array.from(groups.values()).sort((a, b) => new Date(b.signal_time ?? 0).getTime() - new Date(a.signal_time ?? 0).getTime());
  }, [data.events, selectedPair]);
  const snapshot = data.snapshots[0];
  const snapshotData = payloadOf(snapshot);
  const latestRun = data.runs[0];
  const pairScans: Row[] = Array.isArray(latestRun?.pair_results) ? latestRun.pair_results : [];
  const aiOkCount = pairScans.filter(x => x.ai_review_status === "ok").length;
  const aiConfiguredCount = pairScans.filter(x => x.ai_review_status && x.ai_review_status !== "not_configured").length;
  const candidates = events.filter(x => x.event_type === "BREAK_CLOSE_PROBE" || x.event_type === "ENTRY_1" || x.evidence?.event_add === "CONTINUATION_ADD_CONFIRMED");
  const reversalCandidate = snapshotData.momentum_reversal ?? {};
  const candidateCount = candidates.length + (reversalCandidate.eligible ? 1 : 0);
  const latestEvents = events.slice(0, 50);
  const runAgeMs = latestRun?.started_at ? Date.now() - new Date(latestRun.started_at).getTime() : Number.POSITIVE_INFINITY;
  const engineOk = Boolean(latestRun && ["success", "scan_complete_ai_unavailable"].includes(latestRun.status) && data.connected && runAgeMs >= 0 && runAgeMs < 6 * 60 * 60 * 1000);
  const lastScan = latestRun?.finished_at ?? latestRun?.started_at;
  const latestCandle = pairScans.map(x => x.last_completed_candle_utc).filter(Boolean).sort().at(-1) ?? snapshot?.signal_time;
  const latestDecision = snapshotData.friday_decision ?? {};
  const latestSetup = snapshotData.latest_setup_state ?? {};

  return <main className="shell">
    <header className="topbar"><a className="brand" href="#"><span className="brand-mark">F</span><span>FRIDAY<span className="brand-sub">RESEARCH INTELLIGENCE</span></span></a><div className="top-actions"><span className="mode-chip"><i /> RESEARCH ONLY · NO LIVE ORDERS</span><button className="refresh" onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "↻ Refresh"}</button></div></header>

    <section className="command-bar">
      <div><span className="section-kicker">LIVE MARKET RADAR</span><h1>Find the setup. <em>Prove the edge.</em></h1><p>Closed-candle scans · structure first · paper research only</p></div>
      <div className="link-state"><span className={engineOk ? "led on" : "led"} /><div><b>{engineOk ? "ENGINE RECENTLY VERIFIED" : data.status === "loading" ? "CONNECTING" : "ENGINE NOT VERIFIED"}</b><small>{data.message}</small></div></div>
    </section>

    <section className="radar-layout">
      <article className="radar-panel">
        <div className="panel-head"><div><span className="section-kicker">01 / SCANNER</span><h2>FRIDAY radar</h2></div><span className={engineOk ? "status-pill good" : "status-pill"}>{engineOk ? "RUN CONFIRMED" : "AWAITING PROOF"}</span></div>
        <div className="radar-stage">
          <div className="scanner-market-grid">
            {pairScans.length ? pairScans.map((scan, i) => {
              const symbol = displayPair(scan.pair);
              const display = symbol.replace(/USDT$/, "");
              const alertReason = Array.isArray(scan.alert_results) ? scan.alert_results.find((x: Row) => x.reason)?.reason : null;
              return <div className={`scanner-market ${scan.status === "ok" ? "market-ok" : "market-error"}`} key={symbol}>
                <div className="scanner-market-top"><b>{display}</b><span>{scan.status === "ok" ? "SCANNED" : "ERROR"}</span></div>
                <strong>{fmtPrice(scan.last_close)}</strong>
                <small>{clean(scan.latest_setup_state ?? "waiting")}</small>
                <small>AI: {clean(scan.ai_review_assessment ?? scan.ai_review_status ?? "unknown")}</small>
                {alertReason ? <small title={alertReason}>Alert: {String(alertReason)}</small> : null}
              </div>;
            }) : <div className="scanner-empty">No per-market results have been published yet.</div>}
          </div>
        </div>
        <div className="radar-foot"><div><span>LAST COMPLETED RUN</span><b>{fmtTime(lastScan)}</b></div><div><span>PAIRS ATTEMPTED</span><b>{latestRun?.pairs_checked ?? "—"}</b></div><div><span>LAST CANDLE</span><b>{fmtTime(latestCandle)}</b></div></div>
        {!data.connected && <div className="notice"><b>Data connection unavailable</b><p>{data.message} FRIDAY needs a successful scheduled scan and a readable repository-backed dashboard data file.</p></div>}
        {latestRun?.error && <div className="error-note"><b>Last run error</b><p>{latestRun.error}</p></div>}
      </article>

      <aside className="candidate-panel">
        <div className="panel-head"><div><span className="section-kicker">02 / OPPORTUNITY QUEUE</span><h2>Best qualified trades</h2></div><span className="count-badge">{data.paper_trades.filter(x => x.status === "OPEN").length}</span></div>
        <p className="panel-intro">Only risk-defined paper trades that passed the strategy filters appear here. Historical signals and failed setups stay in the ledger below.</p>
        <div className="candidate-list">
          {data.paper_trades.filter(x => x.status === "OPEN").length ? data.paper_trades.filter(x => x.status === "OPEN").slice(0, 5).map((trade, i) => {
            const risk = Math.abs(Number(trade.entry) - Number(trade.stop_loss));
            const reward = String(trade.direction).toLowerCase() === "long" ? Number(trade.take_profit) - Number(trade.entry) : Number(trade.entry) - Number(trade.take_profit);
            const rr = risk > 0 && Number.isFinite(reward) ? reward / risk : Number(trade.reward_risk);
            return <article className="candidate" key={trade.trade_id ?? i}>
              <div className="candidate-top"><b>{displayPair(trade.pair).replace(/USDT$/, "")}</b><span className={String(trade.direction).toLowerCase() === "long" ? "direction long" : "direction short"}>{String(trade.direction).toUpperCase()}</span></div>
              <div className="candidate-title">QUALIFIED · OPEN PAPER TRADE</div>
              <div className="candidate-stats"><div><span>ENTRY</span><b>{fmtPrice(trade.entry)}</b></div><div><span>STOP LOSS</span><b>{fmtPrice(trade.stop_loss)}</b></div><div><span>TARGET</span><b>{fmtPrice(trade.take_profit)}</b></div><div><span>REWARD / RISK</span><b>{Number.isFinite(rr) ? rr.toFixed(2) + "R" : "—"}</b></div></div>
              <div className="candidate-bottom"><span className="state-pill good">filters passed</span><span>{fmtTime(trade.signal_time)}</span></div>
              <p className="candidate-reason">{clean(trade.strategy)} · AI review: {clean(trade.ai_review_status)}{trade.ai_assessment ? " · " + clean(trade.ai_assessment) : ""}. Paper research only; not a live trade recommendation.</p>
            </article>;
          }) : <div className="empty-state"><span className="empty-orbit">◎</span><b>No qualified open paper trades</b><p>That is intentional. A setup only earns a place here after the filters pass and a valid structural stop, target and minimum reward/risk are recorded. Watch for new candidates below.</p></div>}
          {(() => {
            const cutoff = Date.now() - 24 * 60 * 60 * 1000;
            const watch = events.filter(event => {
              const ts = new Date(event.signal_time ?? 0).getTime();
              const outcome = String(event.outcome ?? "").toLowerCase();
              return Number.isFinite(ts) && ts >= cutoff && ["pending", "candidate", "active", "open", "retest_held_waiting_continuation"].includes(outcome);
            }).filter(event => !data.paper_trades.some(trade => displayPair(trade.pair) === displayPair(event.pair) && trade.status === "OPEN" && trade.signal_time === event.signal_time)).slice(0, 3);
            return watch.length ? <details className="history-details"><summary>Near-ready watchlist ({watch.length})</summary>{watch.map((event, i) => <article className="candidate" key={event.event_key ?? i}>
              <div className="candidate-top"><b>{displayPair(event.pair).replace(/USDT$/, "")}</b><span className={String(event.direction).toLowerCase() === "long" ? "direction long" : "direction short"}>{String(event.direction ?? "unknown").toUpperCase()}</span></div>
              <div className="candidate-title">{String(event.outcome ?? "").toLowerCase() === "retest_held_waiting_continuation" ? "RETEST HELD · WAITING CONTINUATION" : "WATCH · NOT YET QUALIFIED"}</div>
              <div className="candidate-bottom"><span className="state-pill">{clean(event.outcome)}</span><span>{fmtTime(event.signal_time)}</span></div>
              <p className="candidate-reason">{String(event.outcome ?? "").toLowerCase() === "retest_held_waiting_continuation" ? "The retest held, but a subsequent closed candle has not yet confirmed continuation through the reaction extreme. No Entry 2 add is confirmed." : "A recent event only. It is not promoted to a qualified trade without a validated structural risk plan and the required filters."}</p>
            </article>)}</details> : null;
          })()}
          <details className="history-details"><summary>Historical setup events ({candidates.length + (reversalCandidate.eligible ? 1 : 0)})</summary><p className="panel-intro">Historical break/retest and Stoch RSI events are retained for review, not presented as current opportunities.</p>
            {candidates.slice(0, 12).map((event, i) => {
              const entry2 = event.evidence?.event_add === "CONTINUATION_ADD_CONFIRMED";
              const direction = String(event.direction ?? "").toLowerCase();
              return <article className="candidate" key={event.event_key ?? i}>
                <div className="candidate-top"><b>{displayPair(event.pair).replace(/USDT$/, "")}</b><span className={direction === "long" ? "direction long" : direction === "short" ? "direction short" : "direction"}>{direction || "direction pending"}</span></div>
                <div className="candidate-title">{entry2 ? "ENTRY 2 · CONTINUATION" : "ENTRY 1 · BREAK CLOSE"}</div>
                <div className="candidate-stats"><div><span>REFERENCE</span><b>{fmtPrice(entry2 ? event.continuation_add_close : event.entry_reference)}</b></div><div><span>STRUCTURE</span><b>{fmtPrice(event.line_price)}</b></div></div>
                <div className="candidate-bottom"><span className="state-pill">{clean(event.outcome)}</span><span>{fmtTime(entry2 ? event.continuation_add_time : event.signal_time)}</span></div>
                <p className="candidate-reason">{event.evidence?.line_kind ? clean(event.evidence.line_kind) + " · " : ""}{event.evidence?.daily_bias ? "Daily bias: " + clean(event.evidence.daily_bias) : "Historical event; inspect the full ledger for evidence."}</p>
              </article>;
            })}
          </details>
        </div>
        <div className="panel-bottom"><span>RESEARCH / PAPER ONLY</span><span>NO EXCHANGE ORDERS</span></div>
      </aside>
    </section>

    <section className="feed-section">
      <div className="section-head"><div><span className="section-kicker">03 / PAPER TRADE MANAGEMENT</span><h2>Stops & targets</h2><p>Open paper positions are checked against each newly closed 4H candle. If stop and target hit in the same candle, stop is recorded first conservatively.</p></div></div>
      <div className="feed-table-wrap"><table><thead><tr><th>MARKET / STRATEGY</th><th>DIRECTION</th><th>STATUS</th><th>ENTRY</th><th>STOP LOSS</th><th>TAKE PROFIT</th><th>AI REVIEW</th><th>EXIT / RESULT</th></tr></thead><tbody>
        {data.paper_trades.length ? data.paper_trades.slice(0, 100).map((trade, i) => <tr key={trade.trade_id ?? i}>
          <td><b>{displayPair(trade.pair).replace(/USDT$/, "")}</b><small>{clean(trade.strategy)} · {fmtTime(trade.signal_time)}</small></td>
          <td><span className={String(trade.direction).toLowerCase() === "long" ? "direction long" : "direction short"}>{trade.direction}</span></td>
          <td><span className={"state-pill " + (trade.status === "OPEN" ? "good" : "")}>{trade.status}</span></td>
          <td>{fmtPrice(trade.entry)}</td><td>{fmtPrice(trade.stop_loss)}</td><td>{fmtPrice(trade.take_profit)}</td>
          <td>{clean(trade.ai_review_status)}{trade.ai_assessment ? <small>{clean(trade.ai_assessment)}</small> : null}{trade.ai_priority_candidate ? <small>{clean(trade.ai_priority_candidate)}</small> : null}</td>
          <td>{trade.exit_reason ? <>{clean(trade.exit_reason)} @ {fmtPrice(trade.exit_price)}<small>{trade.realized_r_multiple}R · {fmtTime(trade.exit_time)}</small></> : "Monitoring"}</td>
        </tr>) : <tr><td colSpan={8}><div className="empty-row">No risk-defined paper trades recorded yet. FRIDAY will not invent a position when filters or risk plans fail.</div></td></tr>}
      </tbody></table></div>
      <div className="feed-foot"><span>{data.paper_trades.filter(x => x.status === "OPEN").length} open paper trades</span><span>{data.paper_trades.filter(x => x.status === "CLOSED").length} closed by stop/target</span></div>
    </section>

    <section className="feed-section">
      <div className="section-head"><div><span className="section-kicker">04 / SCAN & TRADE LEDGER</span><h2>What FRIDAY has recorded</h2><p>New records appear after persistence succeeds. Refreshes automatically every 30 seconds.</p></div>
        <label className="filter-label">PAIR <select value={selectedPair} onChange={e => setSelectedPair(e.target.value)}>{pairs.map(p => <option key={p} value={p}>{p === "ALL" ? p : displayPair(p).replace(/USDT$/, "")}</option>)}</select></label>
      </div>
      <div className="feed-table-wrap"><table><thead><tr><th>TIME / MARKET</th><th>EVENT</th><th>DIRECTION</th><th>STATE</th><th>ENTRY REF.</th><th>RETEST</th><th>CONTINUATION</th></tr></thead><tbody>
        {latestEvents.length ? latestEvents.map((event, i) => <tr key={event.event_key ?? i}><td><b>{displayPair(event.pair).replace(/USDT$/, "")}</b><small>{fmtTime(event.signal_time)}</small></td><td>{clean(event.event_type)}{event.supporting_line_count > 1 ? <small>{event.supporting_line_count} supporting lines grouped</small> : null}</td><td><span className={String(event.direction ?? "").toLowerCase() === "long" ? "direction long" : String(event.direction ?? "").toLowerCase() === "short" ? "direction short" : "direction"}>{event.direction ?? "—"}</span></td><td><span className="state-pill">{clean(event.outcome)}</span></td><td>{fmtPrice(event.entry_reference)}</td><td>{event.retest_confirmed ? <span className="yes">HELD</span> : "—"}</td><td>{fmtTime(event.continuation_add_time)}</td></tr>) : <tr><td colSpan={7}><div className="empty-row">No trade lifecycle events persisted yet.</div></td></tr>}
      </tbody></table></div>
      <div className="feed-foot"><span>{latestEvents.length} of up to 50 recent lifecycle events</span><span>Last dashboard refresh: {fmtTime(data.fetched_at)}</span></div>
    </section>

    <section className="engine-strip">
      <div><span className="section-kicker">05 / ENGINE DIAGNOSTICS</span><h2>Trust, but verify.</h2></div>
      <div className="diagnostic-grid">
        <div><span>DATA LINK</span><b className={data.connected ? "value-good" : "value-bad"}>{data.connected ? "CONNECTED" : clean(data.status).toUpperCase()}</b></div>
        <div><span>LAST RUN</span><b>{latestRun?.status ? clean(latestRun.status).toUpperCase() : "NO RUN RECORD"}</b></div>
        <div><span>PAIRS WITH RESULTS</span><b>{pairScans.filter(x => x.status === "ok").length} / {latestRun?.pairs_checked ?? 0}</b></div>
        <div><span>AI REVIEWS</span><b className={aiOkCount === pairScans.length && pairScans.length > 0 ? "value-good" : "value-bad"}>{aiOkCount} / {pairScans.length || 0} OK</b><small>{aiConfiguredCount === 0 ? "Model not configured" : "Completed reviews in latest run"}</small></div>
        <div><span>ALERT RECORDS</span><b>{data.alerts.length}</b></div>
      </div>
    </section>
    <section className="feed-section">
      <div className="section-head">
        <div>
          <span className="section-kicker">06 / LATEST SCAN TRACE</span>
          <h2>Every market examined</h2>
          <p>Per-market outcomes from the latest completed run, including AI failures and the exact reason a Telegram alert was blocked or not sent.</p>
        </div>
        <div className="feed-foot">
          <span>Run ID: {clean(latestRun?.run_id)}</span>
          <span>Started: {fmtTime(latestRun?.started_at)}</span>
        </div>
      </div>
      <div className="diagnostic-grid">
        <div><span>MARKETS REQUESTED</span><b>{latestRun?.markets_requested ?? latestRun?.pairs_checked ?? pairScans.length}</b></div>
        <div><span>MARKETS FAILED</span><b className={Number(latestRun?.markets_failed ?? 0) > 0 ? "value-bad" : "value-good"}>{latestRun?.markets_failed ?? pairScans.filter(x => x.status !== "ok").length}</b></div>
        <div><span>ALERT OUTCOMES</span><b>{latestRun?.alert_outcomes_total ?? 0}</b></div>
        <div><span>TELEGRAM SENT</span><b>{latestRun?.alert_outcomes_sent ?? data.alerts.filter(x => x.status === "sent").length}</b></div>
        <div><span>BLOCKED / NOT SENT</span><b>{latestRun?.alert_outcomes_blocked ?? 0}</b></div>
      </div>
      <div className="feed-table-wrap"><table><thead><tr><th>MARKET</th><th>SCAN</th><th>SETUP / DECISION</th><th>AI REVIEW</th><th>ALERT OUTCOME / REASON</th><th>DURATION</th></tr></thead><tbody>
        {pairScans.length ? pairScans.map((scan: Row) => {
          const outcomes = Array.isArray(scan.alert_results) ? scan.alert_results : [];
          const outcomeText = outcomes.map((item: Row) =>
            `${clean(item.type)}: ${clean(item.status)}${item.reason ? " — " + clean(item.reason) : ""}`
          ).join(" · ") || "No alert candidate recorded on the latest completed candle";
          const aiText = scan.ai_review_status === "ok"
            ? `OK · ${clean(scan.ai_review_assessment)}`
            : `${clean(scan.ai_review_status)}${scan.ai_review_error_category ? " · " + clean(scan.ai_review_error_category) : ""}${scan.ai_review_error ? " · " + clean(scan.ai_review_error) : ""}`;
          return <tr key={scan.pair}>
            <td><b>{displayPair(scan.pair).replace(/USDT$/, "")}</b></td>
            <td><span className={scan.status === "ok" ? "state-pill good" : "state-pill bad"}>{clean(scan.status).toUpperCase()}</span></td>
            <td>{clean(scan.latest_setup_state)} / {clean(scan.friday_decision)}</td>
            <td>{aiText}</td>
            <td className="candidate-reason">{outcomeText}</td>
            <td>{typeof scan.duration_seconds === "number" ? `${scan.duration_seconds.toFixed(1)}s` : "—"}</td>
          </tr>;
        }) : <tr><td colSpan={6}><div className="empty-row">No completed scan detail is available yet. A 202 response confirms dispatch only; the run appears here after GitHub Actions publishes its summary.</div></td></tr>}
      </tbody></table></div>
      <div className="feed-foot"><span>Run status: {clean(latestRun?.status)}</span><span>Finished: {fmtTime(latestRun?.finished_at)}</span></div>
    </section>
    <footer><span className="footer-brand">FRIDAY<span> / RESEARCH CONTROL ROOM</span></span><span>Built for transparent decisions, not unattended trading.</span><span>REFRESH · 30 SEC</span></footer>
  </main>;
}
