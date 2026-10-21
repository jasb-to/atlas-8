"use client";

import { useEffect, useMemo, useState } from "react";

type Row = Record<string, any>;
type Payload = { connected: boolean; status: string; message: string; snapshots: Row[]; events: Row[]; runs: Row[]; alerts: Row[]; fetched_at?: string };
const empty: Payload = { connected: false, status: "loading", message: "Checking the research data connection…", snapshots: [], events: [], runs: [], alerts: [] };
const fmtTime = (value?: string) => value ? new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
const fmtPrice = (value: unknown) => typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 6 }) : value ?? "—";
const payloadOf = (row: Row) => row.payload && typeof row.payload === "object" ? row.payload : {};

export default function Home() {
  const [data, setData] = useState<Payload>(empty);
  const [busy, setBusy] = useState(false);
  const [selectedPair, setSelectedPair] = useState("ALL");
  async function refresh() {
    setBusy(true);
    try { const res = await fetch("/api/friday", { cache: "no-store" }); setData(await res.json()); }
    catch { setData({ ...empty, status: "unreachable", message: "Dashboard API could not be reached. Check the deployment logs." }); }
    finally { setBusy(false); }
  }
  useEffect(() => { void refresh(); const timer = window.setInterval(() => void refresh(), 30000); return () => window.clearInterval(timer); }, []);

  const pairs = useMemo(() => ["ALL", ...Array.from(new Set(data.events.map((x) => x.pair).filter(Boolean)))], [data.events]);
  const events = useMemo(() => selectedPair === "ALL" ? data.events : data.events.filter((x) => x.pair === selectedPair), [data.events, selectedPair]);
  const latestSnapshot = data.snapshots[0];
  const snapshotPayload = latestSnapshot ? payloadOf(latestSnapshot) : {};
  const reversal = snapshotPayload.reversal_context ?? {};
  const latestRun = data.runs[0];
  const actionable = events.filter((x) => ["pending", "retest_held_waiting_continuation", "continuation_add_confirmed"].includes(String(x.outcome).toLowerCase()));
  const entry1 = events.filter((x) => x.event_type === "BREAK_CLOSE_PROBE" || x.event_type === "ENTRY_1").length;
  const entry2 = events.filter((x) => x.continuation_add_time).length;
  const failed = events.filter((x) => String(x.outcome).toLowerCase() === "failed_break").length;

  return <main className="shell">
    <header className="topbar"><a className="brand" href="#"><span className="brand-mark">F</span><span>FRIDAY<span className="brand-sub">RESEARCH INTELLIGENCE</span></span></a><div className="top-actions"><span className="mode-chip"><i /> RESEARCH ONLY · NO LIVE ORDERS</span><button className="refresh" onClick={() => void refresh()} disabled={busy}>{busy ? "Refreshing…" : "↻ Refresh"}</button></div></header>
    <section className="hero"><div className="hero-copy"><div className="eyebrow"><span className="eyebrow-line" /> THE MARKET TURN, FOUND EARLY</div><h1>Let the structure<br/><em>tell the story.</em></h1><p>Friday watches the break, reads the retest, measures exhaustion, and records what happens next. One decision at a time. No black-box entries.</p><div className="hero-tags"><span>01 / STRUCTURE</span><span>02 / MOMENTUM</span><span>03 / CONFIRMATION</span></div></div><div className="hero-orbit" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="orbit-core"><span>F</span><small>FRIDAY</small></div><span className="orbit-node node-a">BREAK</span><span className="orbit-node node-b">RETEST</span><span className="orbit-node node-c">LEARN</span></div></section>
    <section className="status-strip"><div className="status-led"><span className={data.connected ? "led on" : "led"} /> <div><b>{data.connected ? "DATA LINK CONNECTED" : data.status === "loading" ? "CONNECTING TO FRIDAY" : "AWAITING DATA CONNECTION"}</b><small>{data.message}</small></div></div><div className="status-right"><span>LAST REFRESH</span><b>{fmtTime(data.fetched_at)}</b></div></section>
    {!data.connected && <section className="setup-note"><div className="setup-icon">i</div><div><b>The control room is built; the data link is not configured yet.</b><p>Deploy with <code>FRIDAY_SUPABASE_URL</code> and <code>FRIDAY_SUPABASE_SERVICE_ROLE_KEY</code> set as server-only environment variables, then apply <code>supabase/friday_research_schema.sql</code> to the dedicated research database. Never expose the service-role key in browser code.</p></div></section>}
    <section className="section-head"><div><span className="section-kicker">01 / MARKET PULSE</span><h2>What Friday sees</h2></div><span className="muted-note">Completed-candle context · no hindsight triggers</span></section>
    <section className="metrics-grid"><Metric label="LATEST SNAPSHOT" value={latestSnapshot?.pair ?? "—"} sub={latestSnapshot ? fmtTime(latestSnapshot.signal_time) : "No persisted snapshot yet"} /><Metric label="POTENTIAL SETUPS" value={actionable.length} sub="Recent lifecycle records" /><Metric label="ENTRY 1 / BREAKS" value={entry1} sub="Recorded break-close probes" /><Metric label="ENTRY 2 / ADDS" value={entry2} sub="Retest + continuation records" /></section>
    <section className="two-col"><article className="panel"><div className="panel-head"><div><span className="section-kicker">REVERSAL / EXHAUSTION</span><h3>Early-turn evidence</h3></div><span className="tiny-tag">ADVISORY CONTEXT</span></div>{reversal.state ? <><div className="signal-state">{String(reversal.state).replaceAll("_", " ")}</div><div className="score-row"><Score label="Bullish evidence" value={reversal.long_reversal_score ?? 0}/><Score label="Bearish evidence" value={reversal.short_reversal_score ?? 0}/></div><div className="evidence-columns"><Evidence title="BULLISH" items={reversal.evidence?.bullish ?? []}/><Evidence title="BEARISH" items={reversal.evidence?.bearish ?? []}/></div><p className="footnote">Evidence counts are not probabilities. Structure must still confirm the setup.</p></> : <Empty title="Waiting for Friday’s first snapshot" body="Once the monitor writes a decision snapshot, this panel will show the reversal evidence it recorded."/>}</article>
    <article className="panel"><div className="panel-head"><div><span className="section-kicker">ENGINE HEALTH</span><h3>Monitor & persistence</h3></div><span className={latestRun?.status === "success" ? "tiny-tag good" : "tiny-tag"}>{latestRun?.status ?? "NO RUN DATA"}</span></div><div className="health-list"><Health label="Last monitor run" value={fmtTime(latestRun?.started_at)}/><Health label="Pairs checked" value={latestRun?.pairs_checked ?? "—"}/><Health label="Last run finished" value={fmtTime(latestRun?.finished_at)}/><Health label="Alert delivery records" value={data.alerts.length}/></div>{latestRun?.error && <p className="error-text">{latestRun.error}</p>}<p className="footnote">A missing run is shown as missing—not treated as a healthy run.</p></article></section>
    <section className="section-head"><div><span className="section-kicker">02 / SETUP LIFECYCLE</span><h2>Break → retest → continuation</h2></div><label className="filter-label">MARKET <select value={selectedPair} onChange={(e) => setSelectedPair(e.target.value)}>{pairs.map((p) => <option key={p}>{p}</option>)}</select></label></section>
    <section className="panel table-panel"><div className="table-scroll"><table><thead><tr><th>MARKET / TIME</th><th>DIRECTION</th><th>STATE</th><th>REFERENCE</th><th>RETEST</th><th>CONTINUATION</th></tr></thead><tbody>{events.length ? events.slice(0, 20).map((event, i) => <tr key={event.event_key ?? i}><td><b>{event.pair}</b><small>{fmtTime(event.signal_time)}</small></td><td><span className={String(event.direction ?? "").toLowerCase() === "long" ? "direction long" : String(event.direction ?? "").toLowerCase() === "short" ? "direction short" : "direction"}>{event.direction ?? "—"}</span></td><td><span className="state-pill">{String(event.outcome ?? "unknown").replaceAll("_", " ")}</span></td><td><b>{fmtPrice(event.line_price ?? event.entry_reference)}</b><small>{event.line_kind ?? "structural level"}</small></td><td>{event.retest_confirmed ? <span className="yes">HELD</span> : "—"}</td><td>{event.continuation_add_time ? fmtTime(event.continuation_add_time) : "Waiting / none"}</td></tr>) : <tr><td colSpan={6}><Empty title="No lifecycle records yet" body="Friday will populate this table as the research monitor persists closed-candle events."/></td></tr>}</tbody></table></div><div className="table-foot"><span>{events.length} records loaded · maximum 50 operational events</span><span>{failed} failed breaks in current selection</span></div></section>
    <section className="section-head"><div><span className="section-kicker">03 / LEARNING LOOP</span><h2>Evidence over opinions</h2></div></section><section className="learning-grid"><div className="learning-card"><span className="learning-num">01</span><b>Capture</b><p>Freeze the decision, evidence, model review and structure at candle close.</p></div><div className="learning-card"><span className="learning-num">02</span><b>Replay</b><p>Measure structural targets, invalidations, fees and missed moves separately.</p></div><div className="learning-card"><span className="learning-num">03</span><b>Compare</b><p>Run walk-forward tests against a baseline before changing strategy rules.</p></div><div className="learning-card"><span className="learning-num">04</span><b>Promote carefully</b><p>No automatic rule changes. Better performance must survive out-of-sample testing.</p></div></section>
    <footer><span className="footer-brand">FRIDAY<span> / RESEARCH CONTROL ROOM</span></span><span>Built for transparent decisions, not unattended trading.</span><span>REFRESH · 30 SEC</span></footer>
  </main>;
}

function Metric({ label, value, sub }: { label: string; value: string | number; sub: string }) { return <article className="metric"><span>{label}</span><strong>{value}</strong><small>{sub}</small></article>; }
function Score({ label, value }: { label: string; value: number }) { return <div className="score"><div><span>{label}</span><b>{value}</b></div><div className="score-track"><i style={{ width: `${Math.min(100, Math.max(0, value * 20))}%` }}/></div></div>; }
function Evidence({ title, items }: { title: string; items: string[] }) { return <div className="evidence-list"><span>{title}</span>{items.length ? items.map((x) => <small key={x}>↗ {x.replaceAll("_", " ")}</small>) : <small className="quiet">No evidence recorded</small>}</div>; }
function Health({ label, value }: { label: string; value: string | number }) { return <div className="health-row"><span>{label}</span><b>{value}</b></div>; }
function Empty({ title, body }: { title: string; body: string }) { return <div className="empty"><span>◌</span><b>{title}</b><p>{body}</p></div>; }
