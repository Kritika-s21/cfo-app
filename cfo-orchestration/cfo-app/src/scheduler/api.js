// ─────────────────────────────────────────────────────────
// api.js — thin client for api_server.py
// Use the backend host directly. You can override with VITE_API_BASE.
// ─────────────────────────────────────────────────────────

// Points at the running FastAPI server. Override with VITE_API_BASE in a
// .env file (e.g. VITE_API_BASE=http://localhost:8765 for local dev, or
// your deployed domain for production) — do not hardcode one or the other.
const BASE = import.meta.env.VITE_API_BASE || "https://ezaicfoagentpysch.ezdatamunch.com";

async function request(path, { method = "GET", body, timeout = 30000 } = {}) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(BASE + path, {
      method,
      credentials: "include",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    clearTimeout(t);
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) {
      throw new Error(data?.detail || data?.error || `Request failed (${res.status})`);
    }
    return data;
  } catch (err) {
    clearTimeout(t);
    if (err.name === "AbortError") throw new Error("Request timed out");
    throw err;
  }
}

// sources.py's scanner (used the instant a source is added/re-synced) reads
// flat top-level config keys for Azure/S3 auth — connection_string, or
// account_name + account_key/sas_token; aws_access_key_id,
// aws_secret_access_key, aws_session_token, region_name. But the
// credentials forms (Sources.jsx) only collect the *nested* shape that
// content_extractor.py's downloader expects later, at extraction time
// (credentials.connection_string_ref, credentials.aws_access_key_id_ref,
// etc). Without this, "Add source" fails immediately with e.g.
// "AzureBlobSource: provide 'connection_string', ...". This derives the
// flat keys the scanner needs from the same nested values so every
// addSource() caller gets a working scan, not just the ones that remember
// to flatten it themselves. Only the two auth methods sources.py's scanner
// actually implements (connection-string/keys for Azure, static access
// keys for S3) can be derived this way — DefaultAzureCredential / IAM-role
// scanning isn't supported by the scanner yet.
function withScannerFields(config, sourceType) {
  const c = (config && config.credentials) || {};
  if (sourceType === "azure") {
    const extra = {};
    if (c.connection_string_ref) extra.connection_string = c.connection_string_ref;
    if (c.account_name) extra.account_name = c.account_name;
    if (c.account_key_ref) extra.account_key = c.account_key_ref;
    if (c.sas_token_ref) extra.sas_token = c.sas_token_ref;
    return { ...config, ...extra };
  }
  if (sourceType === "s3") {
    const extra = {};
    if (c.aws_access_key_id_ref) extra.aws_access_key_id = c.aws_access_key_id_ref;
    if (c.aws_secret_access_key_ref) extra.aws_secret_access_key = c.aws_secret_access_key_ref;
    if (c.aws_session_token_ref) extra.aws_session_token = c.aws_session_token_ref;
    if (c.region) extra.region_name = c.region;
    return { ...config, ...extra };
  }
  return config;
}

/* ── Scheduler engine ── */
export const SchedulerAPI = {
  status: () => request("/api/status"),
  start: () => request("/api/scheduler/start", { method: "POST" }),
  stop: () => request("/api/scheduler/stop", { method: "POST" }),
  pause: () => request("/api/scheduler/pause", { method: "POST" }),
  resume: () => request("/api/scheduler/resume", { method: "POST" }),
  triggerNow: () => request("/api/scheduler/trigger", { method: "POST" }),
  updateConfig: (cfg) => request("/api/config", { method: "PUT", body: cfg }),
  addSource: (source) =>
    request("/api/sources", {
      method: "POST",
      body: { ...source, config: withScannerFields(source.config, source.source_type) },
    }),
  removeSource: (sourceId) =>
    request(`/api/sources/${encodeURIComponent(sourceId)}`, { method: "DELETE" }),
  history: () => request("/api/history"),
  deleteHistoryEntry: (scanId) =>
    request(`/api/history/${encodeURIComponent(scanId)}`, { method: "DELETE" }),
  clearHistory: () => request("/api/history", { method: "DELETE" }),
  timezones: () => request("/api/timezones"),
};

/* ── Scheduler profiles (durable, server-persisted) ── */
const ProfileAPI = {
  all: () => request("/api/profiles"),
  save: (id, profile) => request(`/api/profiles/${encodeURIComponent(id)}`, { method: "PUT", body: profile }),
  remove: (id) => request(`/api/profiles/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

/* ── Ingestion agent ── */
export const AgentAPI = {
  status: () => request("/agent/status"),
  ingestFile: (fileInfo, sqlProfileId, vectorProfileId) => {
    const qs = new URLSearchParams();
    if (sqlProfileId) qs.set("sql_profile_id", sqlProfileId);
    if (vectorProfileId) qs.set("vector_profile_id", vectorProfileId);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request(`/agent/ingest/file${suffix}`, { method: "POST", body: fileInfo, timeout: 120000 });
  },
  ingestBatch: (files, sqlProfileId, vectorProfileId) =>
    request("/agent/ingest/batch", {
      method: "POST",
      body: { files, sql_profile_id: sqlProfileId || null, vector_profile_id: vectorProfileId || null },
      timeout: 300000,
    }),
  structured: (limit = 50, sqlProfileId) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (sqlProfileId) qs.set("sql_profile_id", sqlProfileId);
    return request(`/agent/ingested/structured?${qs.toString()}`);
  },
  vectorStats: (vectorProfileId, sqlProfileId) => {
    const qs = new URLSearchParams();
    if (vectorProfileId) qs.set("vector_profile_id", vectorProfileId);
    if (sqlProfileId) qs.set("sql_profile_id", sqlProfileId);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request(`/agent/ingested/vector/stats${suffix}`);
  },
  vectorSearch: (query, top_k = 8, vectorProfileId) =>
    request("/agent/vector/search", { method: "POST", body: { query, top_k, vector_profile_id: vectorProfileId || null } }),
  results: (limit = 50) => request(`/agent/results?limit=${limit}`),
  logs: (lines = 200) => request(`/agent/logs?lines=${lines}`),
};

/* ── Presentation agent ── */
export const PresentationAPI = {
  schemaPreview: (sqlProfileId, vectorProfileId) => {
    const qs = new URLSearchParams();
    if (sqlProfileId) qs.set("sql_profile_id", sqlProfileId);
    if (vectorProfileId) qs.set("vector_profile_id", vectorProfileId);
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request(`/presentation/schema/preview${suffix}`, { timeout: 60000 });
  },
  generate: (topic, sqlProfileId, vectorProfileId) =>
    request("/presentation/generate", {
      method: "POST",
      body: { topic, sql_profile_id: sqlProfileId || null, vector_profile_id: vectorProfileId || null },
      timeout: 30000,
    }),
  jobs: () => request("/presentation/jobs"),
  job: (id) => request(`/presentation/${id}`),
  downloadUrl: (id) => BASE + `/presentation/${id}/download`,
  manifest: (id) => request(`/presentation/${id}/manifest`),
  retry: (id, topic) =>
    request(`/presentation/${id}/retry`, { method: "POST", body: { topic } }),
};

/* ── Data connections (SQL databases / vector stores) ── */
export const ConnectionsAPI = {
  listSql: () => request("/api/connections/sql"),
  addSql: ({ name, connection_string, db_type }) =>
    request("/api/connections/sql", { method: "POST", body: { name, connection_string, db_type } }),
  testSql: (id) => request(`/api/connections/sql/${encodeURIComponent(id)}/test`, { method: "POST", timeout: 30000 }),
  deleteSql: (id) => request(`/api/connections/sql/${encodeURIComponent(id)}`, { method: "DELETE" }),

  listVector: () => request("/api/connections/vector"),
  addVector: ({ name, db_path, collection_name }) =>
    request("/api/connections/vector", { method: "POST", body: { name, db_path, collection_name } }),
  testVector: (id) => request(`/api/connections/vector/${encodeURIComponent(id)}/test`, { method: "POST", timeout: 30000 }),
  deleteVector: (id) => request(`/api/connections/vector/${encodeURIComponent(id)}`, { method: "DELETE" }),
};

/* ── Scheduler-profile registry ──
   Profiles (name / schedule / sources per saved config) used to live only
   in browser localStorage — clearing site data, a private window, or a
   different browser/device silently wiped every scheduler with no way to
   recover them (see ProfileAPI above, backed by /api/profiles on the
   server, which now persists them to disk the same way scan logs are).

   ProfileStore keeps the same synchronous all()/save()/remove()/get() shape
   every existing page already calls, backed by an in-memory + localStorage
   cache for instant reads, while treating the server as the source of
   truth: writes go to the server in the background, and a periodic
   hydrate() pulls the server's copy back in — so if local storage is ever
   lost, the next hydrate (a few seconds after load) restores it. */
const LS_KEY = "fps_scheduler_profiles_v1";

function readLocalCache() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "{}");
  } catch {
    return {};
  }
}

let _cache = readLocalCache();

function writeLocalCache(all) {
  _cache = all;
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(all));
  } catch {
    /* storage full/unavailable — cache still lives in memory for this session */
  }
}

export const ProfileStore = {
  all() {
    return _cache;
  },
  save(id, profile) {
    const all = { ...ProfileStore.all() };
    const saved = { ...profile, id, updated_at: new Date().toISOString() };
    all[id] = saved;
    writeLocalCache(all);
    // Fire-and-forget: the server is the durable copy. If this fails
    // (offline, server restart mid-save, etc.) the next hydrate() will
    // just re-pull whatever the server last had — worst case this one
    // edit didn't make it to the server and gets overwritten on the next
    // successful hydrate, same as any optimistic-write cache.
    ProfileAPI.save(id, saved).catch((e) => {
      console.warn(`ProfileStore: failed to persist "${id}" to server`, e);
    });
    return saved;
  },
  remove(id) {
    const all = { ...ProfileStore.all() };
    delete all[id];
    writeLocalCache(all);
    ProfileAPI.remove(id).catch((e) => {
      console.warn(`ProfileStore: failed to delete "${id}" on server`, e);
    });
  },
  get(id) {
    return ProfileStore.all()[id] || null;
  },
  /** Pull the durable copy from the server and merge it into the local
   * cache (server wins per-id). Called on load and polled periodically;
   * safe to call as often as you like. */
  async hydrate() {
    try {
      const serverAll = await ProfileAPI.all();
      writeLocalCache({ ...ProfileStore.all(), ...serverAll });
    } catch (e) {
      // Server unreachable — keep using whatever's cached locally.
      console.warn("ProfileStore: hydrate from server failed", e);
    }
  },
};

// Restore from the server as soon as the app loads (self-heals a wiped
// localStorage within one round-trip) and keep it in sync afterwards.
ProfileStore.hydrate();
setInterval(() => ProfileStore.hydrate(), 10000);

export function wsURL(path) {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
