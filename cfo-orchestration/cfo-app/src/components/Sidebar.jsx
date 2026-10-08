import { useState } from "react";
import { SCHED_NAV } from "../scheduler/nav.js";
import "./sidebar.css";

const P = {
  logo: <><path d="M12 2 3 7v10l9 5 9-5V7z" /><path d="M12 22V12M3 7l9 5 9-5" /></>,
  dash: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  agents: <><circle cx="9" cy="8" r="3" /><path d="M3 20v-1a6 6 0 0 1 12 0v1z" /><path d="M16 5a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 5v1" /></>,
  shield: <><path d="M12 3 4 6v6c0 4.5 3.2 8 8 9 4.8-1 8-4.5 8-9V6z" /><path d="m9 12 2 2 4-4" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  chev: <path d="m6 9 6 6 6-6" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  x: <path d="M18 6 6 18M6 6l12 12" />,
  trash: <><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" /></>,
  out: <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" /></>,
  panel: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16" /></>,
  // scheduler sub-pages
  sdash: <path d="M3 3v18h18M7 15l4-4 3 3 5-6" />,
  snew: <><circle cx="12" cy="12" r="9" /><path d="M12 8v8M8 12h8" /></>,
  ssrc: <><path d="M9 2v6M15 2v6M7 8h10v4a5 5 0 0 1-10 0z" /><path d="M12 17v5" /></>,
  slogs: <><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" /><path d="M14 3v6h6M8 13h8M8 17h5" /></>,
  sagent: <><path d="M12 3a4 4 0 0 0-4 4v1a3 3 0 0 0-2 5 3 3 0 0 0 2 5 4 4 0 0 0 8 0 3 3 0 0 0 2-5 3 3 0 0 0-2-5V7a4 4 0 0 0-4-4z" /><path d="M12 3v18" /></>,
  sconn: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
  sched: <><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M8 2v4M16 2v4M3 10h18M8 15l2.5 2.5L16 13" /></>,
};
const SCHED_ICON = { dashboard: "sdash", new: "snew", sources: "ssrc", logs: "slogs", agent: "sagent", connections: "sconn" };
const Ic = ({ n }) => <svg viewBox="0 0 24 24">{P[n]}</svg>;

function chatActivityTime(chat) {
  if (!chat.updatedAt) return chat.time || "";
  const updatedAt = new Date(chat.updatedAt);
  if (Number.isNaN(updatedAt.getTime())) return chat.time || "";
  return updatedAt.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function Sidebar({
  view, chats, activeChatId, currentUser,
  schedOpen, setSchedOpen, schedPage,
  onNewChat, onClearAll, onDashboard, onAgentRegistry, onPolicies, onScheduler, onOpenChat, onDeleteChat, onLogout,
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [q, setQ] = useState("");
  const displayName = currentUser?.name || (currentUser?.email ? currentUser.email.split("@")[0] : "User");
  const role = currentUser?.role || currentUser?.email || "Signed in";
  const initials = currentUser?.initials || displayName.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
  const shown = q.trim() ? chats.filter(c => (c.title || "").toLowerCase().includes(q.trim().toLowerCase())) : chats;

  const Item = ({ id, icon, label, on, onClick, right }) => (
    <button className={"sb-item" + (on ? " on" : "")} onClick={onClick} title={collapsed ? label : undefined}>
      <span className="ic"><Ic n={icon} /></span>
      <span className="txt">{label}</span>
      {right}
    </button>
  );

  return (
    <aside className={"sb" + (collapsed ? " collapsed" : "")}>
      <div className="sb-brand">
        <div className="sb-logo"><Ic n="logo" /></div>
        <div className="sb-title"><b>CFO Platform</b><span>14-Module Agentic Finance</span></div>
        <button className="sb-icon-btn" onClick={() => setCollapsed(c => !c)} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}><Ic n="panel" /></button>
      </div>

      <button className="sb-new" onClick={onNewChat} title="New chat"><Ic n="plus" /><span>New chat</span></button>

      <div className="sb-body">
        <div className="sb-label"><span className="l">Overview</span></div>
        <Item icon="dash" label="CFO Intelligence Platform" on={view === "dashboard"} onClick={onDashboard} />

        <div className="sb-label"><span className="l">System</span></div>
        <Item icon="agents" label="Agent Registry" on={view === "agents"} onClick={onAgentRegistry}
          right={<span className="sb-count">19</span>} />
        <Item icon="shield" label="Policy Management" on={view === "policies"} onClick={onPolicies} />

        <div className="sb-label"><span className="l">Automation</span></div>
        <Item icon="sched" label="File Pickup Scheduler" on={view === "scheduler"} onClick={onScheduler}
          right={<span className={"sb-chev" + (schedOpen ? " open" : "")} title={schedOpen ? "Collapse" : "Expand"}
            onClick={e => { e.stopPropagation(); setSchedOpen(o => !o); }}><Ic n="chev" /></span>} />
        {schedOpen && (
          <div className="sb-sub">
            {SCHED_NAV.map(n => (
              <Item key={n.key} icon={SCHED_ICON[n.key] || "sdash"} label={n.label}
                on={view === "scheduler" && schedPage === n.key} onClick={() => onScheduler(n.key)} />
            ))}
          </div>
        )}

        <div className="sb-chats">
          <div className="sb-label"><span className="l">Recent chats <span className="sb-count">{chats.length}</span></span>
            {chats.length > 0 && <button className="sb-icon-btn danger" onClick={onClearAll} title="Clear all chats"><Ic n="trash" /></button>}
          </div>
          {chats.length > 3 && (
            <div className="sb-search"><Ic n="search" /><input value={q} onChange={e => setQ(e.target.value)} placeholder="Search chats…" /></div>
          )}
          {chats.length === 0 && <div className="sb-empty">No chats yet.<br />Ask something or run a workflow and it will appear here.</div>}
          {chats.length > 0 && shown.length === 0 && <div className="sb-empty">No chats match “{q}”.</div>}
          {shown.map(chat => {
            const on = chat.id === activeChatId && view === "chat";
            return (
              <div key={chat.id} className={"sb-chat" + (on ? " on" : "")} onClick={() => onOpenChat(chat)}>
                <span className="dot" />
                <div className="meta"><div className="t">{chat.title}</div><div className="s">{chatActivityTime(chat)}</div></div>
                <button className="x" title="Delete chat" onClick={e => { e.stopPropagation(); onDeleteChat(chat.id); }}><Ic n="x" /></button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="sb-foot">
        <div className="sb-av">{initials}</div>
        <div className="sb-who"><b>{displayName}</b><span>{role}</span></div>
        <button className="sb-icon-btn danger" onClick={onLogout} title="Sign out"><Ic n="out" /></button>
      </div>
      <div className="sb-brand-foot">
        <b>EzInsights AI</b>
        <span>© EzInsights Inc. 2026</span>
      </div>
    </aside>
  );
}
