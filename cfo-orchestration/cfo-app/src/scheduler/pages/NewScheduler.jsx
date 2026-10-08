import React, { useState, useEffect, useMemo } from "react";
import { Panel, Card, Field, Input, Select, TextArea, Button, Badge, useToast } from "../components/ui.jsx";
import { SchedulerAPI, ProfileStore, ConnectionsAPI } from "../api.js";
import { uid } from "../lib/format.js";

const FREQS = [
  { key: "minutely", label: "Every N min" },
  { key: "hourly", label: "Hourly" },
  { key: "daily", label: "Daily" },
  { key: "weekly", label: "Weekly" },
  { key: "monthly", label: "Monthly" },
  { key: "cron", label: "Custom Cron" },
];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const TIMEZONES = [
  "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "Europe/London", "Europe/Paris", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore",
  "Asia/Tokyo", "Australia/Sydney",
];

const DEFAULT_TEMPLATE = JSON.stringify(
  {
    scheduler_id: "{{scheduler_id}}",
    scheduler_name: "{{scheduler_name}}",
    scan_id: "{{scan_id}}",
    trigger: "{{trigger}}",
    started_at: "{{started_at}}",
    summary: { total_new: "{{total_new}}", total_modified: "{{total_modified}}", total_files: "{{total_files}}" },
    new_files: "{{new_files_array}}",
    modified_files: "{{modified_files_array}}",
    metadata: { next_run: "{{next_run}}" },
  },
  null,
  2
);

function computeNextRun(cfg) {
  // Lightweight client-side preview only — the authoritative schedule lives in the engine.
  const now = new Date();
  const next = new Date(now);
  try {
    if (cfg.frequency === "minutely") {
      next.setMinutes(next.getMinutes() + (cfg.minutely_interval || 5));
    } else if (cfg.frequency === "hourly") {
      next.setHours(next.getHours() + 1, cfg.hourly_minute || 0, 0);
    } else if (cfg.frequency === "daily") {
      next.setHours(cfg.daily_hour ?? 9, cfg.daily_minute ?? 0, 0);
      if (next <= now) next.setDate(next.getDate() + 1);
    } else if (cfg.frequency === "weekly") {
      const target = cfg.weekly_weekday ?? 0;
      next.setHours(cfg.weekly_hour ?? 9, cfg.weekly_minute ?? 0, 0);
      let diff = (target - ((next.getDay() + 6) % 7) + 7) % 7;
      if (diff === 0 && next <= now) diff = 7;
      next.setDate(next.getDate() + diff);
    } else if (cfg.frequency === "monthly") {
      next.setDate(cfg.monthly_dom || 1);
      next.setHours(cfg.monthly_hour ?? 9, cfg.monthly_minute ?? 0, 0);
      if (next <= now) next.setMonth(next.getMonth() + 1);
    } else {
      return null;
    }
    return next;
  } catch {
    return null;
  }
}

export default function NewScheduler({ editingId, onSaved }) {
  const [profiles, setProfiles] = useState(ProfileStore.all());
  const eid = editingId || null;
  const existing = eid ? profiles[eid] || {} : {};
  const isEdit = !!eid;

  const [name, setName] = useState(existing.name || "");
  const [schedId, setSchedId] = useState(eid || "");
  const [frequency, setFrequency] = useState(existing.frequency || "daily");
  const [tz, setTz] = useState(existing.timezone || "UTC");
  const [cfg, setCfg] = useState({
    minutely_interval: existing.minutely_interval ?? 5,
    hourly_minute: existing.hourly_minute ?? 0,
    daily_hour: existing.daily_hour ?? 9,
    daily_minute: existing.daily_minute ?? 0,
    weekly_weekday: existing.weekly_weekday ?? 0,
    weekly_hour: existing.weekly_hour ?? 9,
    weekly_minute: existing.weekly_minute ?? 0,
    monthly_dom: existing.monthly_dom ?? 1,
    monthly_hour: existing.monthly_hour ?? 9,
    monthly_minute: existing.monthly_minute ?? 0,
    cron_expression: existing.cron_expression || "0 9 * * 1",
  });
  const [jsonTemplate, setJsonTemplate] = useState(existing.json_template || DEFAULT_TEMPLATE);
  const [outputMode, setOutputMode] = useState(existing.output_mode || "Save JSON log");
  const [logDir, setLogDir] = useState(existing.log_dir || "scheduler_logs/");
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const DEFAULT_SQL_LABEL = "(default — SQL_CONNECTION_STRING env var)";
  const DEFAULT_VEC_LABEL = "(default — VECTOR_DB_PATH env var)";
  const [sqlProfiles, setSqlProfiles] = useState([]);
  const [vecProfiles, setVecProfiles] = useState([]);
  const [sqlChoice, setSqlChoice] = useState(DEFAULT_SQL_LABEL);
  const [vecChoice, setVecChoice] = useState(DEFAULT_VEC_LABEL);

  useEffect(() => {
    ConnectionsAPI.listSql().then((r) => setSqlProfiles(r.profiles || [])).catch(() => {});
    ConnectionsAPI.listVector().then((r) => setVecProfiles(r.profiles || [])).catch(() => {});
  }, []);

  // Once profiles have loaded, preselect whichever one this scheduler was
  // already saved with (editing an existing scheduler).
  useEffect(() => {
    if (existing.sql_profile_id && sqlProfiles.length) {
      const match = sqlProfiles.find((p) => p.id === existing.sql_profile_id);
      if (match) setSqlChoice(match.name);
    }
  }, [sqlProfiles]); // eslint-disable-line
  useEffect(() => {
    if (existing.vector_profile_id && vecProfiles.length) {
      const match = vecProfiles.find((p) => p.id === existing.vector_profile_id);
      if (match) setVecChoice(match.name);
    }
  }, [vecProfiles]); // eslint-disable-line

  const sqlOptions = [DEFAULT_SQL_LABEL, ...sqlProfiles.map((p) => p.name)];
  const vecOptions = [DEFAULT_VEC_LABEL, ...vecProfiles.map((p) => p.name)];
  const sqlIdByName = Object.fromEntries(sqlProfiles.map((p) => [p.name, p.id]));
  const vecIdByName = Object.fromEntries(vecProfiles.map((p) => [p.name, p.id]));

  useEffect(() => {
    SchedulerAPI.timezones().catch(() => {});
  }, []);

  const nextRun = useMemo(() => computeNextRun({ frequency, ...cfg }), [frequency, cfg]);

  const update = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  const save = async () => {
    if (!name.trim()) return toast("Scheduler name is required", "error");
    const id = (schedId || name.toLowerCase().replace(/[^a-z0-9]+/g, "-")).trim() || uid("sched");
    setSaving(true);
    try {
      // Push the schedule to the live engine (single active scheduler).
      // scheduler_id lets the backend report scan history back under the
      // same id this UI uses (see Dashboard's per-row matching), instead
      // of the engine's own internal id.
      await SchedulerAPI.updateConfig({
        scheduler_id: id,
        frequency,
        daily_hour: cfg.daily_hour,
        daily_minute: cfg.daily_minute,
        weekly_day: cfg.weekly_weekday,
        weekly_hour: cfg.weekly_hour,
        weekly_minute: cfg.weekly_minute,
        monthly_day_of_month: cfg.monthly_dom,
        monthly_hour: cfg.monthly_hour,
        monthly_minute: cfg.monthly_minute,
        hourly_minute: cfg.hourly_minute,
        minutely_interval: cfg.minutely_interval,
        cron_expression: cfg.cron_expression,
        timezone_name: tz,
        enabled: true,
      });

      let draftSources = existing.sources || [];
      if (!draftSources.length) {
        try { draftSources = JSON.parse(localStorage.getItem("fps_draft_sources") || "[]"); } catch { /* ignore */ }
      }

      // The engine's actual source list lives entirely in backend memory
      // (_scheduler._sources) — until now this was never pushed, so every
      // scan ran with "0 source(s)" no matter what the UI showed. Push
      // each configured source to the live engine here.
      for (const src of draftSources) {
        const { source_id, source_type, ...config } = src;
        if (!source_id || !source_type) continue;
        try {
          await SchedulerAPI.addSource({ source_id, source_type, config });
        } catch (e) {
          toast(`Source "${source_id}" failed to sync: ${e.message}`, "error");
        }
      }

      ProfileStore.save(id, {
        name,
        frequency,
        timezone: tz,
        ...cfg,
        weekly_day_name: WEEKDAYS[cfg.weekly_weekday] || "Monday",
        json_template: jsonTemplate,
        output_mode: outputMode,
        log_dir: logDir,
        sql_profile_id: sqlIdByName[sqlChoice] || null,
        vector_profile_id: vecIdByName[vecChoice] || null,
        status: existing.status || "paused",
        sources: draftSources,
        created_at: existing.created_at || new Date().toISOString(),
      });
      toast(`Scheduler "${name}" saved`, "success");
      onSaved && onSaved(id);
    } catch (e) {
      toast(`Save failed: ${e.message}`, "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Panel title={isEdit ? "Edit scheduler" : "New scheduler"} icon={isEdit ? "✏️" : "➕"}
        subtitle={isEdit ? `Editing ${existing.name || eid} · ${eid}` : "Configure a new pickup schedule"}>
        <div className="section-label">1 · Identity</div>
        <div className="grid grid-2">
          <Field label="Scheduler name" required>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Finance Weekly Digest" />
          </Field>
          <Field label="Unique ID" hint="Auto-generated from name if left blank">
            <Input value={schedId} onChange={(e) => setSchedId(e.target.value)} placeholder="finance-weekly" disabled={isEdit} />
          </Field>
        </div>

        <div className="section-label">2 · Schedule</div>
        <div className="freq-picker">
          {FREQS.map((f) => (
            <button key={f.key} className={`freq-chip ${frequency === f.key ? "freq-chip-active" : ""}`}
              onClick={() => setFrequency(f.key)}>
              {f.label}
            </button>
          ))}
        </div>

        <div className="grid grid-2 mt-md">
          <Field label="Timezone">
            <Select value={tz} onChange={(e) => setTz(e.target.value)}>
              {TIMEZONES.map((z) => <option key={z} value={z}>{z}</option>)}
            </Select>
          </Field>
          <div />
        </div>

        {frequency === "minutely" && (
          <Field label="Run every N minutes">
            <Input type="number" min={1} max={59} value={cfg.minutely_interval}
              onChange={(e) => update("minutely_interval", Number(e.target.value))} />
          </Field>
        )}
        {frequency === "hourly" && (
          <Field label="At minute (0–59)">
            <Input type="number" min={0} max={59} value={cfg.hourly_minute}
              onChange={(e) => update("hourly_minute", Number(e.target.value))} />
          </Field>
        )}
        {frequency === "daily" && (
          <div className="grid grid-2">
            <Field label="Hour (0–23)">
              <Input type="number" min={0} max={23} value={cfg.daily_hour} onChange={(e) => update("daily_hour", Number(e.target.value))} />
            </Field>
            <Field label="Minute (0–59)">
              <Input type="number" min={0} max={59} value={cfg.daily_minute} onChange={(e) => update("daily_minute", Number(e.target.value))} />
            </Field>
          </div>
        )}
        {frequency === "weekly" && (
          <div className="grid grid-3">
            <Field label="Day of week">
              <Select value={cfg.weekly_weekday} onChange={(e) => update("weekly_weekday", Number(e.target.value))}>
                {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </Select>
            </Field>
            <Field label="Hour (0–23)">
              <Input type="number" min={0} max={23} value={cfg.weekly_hour} onChange={(e) => update("weekly_hour", Number(e.target.value))} />
            </Field>
            <Field label="Minute (0–59)">
              <Input type="number" min={0} max={59} value={cfg.weekly_minute} onChange={(e) => update("weekly_minute", Number(e.target.value))} />
            </Field>
          </div>
        )}
        {frequency === "monthly" && (
          <div className="grid grid-3">
            <Field label="Day of month (1–28)">
              <Input type="number" min={1} max={28} value={cfg.monthly_dom} onChange={(e) => update("monthly_dom", Number(e.target.value))} />
            </Field>
            <Field label="Hour (0–23)">
              <Input type="number" min={0} max={23} value={cfg.monthly_hour} onChange={(e) => update("monthly_hour", Number(e.target.value))} />
            </Field>
            <Field label="Minute (0–59)">
              <Input type="number" min={0} max={59} value={cfg.monthly_minute} onChange={(e) => update("monthly_minute", Number(e.target.value))} />
            </Field>
          </div>
        )}
        {frequency === "cron" && (
          <Field label="Cron expression" hint="minute hour day month weekday — e.g. 0 9 * * 1 = every Monday 09:00">
            <Input value={cfg.cron_expression} onChange={(e) => update("cron_expression", e.target.value)} />
          </Field>
        )}

        {nextRun && (
          <div className="next-run-banner">
            ⏰ Next scan (preview): <b>{nextRun.toLocaleString(undefined, { weekday: "long", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</b> ({tz})
          </div>
        )}

        <div className="section-label mt-md">3 · JSON output template</div>
        <Field label="Template" hint="Uses {{name}} placeholder syntax, rendered per scan">
          <TextArea rows={9} value={jsonTemplate} onChange={(e) => setJsonTemplate(e.target.value)} style={{ minHeight: 220 }} />
        </Field>

        <div className="section-label">4 · Output &amp; delivery</div>
        <div className="grid grid-2">
          <Field label="Output mode">
            <Select value={outputMode} onChange={(e) => setOutputMode(e.target.value)}>
              {["Save JSON log", "Email", "Webhook", "Agent callback"].map((m) => <option key={m}>{m}</option>)}
            </Select>
          </Field>
          <Field label="Log directory">
            <Input value={logDir} onChange={(e) => setLogDir(e.target.value)} />
          </Field>
        </div>

        <div className="section-label">5 · Data destination</div>
        <p className="text-mid" style={{ fontSize: 12.5, marginTop: -6, marginBottom: 12 }}>
          Choose which database structured data is ingested into, and where vector embeddings for unstructured
          content are stored. Manage these under 🔌 Data Connections.
        </p>
        <div className="grid grid-2">
          <Field label="SQL database">
            <Select value={sqlChoice} onChange={(e) => setSqlChoice(e.target.value)}>
              {sqlOptions.map((o) => <option key={o} value={o}>{o}</option>)}
            </Select>
          </Field>
          <Field label="Vector store">
            <Select value={vecChoice} onChange={(e) => setVecChoice(e.target.value)}>
              {vecOptions.map((o) => <option key={o} value={o}>{o}</option>)}
            </Select>
          </Field>
        </div>

        <div className="row-end gap-sm mt-md">
          <Button variant="ghost" onClick={() => onSaved && onSaved(null)}>Cancel</Button>
          <Button variant="primary" size="lg" loading={saving} onClick={save}>💾 Save scheduler</Button>
        </div>
      </Panel>

      <style>{`
        .section-label { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
          color: var(--accent-hi); margin: 22px 0 12px; }
        .section-label:first-child { margin-top: 0; }
        .freq-picker { display: flex; gap: 8px; flex-wrap: wrap; }
        .freq-chip { padding: 9px 16px; border-radius: 20px; border: 1px solid var(--border-soft);
          background: var(--bg-4); color: var(--text-mid); font-size: 13px; font-weight: 600; cursor: pointer; }
        .freq-chip-active { background: var(--accent-dim); color: var(--accent-hi); border-color: var(--accent); }
        .next-run-banner { margin-top: 16px; padding: 12px 16px; border-radius: 10px;
          background: var(--good-dim); color: var(--good); font-size: 13.5px; }
      `}</style>
    </>
  );
}
