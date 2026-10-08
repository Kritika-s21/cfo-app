import { useState, useRef, useEffect } from "react";
import { buildWorkflow } from "./skillWorkflows.js";

const C = { bg: "#f8fafc", card: "#ffffff", line: "#e2e8f0", mut: "#64748b", txt: "#1e293b", blue: "#1d4ed8", green: "#047857" };
const STATUS = { MATCHED: "#047857", REVIEW: "#b45309", CONFLICT: "#dc2626", UNMAPPED: "#dc2626" };
const META = new Set(["policy_cited", "source", "answer", "monthly_breakdown", "analysis", "period", "highlight_month"]);

const box = { background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: 18 };
const btn = { background: "#f1f5f9", border: "1px solid #cbd5e1", borderRadius: 7, color: "#475569", padding: "6px 12px", fontSize: 12, cursor: "pointer", fontFamily: "inherit" };
const primary = { ...btn, background: C.blue, color: "#fff", border: "none", padding: "9px 18px" };
const pill = { fontSize: 10.5, border: "1px solid #cbd5e1", borderRadius: 10, padding: "1px 8px", marginRight: 4 };
const th = { textAlign: "left", padding: "10px", fontSize: 10.5, color: C.mut, textTransform: "uppercase", background: "#f1f5f9" };

export default function SkillWorkflowPreview({
  categories, initialSkill, onBack, onSelectSkill,
  agents, skillsByAgent, files = [], onUpload, runSkill,
}) {
  const allSkills = categories.flatMap((c) => c.skills.map((s) => ({ ...s, color: c.color })));
  const skill = initialSkill || allSkills[0];
  const wf = buildWorkflow(skill, agents, skillsByAgent);

  const [tab, setTab] = useState("input");
  const [done, setDone] = useState(0);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [filter, setFilter] = useState("ALL");
  const [q, setQ] = useState("");
  const fileRef = useRef();

  function reset() {
    setTab("input"); setDone(0); setRunning(false); setResult(null); setFilter("ALL"); setQ("");
  }

  // reset whenever a different skill is picked
  useEffect(() => { reset(); }, [skill.name]);

  async function run() {
    setTab("pipeline"); setRunning(true); setDone(0); setResult(null);
    const job = Promise.resolve(runSkill(wf.agent.id, skill.query, files.map((f) => f.name)))
      .catch(() => ({
        analysis: wf.title,
        answer: "The agent could not complete this run. Please try again.",
        policy_cited: wf.agent.policies.join(", "),
      }));
    for (let i = 1; i <= wf.pipeline.length; i++) {
      await new Promise((r) => setTimeout(r, 450));
      setDone(i);
    }
    setResult(await job);
    setRunning(false);
    setTab("output");
  }

  const steps = ["input", "pipeline", "output"];
  const labels = { input: "① Input Files", pipeline: "② Agent Pipeline", output: "③ Output & Dashboard" };

  const stats = wf.stats || Object.entries(result || {})
    .filter(([k, v]) => !META.has(k) && v !== null && v !== undefined)
    .slice(0, 6)
    .map(([k, v]) => [k.replace(/_/g, " "), String(v), C.txt]);

  const statusIdx = (wf.columns || []).indexOf("Status");
  const confIdx = (wf.columns || []).indexOf("Confidence");
  const rows = (wf.rows || []).filter((r) =>
    (filter === "ALL" || statusIdx < 0 || r[statusIdx] === filter) &&
    (!q || r.join(" ").toLowerCase().includes(q.toLowerCase())));

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px", background: C.bg, color: C.txt }}>
      <button onClick={onBack} style={btn}>← Back</button>
      <h1 style={{ fontSize: 26, margin: "12px 0 6px" }}>{wf.title}</h1>
      <p style={{ color: C.mut, fontSize: 13, lineHeight: 1.6, maxWidth: 900 }}>{wf.subtitle}</p>

      {/* breadcrumb + reset */}
      <div style={{ ...box, display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", margin: "16px 0" }}>
        <div style={{ fontSize: 12, color: C.mut }}>
          {steps.map((s, i) => (
            <span key={s} style={{ color: tab === s ? C.txt : (result && s === "output" ? C.green : C.mut), fontWeight: tab === s ? 700 : 400 }}>
              {labels[s]}{s === "output" && result ? " ✓" : ""}{i < 2 ? "  ›  " : ""}
            </span>
          ))}
        </div>
        <button onClick={reset} style={btn}>⟲ Reset</button>
      </div>

      {/* tabs */}
      <div style={{ display: "flex", gap: 22, borderBottom: `1px solid ${C.line}`, marginBottom: 20 }}>
        {steps.map((s) => (
          <div key={s}
            onClick={() => (s === "input" || (s === "pipeline" && done > 0) || (s === "output" && result)) && setTab(s)}
            style={{ padding: "8px 2px", cursor: "pointer", fontSize: 13, color: tab === s ? C.blue : C.mut,
              borderBottom: tab === s ? `2px solid ${C.blue}` : "2px solid transparent" }}>
            {labels[s]} {s === "output" && result && <span style={{ color: C.green }}>●</span>}
          </div>
        ))}
      </div>

      {/* ── TAB 1: INPUT ── */}
      {tab === "input" && (
        <div style={box}>
          <h3 style={{ marginTop: 0 }}>Input Files</h3>
          <div style={{ fontSize: 12, color: C.mut, marginBottom: 10 }}>Expected: {wf.inputs.join(" · ")}</div>
          <input ref={fileRef} type="file" multiple accept=".xlsx,.xls,.csv,.pdf,.json" hidden onChange={onUpload} />
          <button onClick={() => fileRef.current.click()} style={btn}>📎 Attach file</button>
          <div style={{ margin: "12px 0" }}>
            {files.length === 0 && <span style={{ fontSize: 12, color: C.mut }}>No file attached — agent will run on demo data.</span>}
            {files.map((f) => <div key={f.id} style={{ fontSize: 12, padding: "3px 0" }}>📊 {f.name}</div>)}
          </div>
          {wf.systems && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: 10, marginBottom: 14 }}>
              {wf.systems.map((s) => (
                <div key={s.name} style={{ ...box, padding: 12 }}>
                  <b>{s.name}</b>
                  <div style={{ fontSize: 11, color: C.mut, margin: "2px 0 6px" }}>{s.entity}</div>
                  <span style={pill}>{s.cur}</span>
                  <span style={pill}>{s.accts.toLocaleString()} accounts</span>
                </div>
              ))}
            </div>
          )}
          <button onClick={run} style={primary}>▶ Run Agent Pipeline</button>
        </div>
      )}

      {/* ── TAB 2: PIPELINE ── */}
      {tab === "pipeline" && (
        <div>
          <div style={{ ...box, padding: "10px 14px", marginBottom: 14, fontSize: 12 }}>
            <span style={{ color: C.mut, fontWeight: 700 }}>PIPELINE </span>
            {["dispatch", wf.agent.id, "review"].map((a, i) => (
              <span key={a}>
                <span style={{ ...pill, color: C.green, borderColor: C.green }}>{a}</span>{i < 2 && " › "}
              </span>
            ))}
          </div>
          <div style={{ ...box, fontFamily: "Consolas, monospace", fontSize: 12.5 }}>
            <Block color="#b45309" label="DISPATCH" done={done > 0}>
              <Line tag="POLICY" text={`Loaded: ${wf.agent.policies.join(" · ")}`} />
              <Line text={`Route: ${wf.agent.id} → review`} />
            </Block>
            <Block color="#4338ca" label={wf.agent.name.toUpperCase()} done={done >= wf.pipeline.length}>
              {wf.pipeline.slice(0, done).map(([tag, title, detail], i) => (
                <Line key={i} tag={tag} title={title} text={detail} time={`${(0.4 + i * 0.5).toFixed(1)}s`} />
              ))}
              {running && <div style={{ color: "#b45309", padding: "4px 0" }}>⟳ working…</div>}
            </Block>
          </div>
          {result && <button onClick={() => setTab("output")} style={{ ...primary, marginTop: 12 }}>View Output & Dashboard</button>}
        </div>
      )}

      {/* ── TAB 3: OUTPUT ── */}
      {tab === "output" && result && (
        <div>
          <h2 style={{ marginTop: 0 }}>{result.analysis || wf.title}</h2>
          {result.answer && (
            <p style={{ color: "#475569", fontSize: 13.5, lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
              {String(result.answer).replace(/\*\*/g, "")}
            </p>
          )}

          {stats.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 12, margin: "16px 0" }}>
              {stats.map(([label, val, color]) => (
                <div key={label} style={{ ...box, padding: "14px 16px" }}>
                  <div style={{ fontSize: 10.5, color: C.mut, textTransform: "uppercase", letterSpacing: ".06em" }}>{label}</div>
                  <div style={{ fontSize: 24, fontWeight: 800, color, marginTop: 6, wordBreak: "break-word" }}>{val}</div>
                </div>
              ))}
            </div>
          )}

          {result.monthly_breakdown && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(150px,1fr))", gap: 8, marginBottom: 16 }}>
              {Object.entries(result.monthly_breakdown).map(([m, v]) => (
                <div key={m} style={{ ...box, padding: 10 }}>
                  <div style={{ fontSize: 10, color: C.mut }}>{m}</div>
                  <div style={{ fontSize: 12.5, fontWeight: 700 }}>{String(v)}</div>
                </div>
              ))}
            </div>
          )}

          {wf.columns && (
            <div style={box}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12, gap: 10, flexWrap: "wrap" }}>
                <b>{wf.title} — editable table</b>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <input placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} style={{ ...btn, minWidth: 140 }} />
                  {statusIdx >= 0 && ["ALL", "MATCHED", "REVIEW", "CONFLICT", "UNMAPPED"].map((f) => (
                    <button key={f} onClick={() => setFilter(f)}
                      style={{ ...btn, ...(filter === f ? { background: C.blue, color: "#fff", border: "none" } : {}) }}>{f}</button>
                  ))}
                </div>
              </div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                  <thead><tr>{wf.columns.map((c) => <th key={c} style={th}>{c}</th>)}</tr></thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} style={{ borderTop: `1px solid ${C.line}` }}>
                        {r.map((cell, j) => (
                          <td key={j} style={{ padding: "9px 10px" }}>
                            {j === confIdx ? (
                              <span style={{ color: cell > 85 ? C.green : cell > 70 ? "#b45309" : "#dc2626" }}>{cell}%</span>
                            ) : j === statusIdx ? (
                              <span style={{ ...pill, color: STATUS[cell], borderColor: STATUS[cell] }}>{cell}</span>
                            ) : cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                    {rows.length === 0 && (
                      <tr><td colSpan={wf.columns.length} style={{ padding: 14, color: C.mut }}>No rows match.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result.policy_cited && <div style={{ marginTop: 14, fontSize: 11.5, color: C.green }}>🛡️ {result.policy_cited}</div>}
        </div>
      )}

      {/* skill switcher */}
      <div style={{ marginTop: 30, paddingTop: 14, borderTop: `1px solid ${C.line}`, display: "flex", gap: 6, flexWrap: "wrap" }}>
        {allSkills.map((s) => (
          <button key={s.name} onClick={() => onSelectSkill(s)}
            style={{ ...btn, ...(s.name === skill.name ? { borderColor: s.color, color: s.color } : {}) }}>{s.name}</button>
        ))}
      </div>
    </div>
  );
}

function Block({ color, label, done, children }) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ color, fontWeight: 800, marginBottom: 6 }}>
        ◈ {label} {done && <span style={{ ...pill, color: C.green, borderColor: C.green }}>DONE</span>}
      </div>
      <div style={{ borderLeft: `2px solid ${color}55`, paddingLeft: 12 }}>{children}</div>
    </div>
  );
}

function Line({ tag, title, text, time }) {
  return (
    <div style={{ padding: "3px 0", color: "#64748b" }}>
      {tag && <b style={{ color: "#b45309", marginRight: 6 }}>{tag}</b>}
      {title && <b style={{ color: C.txt, marginRight: 6 }}>{title}</b>}
      {title && "— "}{text} {time && <span style={{ color: C.green }}>[{time}]</span>}
    </div>
  );
}
