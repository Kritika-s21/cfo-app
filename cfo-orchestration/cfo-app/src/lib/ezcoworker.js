// Client for OUR backend (backend/main.py). Browser requests use the HttpOnly account session.
const BASE = import.meta.env.VITE_CFO_API_BASE || import.meta.env.VITE_API_BASE || "https://ezaicfoagentpy.ezdatamunch.com/";
const convByChat = {};

async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(`${BASE}/api/v1${path}`, {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    throw new Error(
      "Could not reach the CFO account API. Check that the backend is running and that CFO_CORS_ORIGINS allows this app's URL.",
      { cause },
    );
  }
  if (!res.ok) {
    const error = new Error(res.status === 404
      ? "CFO account API endpoint not found. Deploy or restart the updated backend and check the API base URL."
      : (await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`);
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export const AuthAPI = {
  register: (user) => api("/auth/register", { method: "POST", body: user }),
  login: (credentials) => api("/auth/login", { method: "POST", body: credentials }),
  me: () => api("/auth/me"),
  logout: () => api("/auth/logout", { method: "POST" }),
};

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
  runCount: () => api("/runs/count"),
  latest: () => api("/runs/latest"),     // newest successful run per agent (dashboard cards)
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

// Upload a data file via our backend -> EzCoworker /upload. Returns { remote_path: "input/<name>" }.
export const FileAPI = {
  // Every file in the workspace (chat uploads + scheduler pickups), whatever it is called.
  list: (q = "") => api(`/files${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  forget: (name) => api(`/files/${encodeURIComponent(name)}`, { method: "DELETE" }),
  upload: async (file) => {
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
    return api("/files", { method: "POST", body: { filename: file.name, content_b64: btoa(bin) } });
  },
};
