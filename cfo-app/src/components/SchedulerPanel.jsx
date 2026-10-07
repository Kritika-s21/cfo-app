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
  local: "#3fb950",
  s3:    "#f97316",
  azure: "#06b6d4",
  gcs:   "#f59e0b",
};

// ── Toggle switch (pure CSS, no external lib) ─────────────────────────────
function Toggle({ on, onChange, disabled }) {
  return (
    <div
      onClick={disabled ? undefined : onChange}
      style={{
        width: 40, height: 22, borderRadius: 11,
        background: on ? "#238636" : "#30363d",
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
  const color   = SOURCE_COLORS[source.type] || "#8b949e";
  const icon    = SOURCE_ICONS[source.type]  || "📦";
  const running = source.running && source.on;

  return (
    <div style={{
      background: "#161b22",
      border: `1px solid ${running ? "#238636" : "#21262d"}`,
      borderLeft: `3px solid ${color}`,
      borderRadius: 8, padding: "14px 16px",
      display: "flex", gap: 12, alignItems: "flex-start",
    }}>
      {/* icon */}
      <div style={{ fontSize: 22, marginTop: 2, flexShrink: 0 }}>{icon}</div>

      {/* body */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "#e6edf3" }}>
            {source.label}
          </span>
          {running && (
            <span style={{
              fontSize: 9, color: "#3fb950", fontWeight: 700,
              background: "#0d2012", borderRadius: 4, padding: "2px 6px",
            }}>● LIVE</span>
          )}
          {source.on && !running && (
            <span style={{
              fontSize: 9, color: "#f97316", fontWeight: 700,
              background: "#1c1007", borderRadius: 4, padding: "2px 6px",
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
          fontSize: 10, color: "#8b949e", fontFamily: "monospace",
          background: "#0d1117", borderRadius: 4, padding: "3px 8px",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {locationString(source)}
        </div>

        {source.notes && (
          <div style={{ fontSize: 11, color: "#c9d1d9", marginTop: 6, fontStyle: "italic" }}>
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
    <span style={{ fontSize: 10, color: "#8b949e" }}>
      <span style={{ color: color || "#6e7681" }}>{label}:</span>{" "}
      <span style={{ color: "#c9d1d9" }}>{value}</span>
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
          <div style={{ fontSize: 22, fontWeight: 700, color: "#e6edf3", letterSpacing: "-0.4px" }}>
            File Ingestion Scheduler
          </div>
          <div style={{ fontSize: 12, color: "#c9d1d9", marginTop: 4 }}>
            Monitors folders, S3, Azure Blob &amp; GCS for new files — auto-routes to CFO agents.
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button
            onClick={reload}
            style={{
              padding: "7px 14px", background: "#21262d",
              border: "1px solid #30363d", borderRadius: 7,
              color: "#e6edf3", fontSize: 12, cursor: "pointer",
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
        background: "#161b22", border: "1px solid #21262d",
        borderRadius: 8, padding: "10px 16px",
      }}>
        <Stat label="Total sources"  value={total}  />
        <Stat label="Active"         value={active} color="#3fb950" />
        <Stat label="Idle"           value={total - active} color="#8b949e" />
        <Stat label="Files ingested" value={schedules.reduce((a,s) => a + (s.seen_count||0), 0)} color="#60a5fa" />
      </div>

      {/* error */}
      {error && (
        <div style={{
          background: "#2d1117", border: "1px solid #f85149", borderRadius: 7,
          padding: "10px 14px", color: "#f85149", fontSize: 12, marginBottom: 16,
        }}>
          ⚠ Could not reach scheduler API: {error}
        </div>
      )}

      {/* loading */}
      {loading && schedules.length === 0 && (
        <div style={{ color: "#8b949e", fontSize: 13 }}>Loading schedules…</div>
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
          background: "#0d1117", border: "1px dashed #30363d",
          borderRadius: 8, fontSize: 11, color: "#8b949e", lineHeight: 1.7,
        }}>
          <strong style={{ color: "#c9d1d9" }}>Tip:</strong> To add a new source, edit{" "}
          <code style={{ color: "#79c0ff" }}>config/schedules.json</code> then click{" "}
          <em>Reload config</em> — no server restart needed. Credentials for cloud
          sources go in your <code style={{ color: "#79c0ff" }}>.env</code> file.
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || "#e6edf3" }}>{value}</div>
      <div style={{ fontSize: 10, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
    </div>
  );
}
