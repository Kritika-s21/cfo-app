/**
 * SchedulerPanel.jsx
 * ==================
 * Drops into App.jsx as the "Scheduler" tab/view.
 * Shows all configured file sources (local, S3, Azure, GCS),
 * their live running state, last-seen count, and a toggle switch.
 *
 * Props
 * -----
 *   token   string   — JWT from login
 *
 * Usage
 * -----
 *   import SchedulerPanel from "./components/SchedulerPanel";
 *   // inside your view router:
 *   {activeView === "scheduler" && <SchedulerPanel token={authToken} />}
 */

import { useScheduler } from "../hooks/useScheduler";

const SOURCE_ICONS = {
  local: "📁",
  s3:    "☁️",
  azure: "🔷",
  gcs:   "🟡",
};

const SOURCE_COLORS = {
  local: "#15803d",
  s3:    "#c2410c",
  azure: "#06b6d4",
  gcs:   "#b45309",
};

// ── Toggle switch (pure CSS, no external lib) ─────────────────────────────
function Toggle({ on, onChange, disabled }) {
  return (
    <div
      onClick={disabled ? undefined : onChange}
      style={{
        width: 40, height: 22, borderRadius: 11,
        background: on ? "#15803d" : "#cbd5e1",
        position: "relative", cursor: disabled ? "not-allowed" : "pointer",
        transition: "background .2s", flexShrink: 0,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <div style={{
        width: 16, height: 16,
        borderRadius: "50%", background: "#fff",
        position: "absolute", top: 3,
        left: on ? 21 : 3,
        transition: "left .2s",
      }} />
    </div>
  );
}

// ── Single source card ────────────────────────────────────────────────────
function SourceCard({ source, onToggle }) {
  const color   = SOURCE_COLORS[source.type] || "#64748b";
  const icon    = SOURCE_ICONS[source.type]  || "📦";
  const running = source.running && source.on;

  return (
    <div style={{
      background: "#ffffff",
      border: `1px solid ${running ? "#15803d" : "#e2e8f0"}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 8, padding: "14px 16px",
      display: "flex", gap: 12, alignItems: "flex-start",
    }}>
      {/* icon */}
      <div style={{ fontSize: 22, marginTop: 2, flexShrink: 0 }}>{icon}</div>

      {/* body */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "#1e293b" }}>
            {source.label}
          </span>
          {running && (
            <span style={{
              fontSize: 9, color: "#15803d", fontWeight: 700,
              background: "#dcfce7", borderRadius: 4, padding: "2px 6px",
            }}>● LIVE</span>
          )}
          {source.on && !running && (
            <span style={{
              fontSize: 9, color: "#c2410c", fontWeight: 700,
              background: "#ffedd5", borderRadius: 4, padding: "2px 6px",
            }}>⏳ STARTING</span>
          )}
        </div>

        {/* meta row */}
        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 16px", marginBottom: 6 }}>
          <MetaPill label="type"     value={source.type.toUpperCase()} color={color} />
          {source.path   && <MetaPill label="path"     value={source.path}   />}
          {source.bucket && <MetaPill label="bucket"   value={source.bucket} />}
          {source.prefix && <MetaPill label="prefix"   value={source.prefix} />}
          <MetaPill label="interval" value={`${source.poll_interval_seconds}s`} />
          <MetaPill label="seen"     value={source.seen_count ?? 0} />
        </div>

        {/* location bar */}
        <div style={{
          fontSize: 10, color: "#64748b", fontFamily: "monospace",
          background: "#f8fafc", borderRadius: 4, padding: "3px 8px",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {locationString(source)}
        </div>

        {source.notes && (
          <div style={{ fontSize: 11, color: "#475569", marginTop: 6, fontStyle: "italic" }}>
            {source.notes}
          </div>
        )}
      </div>

      {/* toggle */}
      <Toggle on={!!source.on} onChange={() => onToggle(source.id)} />
    </div>
  );
}

function MetaPill({ label, value, color }) {
  return (
    <span style={{ fontSize: 10, color: "#64748b" }}>
      <span style={{ color: color || "#64748b" }}>{label}:</span>{" "}
      <span style={{ color: "#475569" }}>{value}</span>
    </span>
  );
}

function locationString(s) {
  if (s.type === "local")  return s.path || "(local path not set)";
  if (s.type === "s3")     return `s3://${s.bucket}/${s.prefix || ""}`;
  if (s.type === "azure")  return `azure://${s.container}/${s.prefix || ""}`;
  if (s.type === "gcs")    return `gcs://${s.bucket}/${s.prefix || ""}`;
  return "";
}

// ── Main panel ────────────────────────────────────────────────────────────
export default function SchedulerPanel({ token }) {
  const { schedules, loading, error, toggle, reload } = useScheduler(token);

  const active = schedules.filter(s => s.on).length;
  const total  = schedules.length;

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px" }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, color: "#1e293b", letterSpacing: "-0.4px" }}>
            File Ingestion Scheduler
          </div>
          <div style={{ fontSize: 12, color: "#475569", marginTop: 4 }}>
            Monitors folders, S3, Azure Blob &amp; GCS for new files — auto-routes to CFO agents.
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button
            onClick={reload}
            style={{
              padding: "7px 14px", background: "#e2e8f0",
              border: "1px solid #cbd5e1", borderRadius: 7,
              color: "#1e293b", fontSize: 12, cursor: "pointer",
              fontFamily: "inherit",
            }}
          >
            ⟳ Reload config
          </button>
        </div>
      </div>

      {/* summary strip */}
      <div style={{
        display: "flex", gap: 16, marginBottom: 20,
        background: "#ffffff", border: "1px solid #e2e8f0",
        borderRadius: 8, padding: "10px 16px",
      }}>
        <Stat label="Total sources"  value={total}  />
        <Stat label="Active"         value={active} color="#15803d" />
        <Stat label="Idle"           value={total - active} color="#64748b" />
        <Stat label="Files ingested" value={schedules.reduce((a,s) => a + (s.seen_count||0), 0)} color="#1d4ed8" />
      </div>

      {/* error */}
      {error && (
        <div style={{
          background: "#fee2e2", border: "1px solid #dc2626", borderRadius: 7,
          padding: "10px 14px", color: "#dc2626", fontSize: 12, marginBottom: 16,
        }}>
          ⚠ Could not reach scheduler API: {error}
        </div>
      )}

      {/* loading */}
      {loading && schedules.length === 0 && (
        <div style={{ color: "#64748b", fontSize: 13 }}>Loading schedules…</div>
      )}

      {/* cards */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {schedules.map(s => (
          <SourceCard key={s.id} source={s} onToggle={toggle} />
        ))}
      </div>

      {/* setup hint */}
      {schedules.length > 0 && (
        <div style={{
          marginTop: 24, padding: "12px 16px",
          background: "#f8fafc", border: "1px dashed #cbd5e1",
          borderRadius: 8, fontSize: 11, color: "#64748b", lineHeight: 1.7,
        }}>
          <strong style={{ color: "#475569" }}>Tip:</strong> To add a new source, edit{" "}
          <code style={{ color: "#1d4ed8" }}>config/schedules.json</code> then click{" "}
          <em>Reload config</em> — no server restart needed. Credentials for cloud
          sources go in your <code style={{ color: "#1d4ed8" }}>.env</code> file.
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || "#1e293b" }}>{value}</div>
      <div style={{ fontSize: 10, color: "#64748b", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
    </div>
  );
}
