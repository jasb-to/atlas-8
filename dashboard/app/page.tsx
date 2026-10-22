"use client";

import { useEffect, useMemo, useState } from "react";

type Row = Record<string, any>;
type Payload = { connected: boolean; status: string; message: string; snapshots: Row[]; events: Row[]; runs: Row[]; alerts: Row[]; fetched_at?: string };
const empty: Payload = { connected: false, status: "loading", message: "Checking FRIDAY's data link…", snapshots: [], events: [], runs: [], alerts: [] };
const fmtTime = (v?: string) => v ? new Date(v).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
const fmtPrice = (v: unknown) => typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 6 }) : typeof v === "string" ? v : "—";
const payloadOf = (row?: Row) => row?.payload && typeof row.payload === "object" ? row.payload : {};
const clean = (v: unknown) => String(v ?? "unknown").replaceAll("_", " ");

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

  const pairs = useMemo(() => ["ALL", ...Array.from(new Set(data.events.map(x => x.pair).filter(Boolean)))], [data.events]);
  const events = useMemo(() => selectedPair === "ALL" ? data.events : data.events.filter(x => x.pair === selectedPair), [data.events, selectedPair]);
  const snapshot = data.snapshots[0];
  const snapshotData = payloadOf(snapshot);
  const latestRun = data.runs[0];
  const pairScans: Row[] = Array.isArray(latestRun?.pair_results) ? latestRun.pair_results : [];
  const candidates = events.filter(x => x.event_type === "BREAK_CLOSE_PROBE" || x.event_type === "ENTRY_1" || x.evidence?.event_add === "CONTINUATION_ADD_CONFIRMED");
  const latestEvents = events.slice(0, 50);
  const runAgeMs = latestRun?.started_at ? Date.now() - new Date(latestRun.started_at).getTime() : Number.POSITIVE_INFINITY;
  const engineOk = latestRun?.status === "success" && data.connected && runAgeMs >= 0 && runAgeMs < 6 * 60 * 60 * 1000;
  const lastScan = latestRun?.finished_at ?? latestRun?.started_at;
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
          <div className={`radar-grid ${engineOk ? "radar-active" : "radar-idle"}`}><span className="radar-sweep" /><i className="radar-cross cross-h" /><i className="radar-cross cross-v" />
            <span className="radar-dot dot-btc" /><span className="radar-dot dot-virtual" /><span className="radar-dot dot-ghost" />
            <div className="radar-core"><b>F</b><small>{engineOk ? "MONITOR OK" : "FRIDAY"}</small></div>
            <span className="radar-label label-btc">XBTUSD</span><span className="radar-label label-virtual">VIRTUALUSD</span>
          </div>
        </div>
        <div className="radar-foot"><div><span>LAST COMPLETED RUN</span><b>{fmtTime(lastScan)}</b></div><div><span>PAIRS ATTEMPTED</span><b>{latestRun?.pairs_checked ?? "—"}</b></div><div><span>LAST CANDLE</span><b>{fmtTime(snapshot?.signal_time)}</b></div></div>
        {!data.connected && <div className="notice"><b>Data connection unavailable</b><p>{data.message} FRIDAY needs the correct dedicated Supabase project, schema and server-only credentials.</p></div>}
        {latestRun?.error && <div className="error-note"><b>Last run error</b><p>{latestRun.error}</p></div>}
      </article>

      <aside className="candidate-panel">
        <div className="panel-head"><div><span className="section-kicker">02 / OPPORTUNITY QUEUE</span><h2>Setup signals</h2></div><span className="count-badge">{candidates.length}</span></div>
        <p className="panel-intro">Recorded Entry 1 / Entry 2 events only. A signal is not a complete trade plan until invalidation, targets and risk/reward are defined.</p>
        <div className="candidate-list">
          {candidates.length ? candidates.slice(0, 12).map((event, i) => {
            const entry2 = event.evidence?.event_add === "CONTINUATION_ADD_CONFIRMED";
            const direction = String(event.direction ?? "").toLowerCase();
            return <article className="candidate" key={event.event_key ?? i}>
              <div className="candidate-top"><b>{event.pair}</b><span className={direction === "long" ? "direction long" : direction === "short" ? "direction short" : "direction"}>{direction || "direction pending"}</span></div>
              <div className="candidate-title">{entry2 ? "ENTRY 2 · CONTINUATION" : "ENTRY 1 · BREAK CLOSE"}</div>
              <div className="candidate-stats"><div><span>REFERENCE</span><b>{fmtPrice(entry2 ? event.continuation_add_close : event.entry_reference)}</b></div><div><span>STRUCTURE</span><b>{fmtPrice(event.line_price)}</b></div></div>
              <div className="candidate-bottom"><span className="state-pill">{clean(event.outcome)}</span><span>{fmtTime(entry2 ? event.continuation_add_time : event.signal_time)}</span></div>
              <p className="candidate-reason">{event.evidence?.line_kind ? clean(event.evidence.line_kind) + " · " : ""}{event.evidence?.daily_bias ? "Daily bias: " + clean(event.evidence.daily_bias) : "See event evidence before treating this as actionable."}</p>
            </article>;
          }) : <div className="empty-state"><span className="empty-orbit">◎</span><b>No qualifying events recorded</b><p>When the engine records a break-close probe or confirmed continuation event, it will appear here. No placeholder trades are shown.</p></div>}
        </div>
        <div className="panel-bottom"><span>RESEARCH / PAPER ONLY</span><span>NO EXCHANGE ORDERS</span></div>
      </aside>
    </section>

    <section className="feed-section">
      <div className="section-head"><div><span className="section-kicker">03 / SCAN & TRADE LEDGER</span><h2>What FRIDAY has recorded</h2><p>New records appear after persistence succeeds. Refreshes automatically every 30 seconds.</p></div>
        <label className="filter-label">PAIR <select value={selectedPair} onChange={e => setSelectedPair(e.target.value)}>{pairs.map(p => <option key={p} value={p}>{p}</option>)}</select></label>
      </div>
      <div className="feed-table-wrap"><table><thead><tr><th>TIME / MARKET</th><th>EVENT</th><th>DIRECTION</th><th>STATE</th><th>ENTRY REF.</th><th>RETEST</th><th>CONTINUATION</th></tr></thead><tbody>
        {latestEvents.length ? latestEvents.map((event, i) => <tr key={event.event_key ?? i}><td><b>{event.pair}</b><small>{fmtTime(event.signal_time)}</small></td><td>{clean(event.event_type)}</td><td><span className={String(event.direction ?? "").toLowerCase() === "long" ? "direction long" : String(event.direction ?? "").toLowerCase() === "short" ? "direction short" : "direction"}>{event.direction ?? "—"}</span></td><td><span className="state-pill">{clean(event.outcome)}</span></td><td>{fmtPrice(event.entry_reference)}</td><td>{event.retest_confirmed ? <span className="yes">HELD</span> : "—"}</td><td>{fmtTime(event.continuation_add_time)}</td></tr>) : <tr><td colSpan={7}><div className="empty-row">No trade lifecycle events persisted yet.</div></td></tr>}
      </tbody></table></div>
      <div className="feed-foot"><span>{latestEvents.length} of up to 50 recent lifecycle events</span><span>Last dashboard refresh: {fmtTime(data.fetched_at)}</span></div>
    </section>

    <section className="engine-strip">
      <div><span className="section-kicker">04 / ENGINE DIAGNOSTICS</span><h2>Trust, but verify.</h2></div>
      <div className="diagnostic-grid">
        <div><span>DATA LINK</span><b className={data.connected ? "value-good" : "value-bad"}>{data.connected ? "CONNECTED" : clean(data.status).toUpperCase()}</b></div>
        <div><span>LAST RUN</span><b>{latestRun?.status ? clean(latestRun.status).toUpperCase() : "NO RUN RECORD"}</b></div>
        <div><span>PAIRS WITH RESULTS</span><b>{pairScans.filter(x => x.status === "ok").length} / {latestRun?.pairs_checked ?? 0}</b></div>
        <div><span>ALERT RECORDS</span><b>{data.alerts.length}</b></div>
      </div>
    </section>
    <footer><span className="footer-brand">FRIDAY<span> / RESEARCH CONTROL ROOM</span></span><span>Built for transparent decisions, not unattended trading.</span><span>REFRESH · 30 SEC</span></footer>
  </main>;
}
