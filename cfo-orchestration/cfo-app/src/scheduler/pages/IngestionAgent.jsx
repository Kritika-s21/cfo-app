import React, { useState, useEffect, useCallback } from "react";
import { Panel, Card, Stat, Tabs, Badge, Button, Input, Field, Empty, LogConsole, useToast } from "../components/ui.jsx";
import { AgentAPI, ConnectionsAPI } from "../api.js";

const DEFAULT_SQL_LABEL = "(default — SQL_CONNECTION_STRING env var)";
const DEFAULT_VEC_LABEL = "(default — VECTOR_DB_PATH env var)";

export default function IngestionAgent() {
  const [tab, setTab] = useState("status");
  const [agentStatus, setAgentStatus] = useState(null);
  const [err, setErr] = useState(null);
  const toast = useToast();

  const load = useCallback(() => {
    AgentAPI.status().then(setAgentStatus).catch((e) => setErr(e.message));
  }, []);
  useEffect(() => { load(); const id = setInterval(load, 15000); return () => clearInterval(id); }, [load]);

  if (err) {
    return (
      <Panel title="Data Classification & Ingestion Agent" icon="🧠">
        <div className="err-banner">⚠️ {err}</div>
        <p className="text-mid">Start the API server with:</p>
        <pre className="mono code-box">python api_server.py</pre>
      </Panel>
    );
  }

  const vs = agentStatus?.vector_store || {};

  return (
    <>
      <div className="grid grid-3 mb-md">
        <Card><Stat icon="🟢" tone="good" label="Agent status" value={agentStatus ? "Online" : "…"} /></Card>
        <Card><Stat icon="🧩" tone="violet" label="Vector chunks" value={vs.total_chunks ?? 0} /></Card>
        <Card><Stat icon="📈" tone="accent" label="Recent pipelines" value={agentStatus?.recent_pipelines ?? 0} /></Card>
      </div>

      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { key: "status", label: "Scan Ingestion Status", icon: "📂" },
          { key: "sql", label: "SQL Records", icon: "🗄" },
          { key: "vector", label: "Vector Store", icon: "🔍" },
          { key: "results", label: "Results", icon: "📊" },
          { key: "logs", label: "Logs", icon: "📋" },
        ]}
      />

      <div className="mt-md">
        {tab === "status" && <ScanStatusTab />}
        {tab === "sql" && <SqlRecordsTab />}
        {tab === "vector" && <VectorTab vs={vs} agentStatus={agentStatus} />}
        {tab === "results" && <ResultsTab />}
        {tab === "logs" && <LogsTab />}
      </div>

      <style>{`
        .err-banner { background: var(--bad-dim); color: var(--bad); padding: 12px 16px; border-radius: 8px; margin-bottom: 14px; }
        .code-box { background: #f8fafc; border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px 16px; }
      `}</style>
    </>
  );
}

function ScanStatusTab() {
  const [results, setResults] = useState(null);
  const [err, setErr] = useState(null);
  const load = () => AgentAPI.results(50).then((r) => setResults(r.results || [])).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  return (
    <Panel title="Scan-driven ingestion status" icon="📂"
      subtitle="Ingestion runs automatically — every scan that finds new/modified files extracts and ingests them in the background"
      actions={<Button size="sm" onClick={load}>🔄 Refresh</Button>}>
      {err && <div className="err-banner">{err}</div>}
      {!results ? <Empty icon="⏳" title="Loading…" /> :
        results.length === 0 ? (
          <Empty icon="📭" title="No pipeline activity yet" hint="Trigger a scan from Dashboard or My Schedulers." />
        ) : (
          <div className="flex-col gap-sm">
            {results.slice(0, 20).map((r) => (
              <div key={r.pipeline_id} className="file-row">
                <span>{r.error ? "❌" : "✅"}</span>
                <b>{r.file_name || "?"}</b>
                <span className="text-mid">{r.stages?.classification?.classification || "?"}</span>
                <span className="text-mid mono" style={{ fontSize: 11 }}>{r.pipeline_id}</span>
              </div>
            ))}
          </div>
        )}
      <style>{`.err-banner{background:var(--bad-dim);color:var(--bad);padding:10px 14px;border-radius:8px;margin-bottom:12px}
        .file-row{display:flex;align-items:center;gap:10px;padding:9px 12px;background:var(--bg-3);border-radius:8px;font-size:13px}`}</style>
    </Panel>
  );
}

function SqlRecordsTab() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const [sqlProfiles, setSqlProfiles] = useState([]);
  const [sqlChoice, setSqlChoice] = useState(DEFAULT_SQL_LABEL);
  const sqlIdByName = Object.fromEntries(sqlProfiles.map((p) => [p.name, p.id]));

  useEffect(() => {
    ConnectionsAPI.listSql().then((r) => setSqlProfiles(r.profiles || [])).catch(() => {});
  }, []);

  const load = useCallback(() => {
    setData(null);
    AgentAPI.structured(50, sqlIdByName[sqlChoice]).then(setData).catch((e) => setErr(e.message));
  }, [sqlChoice, sqlProfiles]); // eslint-disable-line
  useEffect(() => { load(); }, [load]);

  return (
    <Panel title="SQL ingested records" icon="🗄" subtitle="dbo.FileIngestion"
      actions={<Button size="sm" onClick={load}>🔄 Refresh</Button>}>
      <Field label="Database">
        <select className="select" value={sqlChoice} onChange={(e) => setSqlChoice(e.target.value)}>
          {[DEFAULT_SQL_LABEL, ...sqlProfiles.map((p) => p.name)].map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </Field>
      {err && <div className="err-banner">{err}</div>}
      {!data ? <Empty icon="⏳" title="Loading…" /> : data.records?.length ? (
        <>
          <div className="text-mid mb-sm" style={{ fontSize: 12.5 }}>{data.count} record(s) found</div>
          <table className="data-table">
            <thead><tr><th>File</th><th>Type</th><th>Source</th><th>Classification</th><th>Data type</th><th>Confidence</th><th>Ingested</th></tr></thead>
            <tbody>
              {data.records.map((rec, i) => (
                <tr key={i}>
                  <td>{rec.file_name}</td>
                  <td>{rec.file_type}</td>
                  <td>{rec.source_type}</td>
                  <td><Badge status={rec.classification}>{rec.classification}</Badge></td>
                  <td>{rec.data_type}</td>
                  <td>{rec.confidence != null ? `${Math.round(rec.confidence * 100)}%` : "—"}</td>
                  <td className="mono" style={{ fontSize: 11.5 }}>{rec.ingested_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <Empty icon="📭" title="No structured records ingested yet" />
      )}
      <style>{`.err-banner{background:var(--bad-dim);color:var(--bad);padding:10px 14px;border-radius:8px;margin-bottom:12px}
        .select{background:var(--bg-3);border:1px solid var(--border-soft);border-radius:8px;padding:8px 12px;font-size:13px;margin-bottom:12px}`}</style>
    </Panel>
  );
}

function VectorTab({ vs, agentStatus }) {
  const [stats, setStats] = useState(null);
  const [vecProfiles, setVecProfiles] = useState([]);
  const [vecChoice, setVecChoice] = useState(DEFAULT_VEC_LABEL);
  const vecIdByName = Object.fromEntries(vecProfiles.map((p) => [p.name, p.id]));

  useEffect(() => {
    ConnectionsAPI.listVector().then((r) => setVecProfiles(r.profiles || [])).catch(() => {});
  }, []);
  useEffect(() => {
    AgentAPI.vectorStats(vecIdByName[vecChoice]).then(setStats).catch(() => {});
  }, [vecChoice, vecProfiles]); // eslint-disable-line

  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(8);
  const [results, setResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const toast = useToast();

  const search = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const r = await AgentAPI.vectorSearch(query, topK, vecIdByName[vecChoice]);
      setResults(r.results || []);
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setSearching(false);
    }
  };

  const s = stats || vs || {};
  return (
    <>
      <Panel title="Vector store" icon="🔍" subtitle="LanceDB collection stats">
        <Field label="Vector store">
          <select className="select" value={vecChoice} onChange={(e) => setVecChoice(e.target.value)}>
            {[DEFAULT_VEC_LABEL, ...vecProfiles.map((p) => p.name)].map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </Field>
        <div className="grid grid-4">
          <Card><Stat icon="🧩" tone="violet" label="Total chunks" value={s.total_chunks ?? 0} /></Card>
          <Card><Stat icon="🗂️" tone="accent" label="Collection" value={s.collection ?? "—"} /></Card>
          <Card><Stat icon="🗄" tone="good" label="SQL table" value={s.sql_table ?? "dbo.FileVectors"} /></Card>
          <Card><Stat icon="⚙️" tone="warn" label="Status" value={s.status ?? agentStatus?.status ?? "unknown"} /></Card>
        </div>
      </Panel>

      <Panel title="Semantic search" icon="🔎" subtitle="Query the vector store directly">
        <div className="row gap-sm mb-md">
          <Input placeholder="Search ingested content…" value={query} onChange={(e) => setQuery(e.target.value)} className="w-full" />
          <Input type="number" value={topK} min={1} max={30} style={{ width: 80 }} onChange={(e) => setTopK(Number(e.target.value))} />
          <Button variant="primary" loading={searching} onClick={search}>Search</Button>
        </div>
        {results && (
          results.length === 0 ? <Empty icon="🔍" title="No matches" /> : (
            <div className="flex-col gap-sm">
              {results.map((r, i) => (
                <div key={i} className="card" style={{ padding: 12 }}>
                  <div className="row-between mb-sm">
                    <b style={{ fontSize: 13 }}>{r.file_name || r.source || "Result " + (i + 1)}</b>
                    {r.score != null && <Badge status="accent">score {r.score.toFixed?.(3) ?? r.score}</Badge>}
                  </div>
                  <div className="text-mid" style={{ fontSize: 12.5 }}>{(r.text || r.content || r.chunk || "").slice(0, 300)}</div>
                </div>
              ))}
            </div>
          )
        )}
      </Panel>
      <style>{`.flex-col{display:flex;flex-direction:column}
        .select{background:var(--bg-3);border:1px solid var(--border-soft);border-radius:8px;padding:8px 12px;font-size:13px}`}</style>
    </>
  );
}

function ResultsTab() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  const load = () => AgentAPI.results(50).then((r) => setData(r)).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  return (
    <Panel title="Recent pipeline results" icon="📊"
      actions={<Button size="sm" onClick={load}>🔄 Refresh</Button>}>
      {err && <div className="err-banner">{err}</div>}
      {!data ? <Empty icon="⏳" title="Loading…" /> : data.results?.length ? (
        <div className="flex-col gap-sm">
          {data.results.map((res) => {
            const stages = res.stages || {};
            const cls = stages.classification || {};
            const ing = stages.ingestion || {};
            return (
              <details key={res.pipeline_id} className="result-item">
                <summary>
                  {res.error ? "❌" : "✅"} [{res.pipeline_id}] {res.file_name || "?"} → {cls.classification || "?"} → {ing.target || "?"}
                </summary>
                <div className="grid grid-3 mt-sm mb-sm">
                  <Badge status={stages.extraction?.status}>Extract: {stages.extraction?.status || "?"}</Badge>
                  <Badge status={cls.status}>Classify: {cls.status || "?"}</Badge>
                  <Badge status={ing.status}>Ingest: {ing.status || "?"}</Badge>
                </div>
                <pre className="mono json-block">{JSON.stringify({
                  pipeline_id: res.pipeline_id, started_at: res.started_at, finished_at: res.finished_at,
                  classification: cls.classification, data_type: cls.data_type, confidence: cls.confidence,
                  summary: cls.summary, ingestion: { target: ing.target, chunks: ing.chunks_stored, table: ing.table, error: ing.error },
                }, null, 2)}</pre>
              </details>
            );
          })}
        </div>
      ) : <Empty icon="📭" title="No pipeline runs yet" />}
      <style>{`
        .err-banner{background:var(--bad-dim);color:var(--bad);padding:10px 14px;border-radius:8px;margin-bottom:12px}
        .result-item{background:var(--bg-3);border-radius:10px;padding:12px 16px}
        .result-item summary{cursor:pointer;font-size:13px;font-weight:500}
        .json-block{background:#f8fafc;border:1px solid var(--border-soft);border-radius:8px;padding:12px;font-size:12px;max-height:280px;overflow:auto}
      `}</style>
    </Panel>
  );
}

function LogsTab() {
  const [lines, setLines] = useState(200);
  const [logData, setLogData] = useState(null);
  const [err, setErr] = useState(null);
  const load = () => AgentAPI.logs(lines).then(setLogData).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [lines]);

  return (
    <Panel title="Agent logs" icon="📋"
      actions={<Button size="sm" onClick={load}>🔄 Refresh</Button>}>
      <div className="row gap-sm mb-md">
        <Field label={`Lines: ${lines}`}>
          <input type="range" min={50} max={500} step={10} value={lines} onChange={(e) => setLines(Number(e.target.value))} />
        </Field>
      </div>
      {err && <div className="err-banner">{err}</div>}
      {logData && (
        <>
          <div className="text-mid mb-sm mono" style={{ fontSize: 11.5 }}>
            {logData.log_file} · total lines: {logData.total_lines}
          </div>
          <LogConsole lines={(logData.lines || []).map((l) => l.replace(/\n$/, ""))} height={500} />
        </>
      )}
      <style>{`.err-banner{background:var(--bad-dim);color:var(--bad);padding:10px 14px;border-radius:8px;margin-bottom:12px}`}</style>
    </Panel>
  );
}
