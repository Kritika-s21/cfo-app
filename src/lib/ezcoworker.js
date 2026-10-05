// Client for OUR backend (backend/main.py). The EzCoworker key stays server-side.
// Optional: VITE_CFO_API_KEY must match one of the server's CFO_API_KEYS (internal deployments only).
const BASE = import.meta.env.VITE_API_BASE || "http://localhost:8765";
const KEY = import.meta.env.VITE_CFO_API_KEY;
const convByChat = {};

async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`${BASE}/api/v1${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...(KEY ? { "X-API-Key": KEY } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
  return res.json();
}

export async function runAgent({ agentId, skill, text, fileContext = "", chatId = "default" }) {
  const data = await api(`/agents/${agentId}/run`, {
    method: "POST",
    body: { text, skill, file_context: fileContext, conversation_id: convByChat[chatId] || null },
  });
  if (data.conversationId) convByChat[chatId] = data.conversationId;
  return data.result;
}

export const AgentAPI = {
  list: () => api("/agents"),
  context: (agentId, query) => api(`/agents/${agentId}/context`, { method: "POST", body: { query } }),
  runs: (agentId) => api(`/runs${agentId ? `?agent_id=${agentId}` : ""}`),
  run: (id) => api(`/runs/${id}`),
};

export const PolicyAPI = {
  list: (q = "") => api(`/policies${q}`),
  get: (id) => api(`/policies/${id}`),
  create: (id, p) => api(`/policies/${id}`, { method: "POST", body: p }),
  update: (id, p) => api(`/policies/${id}`, { method: "PUT", body: p }),
  remove: (id) => api(`/policies/${id}`, { method: "DELETE" }),
  versions: (id) => api(`/policies/${id}/versions`),
  impact: (id) => api(`/policies/${id}/impact`),
  graph: () => api("/policies/graph"),
  search: (query, agentId) => api("/policies/search", { method: "POST", body: { query, agent_id: agentId } }),
  reindex: () => api("/policies/reindex", { method: "POST" }),
  upload: (filename, content) => api("/policies/upload", { method: "POST", body: { filename, content } }),
  status: () => api("/knowledge/status"),
};

export const SkillAPI = {
  list: () => api("/skills"),
  get: (id) => api(`/skills/${id}`),
  save: (id, meta, body) => api(`/skills/${id}`, { method: "PUT", body: { meta, body } }),
  route: (agentId, text) => api("/skills/route", { method: "POST", body: { agent_id: agentId, text } }),
};

export const ScheduleAPI = {
  list: () => api("/schedules"),
  create: (s) => api("/schedules", { method: "POST", body: s }),
  update: (id, s) => api(`/schedules/${id}`, { method: "PUT", body: s }),
  remove: (id) => api(`/schedules/${id}`, { method: "DELETE" }),
  runNow: (id) => api(`/schedules/${id}/run-now`, { method: "POST" }),
};

export const LogAPI = {
  list: (q = "") => api(`/logs${q}`),
  runEvents: (runId) => api(`/runs/${runId}/events`),
};
