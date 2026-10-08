import React, { useState, useEffect, useRef, useCallback, createContext, useContext } from "react";
import "./ui.css";

/* ───────────────────────── Toasts ───────────────────────── */

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  // Stable identity across renders — without useCallback, every push() call
  // (e.g. from a failed API request) re-renders this provider with a new
  // function reference, which re-triggers any consumer's useEffect/useCallback
  // that depends on `toast`, which can call the same failing request again,
  // toast again, and loop indefinitely (seen as a stack of repeated toasts
  // and a page stuck on "Loading…").
  const push = useCallback((msg, kind = "info") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.msg}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ───────────────────────── Panel (minimize / maximize) ───────────────────────── */

export function Panel({ title, icon, subtitle, children, actions, defaultOpen = true, badge }) {
  const [open, setOpen] = useState(defaultOpen);
  const [maximized, setMaximized] = useState(false);

  return (
    <>
      <section className={`panel ${maximized ? "panel-hidden-placeholder" : ""}`}>
        <header className="panel-head">
          <div className="panel-head-left">
            {icon && <span className="panel-icon">{icon}</span>}
            <div>
              <h3 className="panel-title">{title}</h3>
              {subtitle && <div className="panel-subtitle">{subtitle}</div>}
            </div>
            {badge}
          </div>
          <div className="panel-head-right">
            {actions}
            <button
              className="icon-btn"
              title={open ? "Minimize" : "Expand"}
              onClick={() => setOpen((o) => !o)}
            >
              {open ? "–" : "+"}
            </button>
            <button className="icon-btn" title="Maximize" onClick={() => setMaximized(true)}>
              ⤢
            </button>
          </div>
        </header>
        {open && <div className="panel-body">{children}</div>}
      </section>

      {maximized && (
        <div className="panel-overlay" role="dialog" aria-modal="true">
          <div className="panel panel-maximized">
            <header className="panel-head">
              <div className="panel-head-left">
                {icon && <span className="panel-icon">{icon}</span>}
                <div>
                  <h3 className="panel-title">{title}</h3>
                  {subtitle && <div className="panel-subtitle">{subtitle}</div>}
                </div>
                {badge}
              </div>
              <div className="panel-head-right">
                {actions}
                <button className="icon-btn" title="Restore" onClick={() => setMaximized(false)}>
                  ⤡
                </button>
              </div>
            </header>
            <div className="panel-body panel-body-max">{children}</div>
          </div>
        </div>
      )}
    </>
  );
}

/* ───────────────────────── Card ───────────────────────── */

export function Card({ children, className = "", style, onClick }) {
  return (
    <div className={`card ${className}`} style={style} onClick={onClick}>
      {children}
    </div>
  );
}

/* ───────────────────────── Buttons ───────────────────────── */

export function Button({ children, variant = "default", size = "md", icon, loading, ...rest }) {
  return (
    <button className={`btn btn-${variant} btn-${size}`} disabled={loading || rest.disabled} {...rest}>
      {loading ? <span className="spinner" /> : icon}
      <span>{children}</span>
    </button>
  );
}

/* ───────────────────────── Badge ───────────────────────── */

const BADGE_STYLES = {
  running: "good", active: "good", success: "good", ok: "good", completed: "good",
  paused: "warn", pending: "warn", queued: "warn", processing: "warn",
  stopped: "bad", error: "bad", failed: "bad",
  idle: "neutral", structured: "violet", unstructured: "accent",
};

export function Badge({ status, children }) {
  const tone = BADGE_STYLES[String(status || "").toLowerCase()] || "neutral";
  return <span className={`badge badge-${tone}`}>{children ?? status}</span>;
}

/* ───────────────────────── Tabs ───────────────────────── */

export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button
          key={t.key}
          className={`tab ${active === t.key ? "tab-active" : ""}`}
          onClick={() => onChange(t.key)}
        >
          {t.icon} {t.label}
        </button>
      ))}
    </div>
  );
}

/* ───────────────────────── Form primitives ───────────────────────── */

export function Field({ label, hint, required, children }) {
  return (
    <label className="field">
      <span className="field-label">
        {label} {required && <span className="req">*</span>}
      </span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Input(props) {
  return <input className="input" {...props} />;
}
export function Select({ children, ...props }) {
  return (
    <select className="input" {...props}>
      {children}
    </select>
  );
}
export function TextArea(props) {
  return <textarea className="input textarea" {...props} />;
}
export function Toggle({ checked, onChange, label }) {
  return (
    <label className="toggle-row">
      <span className={`toggle ${checked ? "toggle-on" : ""}`} onClick={() => onChange(!checked)}>
        <span className="toggle-knob" />
      </span>
      {label && <span>{label}</span>}
    </label>
  );
}

/* ───────────────────────── Modal / Confirm ───────────────────────── */

export function Modal({ open, onClose, title, children, width = 460 }) {
  if (!open) return null;
  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="modal" style={{ width }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon-btn" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function useConfirm() {
  const [state, setState] = useState(null); // {message, resolve}
  const confirm = (message) =>
    new Promise((resolve) => setState({ message, resolve }));

  const node = state ? (
    <Modal open onClose={() => { state.resolve(false); setState(null); }} title="Please confirm" width={380}>
      <p style={{ color: "var(--text-mid)", marginTop: 0 }}>{state.message}</p>
      <div className="row-end gap-sm">
        <Button variant="ghost" onClick={() => { state.resolve(false); setState(null); }}>Cancel</Button>
        <Button variant="danger" onClick={() => { state.resolve(true); setState(null); }}>Delete</Button>
      </div>
    </Modal>
  ) : null;

  return [confirm, node];
}

/* ───────────────────────── Empty state ───────────────────────── */

export function Empty({ icon = "📭", title, hint }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
    </div>
  );
}

/* ───────────────────────── Stat ───────────────────────── */

export function Stat({ label, value, tone = "accent", icon }) {
  return (
    <div className="stat">
      <div className={`stat-icon stat-${tone}`}>{icon}</div>
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );
}

/* ───────────────────────── Live dot ───────────────────────── */

export function LiveDot({ tone = "good" }) {
  return <span className={`live-dot live-${tone}`} />;
}

/* ───────────────────────── Auto-scrolling log console ───────────────────────── */

export function LogConsole({ lines = [], height = 320 }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines]);
  return (
    <div className="log-console mono" style={{ height }} ref={ref}>
      {lines.length === 0 ? (
        <div className="text-low">No log output yet.</div>
      ) : (
        lines.map((l, i) => <div key={i} className="log-line">{l}</div>)
      )}
    </div>
  );
}
