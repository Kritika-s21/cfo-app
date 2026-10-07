import React, { useState, useEffect, useCallback } from "react";
import { Panel, Select, Button, Badge, Empty, useToast, useConfirm } from "../components/ui.jsx";
import { SchedulerAPI, ProfileStore } from "../api.js";
import { fmtDateTime } from "../lib/format.js";

export default function Logs() {
  const [history, setHistory] = useState([]);
  const [profiles, setProfiles] = useState({});
  const [filter, setFilter] = useState("All");
  const [openIds, setOpenIds] = useState({});
  const [busyId, setBusyId] = useState(null);
  const toast = useToast();
  const [confirm, confirmNode] = useConfirm();

  const reload = useCallback(() => {
    SchedulerAPI.history().then(setHistory).catch(() => {});
    setProfiles(ProfileStore.all());
  }, []);

  useEffect(() => {
    reload();
    // Extraction runs in a background thread after the scan log is first
    // written, so a one-time fetch on mount can render before content_preview
    // / extraction_method are ready. Poll so entries update in place once
    // the pipeline finishes, the same way Dashboard/SchedulerList already do.
    const id = setInterval(reload, 4000);
    return () => clearInterval(id);
  }, [reload]);

  const visible = filter === "All" ? history : history.filter((r) => r.scheduler_id === filter);

  const deleteOne = async (scanId) => {
    if (!(await confirm("Delete this log? This can't be undone."))) return;
    setBusyId(scanId);
    try {
      await SchedulerAPI.deleteHistoryEntry(scanId);
      toast("Log deleted", "success");
      reload();
    } catch (e) {
      toast(`Delete failed: ${e.message}`, "error");
    } finally {
      setBusyId(null);
    }
  };

  const deleteAll = async () => {
    if (!(await confirm(`Delete all ${visible.length} log(s)? This can't be undone.`))) return;
    try {
      await SchedulerAPI.clearHistory();
      toast("All logs deleted", "success");
      reload();
    } catch (e) {
      toast(`Delete failed: ${e.message}`, "error");
    }
  };

  const download = (r) => {
    const blob = new Blob([JSON.stringify(r, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `scan_${r.scan_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      {confirmNode}
      <Panel title="Scan history" icon="📜" subtitle="Recent scans reported by /api/history">
        <div className="row-between mb-md">
          <Select value={filter} onChange={(e) => setFilter(e.target.value)} style={{ maxWidth: 260 }}>
            <option value="All">All schedulers</option>
            {Object.values(profiles).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
          <div className="row gap-sm">
            <span className="text-mid" style={{ fontSize: 12.5 }}>Showing {visible.length} scan runs</span>
            {visible.length > 0 && <Button size="sm" variant="danger" onClick={deleteAll}>🗑 Delete all</Button>}
          </div>
        </div>

        {visible.length === 0 ? (
          <Empty icon="📉" title="No scan logs yet" hint="Trigger a scan from Dashboard or Schedulers list." />
        ) : (
          <div className="log-list">
            {visible.map((r) => {
              const open = !!openIds[r.scan_id];
              return (
                <div key={r.scan_id} className="log-entry">
                  <button className="log-entry-head" onClick={() => setOpenIds((o) => ({ ...o, [r.scan_id]: !open }))}>
                    <span>{open ? "▾" : "▸"}</span>
                    <span className="mono">{r.scan_id}</span>
                    <span className="text-mid">· {r.scheduler_name || r.scheduler_id}</span>
                    <span className="text-mid">· {fmtDateTime(r.started_at)}</span>
                    <span className="text-mid">· {r.summary?.total_files ?? 0} files</span>
                    <Badge status="neutral">{r.trigger || "scheduled"}</Badge>
                    {(r.summary?.total_new ?? 0) > 0 && <Badge status="success">+{r.summary.total_new} new</Badge>}
                    {(r.summary?.total_modified ?? 0) > 0 && <Badge status="warn">~{r.summary.total_modified} mod</Badge>}
                    {r.extraction_pending && <Badge status="pending">extracting…</Badge>}
                    {Array.isArray(r.errors) && r.errors.length > 0 && <Badge status="error">error</Badge>}
                  </button>
                  {open && (
                    <div className="log-entry-body">
                      <div className="grid grid-4 mb-md">
                        <MiniStat label="Total files" value={r.summary?.total_files ?? 0} />
                        <MiniStat label="New" value={r.summary?.total_new ?? 0} />
                        <MiniStat label="Modified" value={r.summary?.total_modified ?? 0} />
                        <MiniStat label="Duration" value={typeof r.duration_seconds === "number" ? `${r.duration_seconds.toFixed(3)}s` : "—"} />
                      </div>
                      <div className="grid grid-4 mb-md">
                        <MiniStat label="Sources scanned" value={r.sources_scanned ?? "—"} />
                        <MiniStat label="Extraction mode" value={r.extraction_mode || "—"} />
                        <MiniStat label="Frequency" value={r.metadata?.frequency || "—"} />
                        <MiniStat label="Timezone" value={r.metadata?.timezone || "—"} />
                      </div>
                      {Array.isArray(r.errors) && r.errors.length > 0 && (
                        <div className="err-box">⚠️ {r.errors.join(", ")}</div>
                      )}

                      {(r.new_files?.length > 0 || r.modified_files?.length > 0) && (
                        <div className="mb-md">
                          <div className="section-label">Files</div>
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>File</th><th>Type</th><th>Size</th><th>Source</th><th>Status</th>
                                <th>Extraction</th><th>Preview</th>
                              </tr>
                            </thead>
                            <tbody>
                              {[...(r.new_files || []), ...(r.modified_files || [])].map((f, i) => (
                                <tr key={i}>
                                  <td>{f.file_name}</td>
                                  <td>{f.file_type}</td>
                                  <td className="mono" style={{ fontSize: 11.5 }}>{f.file_size_human || f.file_size_bytes}</td>
                                  <td>{f.source_id} <span className="text-low">({f.source_type})</span></td>
                                  <td><Badge status={f.status === "new" ? "success" : "warn"}>{f.status}</Badge></td>
                                  <td>
                                    {f.extraction_error
                                      ? <Badge status="error">error</Badge>
                                      : f.extraction_method
                                        ? <Badge status="neutral">{f.extraction_method}</Badge>
                                        : <span className="text-low">—</span>}
                                  </td>
                                  <td className="mono" style={{ fontSize: 11, maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                                    title={f.content_preview || ""}>
                                    {(f.content_preview || "").slice(0, 80)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}

                      <pre className="json-block mono">{JSON.stringify(r, null, 2)}</pre>
                      <div className="row-end gap-sm mt-md">
                        <Button size="sm" onClick={() => download(r)}>⬇️ Download JSON</Button>
                        <Button size="sm" variant="danger" loading={busyId === r.scan_id} onClick={() => deleteOne(r.scan_id)}>🗑 Delete this log</Button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <style>{`
        .log-list { display: flex; flex-direction: column; gap: 8px; }
        .log-entry { border: 1px solid var(--border-soft); border-radius: 10px; overflow: hidden; }
        .log-entry-head {
          width: 100%; display: flex; align-items: center; gap: 10px; padding: 12px 16px;
          background: var(--bg-3); border: none; color: var(--text-hi); cursor: pointer; font-size: 13px; text-align: left;
        }
        .log-entry-body { padding: 16px; background: var(--bg-2); }
        .json-block { background: #0c1119; border: 1px solid var(--border-soft); border-radius: 8px; padding: 14px;
          font-size: 12px; max-height: 320px; overflow: auto; white-space: pre-wrap; }
        .err-box { background: var(--bad-dim); color: var(--bad); padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; font-size: 13px; }
        .section-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-mid); margin-bottom: 8px; }
      `}</style>
    </>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="mini-stat">
      <div className="mini-stat-value">{value}</div>
      <div className="mini-stat-label">{label}</div>
      <style>{`
        .mini-stat { background: var(--bg-3); border-radius: 8px; padding: 10px 14px; }
        .mini-stat-value { font-family: var(--font-display); font-weight: 700; font-size: 18px; }
        .mini-stat-label { font-size: 11px; color: var(--text-low); margin-top: 2px; }
      `}</style>
    </div>
  );
}
