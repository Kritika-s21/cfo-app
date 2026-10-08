import React, { useState, useEffect } from "react";
import "./theme.css";
import "./shell.css";
import "./components/ui.css";
import { ToastProvider } from "./components/ui.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { SchedulerAPI } from "./api.js";
import { SCHED_NAV } from "./nav.js";

import Dashboard from "./pages/Dashboard.jsx";
import NewScheduler from "./pages/NewScheduler.jsx";
import Sources from "./pages/Sources.jsx";
import Logs from "./pages/Logs.jsx";
import IngestionAgent from "./pages/IngestionAgent.jsx";
import DataConnections from "./pages/DataConnections.jsx";

const PAGES = {
  dashboard: Dashboard,
  new: NewScheduler,
  sources: Sources,
  logs: Logs,
  agent: IngestionAgent,
  connections: DataConnections,
};

/**
 * File Pickup Scheduler, embedded in the CFO Back Office shell.
 * Navigation lives in the CFO left panel; `page` / `editingId` are owned by App.
 */
export default function SchedulerApp({ page = "dashboard", editingId = null, navigate }) {
  const [status, setStatus] = useState(null);
  const [connErr, setConnErr] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const s = await SchedulerAPI.status();
        if (alive) { setStatus(s); setConnErr(false); }
      } catch {
        if (alive) setConnErr(true);
      }
    };
    poll();
    const id = setInterval(poll, 8000);
    return () => { alive = false; clearInterval(id); };
  }, []);

  const Comp = PAGES[page] || Dashboard;
  const label = SCHED_NAV.find((n) => n.key === page)?.label;
  const engine = status?.status || "unknown";

  return (
    <div className="sch-root" style={{ flex: 1, minWidth: 0 }}>
      <ToastProvider>
        <div className="content-inner">
          <div className="row-between mb-md">
            <div className="row gap-sm" style={{ fontSize: 12, color: "var(--text-low)" }}>
              <span style={{ color: connErr ? "var(--bad)" : "var(--good)" }}>●</span>
              <span>{connErr ? "Disconnected from api_server.py" : `Connected · Engine: ${engine}`}</span>
              <span>/</span>
              <span style={{ color: "var(--text-mid)" }}>{page === "new" && editingId ? "Edit Scheduler" : label}</span>
            </div>
            {page !== "new" && (
              <button
                onClick={() => navigate("new")}
                style={{ background: "var(--accent)", color: "#fff", border: "none", borderRadius: 6, padding: "6px 12px", fontWeight: 600, fontSize: 12, cursor: "pointer" }}
              >
                ＋ New Scheduler
              </button>
            )}
          </div>

          <ErrorBoundary key={page + (editingId || "")}>
            <Comp
              status={status}
              editingId={editingId}
              goTo={navigate}
              onSaved={(id) => navigate("dashboard")}
            />
          </ErrorBoundary>
        </div>
      </ToastProvider>
    </div>
  );
}
