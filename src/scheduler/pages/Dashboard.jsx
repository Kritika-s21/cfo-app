import React, { useEffect, useState, useCallback } from "react";
import { Panel, Card, Stat, Badge, Button, Empty, useToast, useConfirm } from "../components/ui.jsx";
import { SchedulerAPI, ProfileStore } from "../api.js";
import { freqLabel, fmtDateTime } from "../lib/format.js";

// Groups the flat /api/history list by scheduler_id and returns, per scheduler,
// its most recent scan record — mirrors scheduler_app_1.py's page_dashboard()
// which does: sid_logs = sorted([r for r in all_logs if r["scheduler_id"] == sid], ...)
function lastLogBySched(history) {
  const map = {};
  for (const r of history) {
    const sid = r.scheduler_id;
    if (!sid) continue;
    // Store by both full id and short-8 prefix so frontend profiles
    // that use full ids still match server logs that may carry the
    // 8-char shorthand (legacy behaviour / restart edge-cases).
    const keys = [sid];
    if (sid.length > 8) keys.push(sid.slice(0, 8));
    for (const k of keys) {
      if (!map[k] || (r.started_at || "") > (map[k].started_at || "")) {
        map[k] = r;
      }
    }
  }
  return map;
}

function runsCountBySched(history) {
  const counts = {};
  for (const r of history) {
    const sid = r.scheduler_id;
    if (!sid) continue;
    counts[sid] = (counts[sid] || 0) + 1;
    if (sid.length > 8) {
      const short = sid.slice(0, 8);
      counts[short] = (counts[short] || 0) + 1;
    }
  }
  return counts;
}

export default function Dashboard({ goTo, status }) {
  const [profiles, setProfiles] = useState({});
  const [history, setHistory] = useState([]);
  const [busyId, setBusyId] = useState(null);
  const toast = useToast();
  const [confirm, confirmNode] = useConfirm();
  const [, forceTick] = useState(0);

  useEffect(() => {
    const id = setInterval(() => forceTick((t) => t + 1), 15000);
    return () => clearInterval(id);
  }, []);

  const reload = useCallback(() => {
    setProfiles(ProfileStore.all());
    SchedulerAPI.history().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    reload();
    // Poll frequently so counts/badges reflect a just-triggered scan quickly.
    const id = setInterval(reload, 4000);
    return () => clearInterval(id);
  }, [reload]);

  const list = Object.values(profiles).sort(
    (a, b) => (b.created_at || "").localeCompare(a.created_at || "")
  );

  const lastLogs = lastLogBySched(history);
  const runCounts = runsCountBySched(history);

  // Same aggregation as Streamlit: sum each scheduler's *own* latest scan,
  // not just the single most-recent scan across all schedulers.
  let totalUniqueFiles = 0;
  let totalNewAll = 0;
  for (const s of list) {
    const last = lastLogs[s.id];
    if (last) {
      totalUniqueFiles += last.summary?.total_files ?? 0;
      totalNewAll += last.summary?.total_new ?? 0;
    }
  }
  const activeCount = list.filter((s) => s.status === "running").length;
  const pausedCount = list.filter((s) => (s.status || "paused") !== "running").length;

  const act = async (sid, fn, label) => {
    setBusyId(sid);
    try {
      await fn();
      toast(`${label} succeeded`, "success");
    } catch (e) {
      toast(`${label} failed: ${e.message}`, "error");
    } finally {
      setBusyId(null);
    }
  };

  // The backend's schedule + sources live in memory only and are lost on
  // restart. Re-push both before Start OR Run so either action is
  // self-healing instead of silently running against stale/empty backend
  // state (e.g. "0 source(s)" scans, or "daily" when the UI says "Every 5m").
  const syncBackendConfig = async (sid, sched) => {
    await SchedulerAPI.updateConfig({
      scheduler_id: sid,
      frequency: sched.frequency,
      daily_hour: sched.daily_hour,
      daily_minute: sched.daily_minute,
      weekly_day: sched.weekly_weekday,
      weekly_hour: sched.weekly_hour,
      weekly_minute: sched.weekly_minute,
      monthly_day_of_month: sched.monthly_dom,
      monthly_hour: sched.monthly_hour,
      monthly_minute: sched.monthly_minute,
      hourly_minute: sched.hourly_minute,
      minutely_interval: sched.minutely_interval,
      cron_expression: sched.cron_expression,
      timezone_name: sched.timezone,
      enabled: true,
    });
    for (const src of sched.sources || []) {
      const { source_id, source_type, ...config } = src;
      if (!source_id || !source_type) continue;
      try {
        await SchedulerAPI.addSource({ source_id, source_type, config });
      } catch { /* likely already added — safe to ignore */ }
    }
  };

  const toggle = async (sid, sched) => {
    const running = sched.status === "running";
    await act(
      sid,
      async () => {
        if (running) {
          await SchedulerAPI.pause();
        } else {
          await syncBackendConfig(sid, sched);
          await SchedulerAPI.start();
        }
        ProfileStore.save(sid, {
          ...sched,
          status: running ? "paused" : "running",
          ...(running ? {} : { run_started_at: new Date().toISOString() }),
        });
      },
      running ? "Pause" : "Start"
    );
    reload();
  };

  const runNow = async (sid, sched) => {
    await act(
      sid,
      async () => {
        await syncBackendConfig(sid, sched);
        await SchedulerAPI.triggerNow();
      },
      "Trigger scan"
    );
    // Scan results land in /api/history asynchronously — poll a couple of
    // extra times right after triggering so the row updates without
    // waiting for the full 4s interval.
    reload();
    setTimeout(reload, 1500);
    setTimeout(reload, 4000);
  };

  const remove = async (sid, sched) => {
    if (!(await confirm(`Delete scheduler "${sched.name}"? This can't be undone.`))) return;
    ProfileStore.remove(sid);
    try { await SchedulerAPI.stop(); } catch {}
    toast("Scheduler deleted", "success");
    reload();
  };

  return (
    <>
      {confirmNode}
      <div className="grid grid-4 mb-md" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        <Card><Stat icon="🗂️" tone="accent" label="Schedulers" value={list.length} /></Card>
        <Card><Stat icon="📈" tone="good" label="Total scans" value={history.length} /></Card>
        <Card><Stat icon="📄" tone="violet" label="Total files" value={totalUniqueFiles} /></Card>
        <Card><Stat icon="🆕" tone="accent" label="New (latest scan)" value={totalNewAll} /></Card>
        <Card><Stat icon="🟢" tone="warn" label="Active" value={activeCount} /></Card>
      </div>

      <Panel title="Schedulers" icon="🗂️" subtitle="All configured pickup schedulers">
        {list.length === 0 ? (
          <Empty
            icon="📭"
            title="No schedulers yet"
            hint='Click "New Scheduler" to create your first one.'
          />
        ) : (
          <div className="sched-table">
            {list.map((s) => {
              const last = lastLogs[s.id];
              const runs = runCounts[s.id] || 0;
              const nNew = last?.summary?.total_new ?? 0;
              const nMod = last?.summary?.total_modified ?? 0;
              const nTotal = last?.summary?.total_files ?? 0;
              const errCount = Array.isArray(last?.errors) ? last.errors.length : 0;
              const isActiveBackendProfile = status?.active_scheduler_profile_id === s.id;
              const authoritativeNextRun = isActiveBackendProfile ? status?.next_run_at : null;

              return (
                <div className="sched-row" key={s.id}>
                  <div className="sched-row-main">
                    <div className="sched-name">{s.name}</div>
                    <div className="sched-id mono">{s.id}</div>
                  </div>
                  <Badge status={s.status || "idle"} />
                  <div className="sched-freq text-mid">
                    📆 {freqLabel(s)}
                    {s.status === "running" && (
                      <div style={{ marginTop: 2, fontSize: 11.5 }}>
                        ⏰ next: <b>{authoritativeNextRun ? fmtDateTime(authoritativeNextRun) : "—"}</b>
                      </div>
                    )}
                  </div>

                  <div className="sched-scan text-mid" style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                    {last ? (
                      <>
                        <Badge status="neutral">⬛ {nTotal} files</Badge>
                        <Badge status={nNew ? "success" : "neutral"}>+{nNew} new</Badge>
                        {nMod > 0 && <Badge status="warn">~{nMod} mod</Badge>}
                        {errCount > 0 && <Badge status="error">⚠ {errCount}</Badge>}
                        <span style={{ fontSize: 11 }}>
                          · {runs} run{runs === 1 ? "" : "s"} · last {fmtDateTime(last.finished_at || last.started_at)}
                        </span>
                      </>
                    ) : (
                      <span>— no runs yet</span>
                    )}
                  </div>

                  <div className="row gap-sm">
                    <Button size="sm" variant={s.status === "running" ? "warn" : "success"}
                      loading={busyId === s.id} onClick={() => toggle(s.id, s)}>
                      {s.status === "running" ? "Pause" : "Start"}
                    </Button>
                    <Button size="sm" variant="default" loading={busyId === s.id} onClick={() => runNow(s.id, s)}>⚡ Run</Button>
                    <Button size="sm" variant="ghost" onClick={() => goTo && goTo("new", { editingId: s.id })}>✏️</Button>
                    <Button size="sm" variant="danger" onClick={() => remove(s.id, s)}>🗑</Button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <Panel title="Recent scan activity" icon="📈" subtitle="From /api/history" defaultOpen={list.length > 0}>
        {history.length === 0 ? (
          <Empty icon="📉" title="No scan history yet" />
        ) : (
          <table className="data-table">
            <thead>
              <tr><th>Scheduler</th><th>Scan ID</th><th>Started</th><th>New</th><th>Modified</th><th>Total files</th><th>Status</th></tr>
            </thead>
            <tbody>
              {[...history]
                .sort((a, b) => (b.started_at || "").localeCompare(a.started_at || ""))
                .slice(0, 12)
                .map((r) => (
                  <tr key={`${r.scheduler_id}_${r.scan_id}`}>
                    <td>{r.scheduler_name || r.scheduler_id}</td>
                    <td className="mono">{r.scan_id}</td>
                    <td>{fmtDateTime(r.started_at)}</td>
                    <td>{r.summary?.total_new ?? 0}</td>
                    <td>{r.summary?.total_modified ?? 0}</td>
                    <td>{r.summary?.total_files ?? 0}</td>
                    <td>
                      {Array.isArray(r.errors) && r.errors.length > 0
                        ? <Badge status="error">Error</Badge>
                        : <Badge status="success">OK</Badge>}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}
