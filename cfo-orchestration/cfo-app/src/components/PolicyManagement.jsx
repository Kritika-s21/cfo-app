// Policy Management — live view of the backend knowledge layer (markdown policies -> LanceDB chunks + graph).
// Replaces the hardcoded PolicyView: every action calls /api/v1/policies*, so the next agent run sees the change.
import { useCallback, useEffect, useRef, useState } from "react";
import { AgentAPI, PolicyAPI } from "../lib/ezcoworker.js";

const C = { bg: "#f8fafc", card: "#ffffff", line: "#e2e8f0", text: "#1e293b", mute: "#64748b", blue: "#1d4ed8", warn: "#c2410c", red: "#dc2626", green: "#15803d" };
const pill = (c) => ({ fontSize: 10, padding: "2px 8px", background: c + "22", border: `1px solid ${c}44`, borderRadius: 10, color: c, fontWeight: 600 });
const btn = (primary) => ({ padding: "6px 14px", background: primary ? "#1f6feb" : C.card, border: primary ? "none" : `1px solid ${C.line}`, borderRadius: 7, color: primary ? "#fff" : C.text, fontSize: 12, fontWeight: 600, cursor: "pointer", fontFamily: "inherit" });
const field = { width: "100%", boxSizing: "border-box", padding: "8px 10px", background: C.bg, border: `1px solid ${C.line}`, borderRadius: 6, color: C.text, fontSize: 12, fontFamily: "inherit" };
const EMPTY = { id: "", name: "", version: "v1.0", category: "Governance", owner: "", critical: false, always_load: false, agents: [], body: "## Rules\n- " };

export default function PolicyManagement() {
  const [policies, setPolicies] = useState([]);
  const [agents, setAgents] = useState([]);
  const [status, setStatus] = useState(null);
  const [tab, setTab] = useState("list");
  const [sel, setSel] = useState(null);
  const [detail, setDetail] = useState({ impact: null, versions: [] });
  const [edit, setEdit] = useState(null);          // policy draft being created/edited
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState(null);            // {ok, text}
  const [busy, setBusy] = useState(false);
  const [probe, setProbe] = useState({ agent: "", query: "", out: null });
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const [p, a, s] = await Promise.all([PolicyAPI.list(), AgentAPI.list(), PolicyAPI.status()]);
      setPolicies(p); setAgents(a); setStatus(s);
    } catch (e) { setMsg({ ok: false, text: `Backend unreachable: ${e.message}` }); }
  }, []);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!sel) return;
    Promise.all([PolicyAPI.impact(sel.id), PolicyAPI.versions(sel.id)])
      .then(([impact, versions]) => setDetail({ impact, versions })).catch(() => setDetail({ impact: null, versions: [] }));
  }, [sel]);

  const run = async (fn, ok) => {
    setBusy(true);
    try { const r = await fn(); setMsg({ ok: true, text: typeof ok === "function" ? ok(r) : ok }); await load(); return r; }
    catch (e) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  };

  const onUpload = async (e) => {
    for (const f of Array.from(e.target.files || [])) {
      await run(async () => PolicyAPI.upload(f.name, await f.text()),
        (r) => `${r.policy} ${r.updated ? "updated (previous version archived)" : "created"}.` +
          (r.unknown_agents.length ? ` Unknown agents ignored: ${r.unknown_agents.join(", ")}.` : "") + (r.warning ? ` ${r.warning}` : ""));
    }
    e.target.value = "";
  };

  const save = () => {
    const { id, body, ...meta } = edit;
    const pid = (id || "").toUpperCase().trim();
    if (!/^POL-\d{3,}$/.test(pid) || !meta.name || !body.trim()) return setMsg({ ok: false, text: "ID like POL-009, a name and a body are required." });
    run(() => (isNew ? PolicyAPI.create(pid, { ...meta, body }) : PolicyAPI.update(pid, { ...meta, body })), `${pid} saved and re-indexed.`)
      .then(() => { setEdit(null); setSel(null); });
  };

  const del = (p) => window.confirm(`Delete ${p.id}? The last version is archived.`) &&
    run(() => PolicyAPI.remove(p.id), `${p.id} deleted.`).then(() => setSel(null));

  const exportMd = (p) => {
    const { body, ...meta } = p;
    const yamlLines = Object.entries(meta).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join("\n");
    const url = URL.createObjectURL(new Blob([`---\n${yamlLines}\n---\n${body}\n`], { type: "text/markdown" }));
    Object.assign(document.createElement("a"), { href: url, download: `${p.id}.md` }).click();
    URL.revokeObjectURL(url);
  };

  const testRetrieval = () => probe.agent && probe.query &&
    AgentAPI.context(probe.agent, probe.query).then((out) => setProbe((x) => ({ ...x, out }))).catch((e) => setMsg({ ok: false, text: e.message }));

  const critical = policies.filter((p) => p.critical).length;
  const agentsLoading = new Set(policies.flatMap((p) => p.agents || [])).size;
  const stats = [["TOTAL POLICIES", policies.length, C.blue], ["CRITICAL", critical, C.warn], ["AGENTS LOADING POLICIES", agentsLoading, C.green],
  ["STORAGE", status ? `Markdown + ${status.vector_backend}` : "…", C.text, `${status?.chunks ?? 0} chunks · ${status?.graph_edges ?? 0} graph edges`]];

  return (
    <div style={{ flex: 1, minHeight: 0, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden", background: C.bg, color: C.text, fontFamily: "inherit" }}>
      <style>{`
        .policy-management-scroll {
          scrollbar-color: #cbd5e1 #ffffff;
          scrollbar-width: auto;
        }
        .policy-management-scroll::-webkit-scrollbar {
          width: 10px;
          height: 10px;
        }
        .policy-management-scroll::-webkit-scrollbar-track {
          background: #ffffff;
        }
        .policy-management-scroll::-webkit-scrollbar-thumb {
          background: #cbd5e1;
          border: 2px solid #ffffff;
          border-radius: 8px;
        }
        .policy-management-scroll::-webkit-scrollbar-thumb:hover {
          background: #94a3b8;
        }
      `}</style>
      <div style={{ padding: "22px 28px 12px" }}>
        <div style={{ fontSize: 22, fontWeight: 700 }}>Policy Management</div>
        <div style={{ fontSize: 12, color: C.mute, marginTop: 5 }}>Markdown policies are the source of truth. Every save re-chunks, re-embeds (LanceDB) and rebuilds the policy graph — the next agent run uses it, no restart.</div>
        <div style={{ display: "flex", gap: 12, marginTop: 12 }}>
          {stats.map(([l, v, c, sub]) => (
            <div key={l} style={{ flex: 1, height: 76, boxSizing: "border-box", background: C.card, border: `1px solid ${C.line}`, borderRadius: 9, padding: "8px 12px", display: "flex", flexDirection: "column", justifyContent: "center" }}>
              <div style={{ fontSize: 9, fontWeight: 700, color: C.mute, letterSpacing: ".08em", marginBottom: 5 }}>{l}</div>
              <div style={{ fontSize: typeof v === "number" ? 23 : 13, fontWeight: 700, color: c }}>{v}</div>
              {sub && <div style={{ fontSize: 9, color: C.mute, marginTop: 3 }}>{sub}</div>}
            </div>))}
        </div>
        {msg && <div onClick={() => setMsg(null)} style={{ marginTop: 12, padding: "8px 12px", borderRadius: 6, fontSize: 12, cursor: "pointer", background: (msg.ok ? C.green : C.red) + "18", border: `1px solid ${(msg.ok ? C.green : C.red)}44`, color: msg.ok ? C.green : C.red }}>{msg.text}</div>}
      </div>

      <div style={{ padding: "0 28px 12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", borderBottom: `1px solid ${C.line}` }}>
          {[["list", "Policy List"], ["matrix", "Agent Matrix"]].map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} style={{ padding: "7px 18px", background: "none", border: "none", borderBottom: `2px solid ${tab === k ? "#1f6feb" : "transparent"}`, color: tab === k ? C.blue : C.mute, fontSize: 13, cursor: "pointer", fontFamily: "inherit" }}>{l}</button>))}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <input ref={fileRef} type="file" accept=".md,text/markdown" multiple style={{ display: "none" }} onChange={onUpload} />
          <button style={btn(false)} disabled={busy} onClick={() => fileRef.current.click()}>⬆ Upload .md File</button>
          <button style={btn(true)} onClick={() => { setIsNew(true); setEdit({ ...EMPTY, id: "POL-" + String(policies.length + 1).padStart(3, "0") }); }}>+ New Policy</button>
        </div>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: "hidden", display: "flex" }}>
        {tab === "list" && (<>
          <div className="policy-management-scroll" style={{ width: sel ? 360 : "100%", minHeight: 0, overflowY: "auto", flexShrink: 0, borderRight: sel ? `1px solid ${C.line}` : "none" }}>
            {policies.map((p) => (
              <div key={p.id} onClick={() => setSel(sel?.id === p.id ? null : p)} style={{ padding: "17px 24px", borderBottom: `1px solid ${C.line}`, cursor: "pointer", background: sel?.id === p.id ? C.card : "transparent", borderLeft: `3px solid ${p.critical ? C.warn : "transparent"}` }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <b style={{ fontSize: 13, color: p.critical ? C.warn : C.blue }}>{p.id}</b>
                  <span style={{ display: "flex", gap: 6 }}>{p.always_load && <span style={{ ...pill(C.green), fontSize: 11 }}>Always loaded</span>}<span style={{ ...pill(C.blue), fontSize: 11 }}>{p.version}</span>{p.critical && <span style={{ ...pill(C.warn), fontSize: 11 }}>Critical</span>}</span>
                </div>
                <div style={{ fontSize: 14, fontWeight: 600 }}>{p.name}</div>
                <div style={{ fontSize: 12, color: C.mute, margin: "4px 0 8px" }}>{p.owner || "—"} · {p.category}{p.effective_date ? ` · effective ${p.effective_date}` : ""}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {(p.agents || []).slice(0, 5).map((a) => <span key={a} style={{ fontSize: 11, padding: "2px 7px", border: `1px solid ${C.blue}33`, borderRadius: 4, color: C.blue }}>{a}</span>)}
                  {(p.agents || []).length > 5 && <span style={{ fontSize: 11, color: C.mute }}>+{p.agents.length - 5} more</span>}
                </div>
              </div>))}
          </div>
          {sel && (
            <div className="policy-management-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "20px 28px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div><b style={{ color: C.warn }}>{sel.id}</b> <span style={pill(C.blue)}>{sel.version}</span><div style={{ fontSize: 20, fontWeight: 700, marginTop: 6 }}>{sel.name}</div></div>
                <div style={{ display: "flex", gap: 6 }}>
                  <button style={btn(false)} onClick={() => { setIsNew(false); setEdit({ ...EMPTY, ...sel }); }}>Edit</button>
                  <button style={btn(false)} onClick={() => exportMd(sel)}>Export .md</button>
                  <button style={{ ...btn(false), color: C.red }} onClick={() => del(sel)}>Delete</button>
                </div>
              </div>
              <pre style={{ whiteSpace: "pre-wrap", fontFamily: "inherit", fontSize: 12.5, lineHeight: 1.7, background: C.card, border: `1px solid ${C.line}`, borderRadius: 8, padding: 14, margin: "16px 0" }}>{sel.body}</pre>
              <div style={{ fontSize: 12, color: C.mute, lineHeight: 1.9 }}>
                <div><b style={{ color: C.text }}>Impact of changing this policy →</b> agents: {(detail.impact?.agents || []).join(", ") || "none"}</div>
                <div>References: {(detail.impact?.references || []).join(", ") || "none"} · Referenced by: {(detail.impact?.referenced_by || []).join(", ") || "none"}</div>
                <div>Archived versions: {detail.versions.length ? detail.versions.map((v) => `${v.revision}`).join(", ") : "none"}</div>
              </div>
            </div>)}
        </>)}

        {tab === "matrix" && (
          <div className="policy-management-scroll" style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "10px 28px 24px" }}>
            <div style={{ fontSize: 12, color: C.mute, marginBottom: 12 }}>Live from the policy graph (APPLIES_TO edges). Green = the agent retrieves this policy. Always-loaded policies reach every agent.</div>
            <table style={{ borderCollapse: "collapse", fontSize: 11 }}>
              <thead><tr><th style={{ textAlign: "left", padding: 8, color: C.mute }}>Agent</th>{policies.map((p) => <th key={p.id} style={{ padding: 8, color: p.critical ? C.warn : C.blue }}>{p.id}</th>)}</tr></thead>
              <tbody>{agents.map((a, i) => (
                <tr key={a.id} style={{ background: i % 2 ? "#f8fafc" : "transparent" }}>
                  <td style={{ padding: "7px 8px", color: "#475569" }}>{a.id}</td>
                  {policies.map((p) => <td key={p.id} style={{ textAlign: "center" }}>{((p.agents || []).includes(a.id) || p.always_load) && <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: "50%", background: "#15803d", opacity: (p.agents || []).includes(a.id) ? 1 : .45 }} title={p.always_load && !(p.agents || []).includes(a.id) ? "always_load" : ""} />}</td>)}
                </tr>))}</tbody>
            </table>
          </div>)}

        {tab === "test" && (
          <div className="policy-management-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "10px 28px 24px" }}>
            <div style={{ fontSize: 12, color: C.mute, marginBottom: 12 }}>Dry-run the knowledge layer: exactly the policy context an agent would receive for a request (skill routing → LanceDB search → graph expansion).</div>
            <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
              <select style={{ ...field, width: 200 }} value={probe.agent} onChange={(e) => setProbe({ ...probe, agent: e.target.value })}><option value="">Agent…</option>{agents.map((a) => <option key={a.id}>{a.id}</option>)}</select>
              <input style={field} placeholder="e.g. Is a ₹12 lakh journal entry allowed without CFO sign-off?" value={probe.query} onChange={(e) => setProbe({ ...probe, query: e.target.value })} />
              <button style={btn(true)} onClick={testRetrieval}>Run</button>
            </div>
            {probe.out && <><div style={{ fontSize: 12, marginBottom: 8 }}>Policies used: {probe.out.policies_used.map((p) => <span key={p} style={{ ...pill(C.blue), marginRight: 4 }}>{p}</span>)}</div>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 11.5, background: C.card, border: `1px solid ${C.line}`, borderRadius: 8, padding: 14 }}>{probe.out.context}</pre></>}
          </div>)}
      </div>

      {edit && (
        <div style={{ position: "fixed", inset: 0, background: "#000a", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50 }}>
          <div style={{ width: 640, maxHeight: "90vh", overflowY: "auto", background: C.card, border: `1px solid ${C.line}`, borderRadius: 12, padding: 22 }}>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 14 }}>{isNew ? "New policy" : `Edit ${edit.id}`}</div>
            <div style={{ display: "grid", gridTemplateColumns: "120px 1fr 90px", gap: 8, marginBottom: 8 }}>
              <input style={field} disabled={!isNew} value={edit.id} onChange={(e) => setEdit({ ...edit, id: e.target.value })} placeholder="POL-009" />
              <input style={field} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} placeholder="Policy name" />
              <input style={field} value={edit.version} onChange={(e) => setEdit({ ...edit, version: e.target.value })} />
            </div>
            <div style={{ display: "flex", gap: 14, fontSize: 12, margin: "10px 0" }}>
              <label><input type="checkbox" checked={!!edit.critical} onChange={(e) => setEdit({ ...edit, critical: e.target.checked })} /> Critical</label>
              <label title="Injected into every run (e.g. POL-008)"><input type="checkbox" checked={!!edit.always_load} onChange={(e) => setEdit({ ...edit, always_load: e.target.checked })} /> Always load</label>
            </div>
            <div style={{ fontSize: 11, color: C.mute, margin: "6px 0 4px" }}>Agents that load this policy</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 5, marginBottom: 10 }}>
              {agents.map((a) => {
                const on = (edit.agents || []).includes(a.id); return (
                  <span key={a.id} onClick={() => setEdit({ ...edit, agents: on ? edit.agents.filter((x) => x !== a.id) : [...(edit.agents || []), a.id] })}
                    style={{ fontSize: 10, padding: "3px 8px", borderRadius: 10, cursor: "pointer", border: `1px solid ${on ? C.blue : C.line}`, background: on ? C.blue + "22" : "transparent", color: on ? C.blue : C.mute }}>{a.id}</span>);
              })}
            </div>
            <div style={{ fontSize: 11, color: C.mute, marginBottom: 4 }}>Markdown body — one <code>## Section</code> per rule group (each becomes a vector chunk); <code>[[POL-003]]</code> links policies in the graph</div>
            <textarea style={{ ...field, height: 220, fontFamily: "monospace" }} value={edit.body} onChange={(e) => setEdit({ ...edit, body: e.target.value })} />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
              <button style={btn(false)} onClick={() => setEdit(null)}>Cancel</button>
              <button style={btn(true)} disabled={busy} onClick={save}>Save &amp; re-index</button>
            </div>
          </div>
        </div>)}
    </div>
  );
}
