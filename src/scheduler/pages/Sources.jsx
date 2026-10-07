import React, { useState, useEffect } from "react";
import { Panel, Card, Field, Input, Select, Toggle, Button, Badge, Empty, useToast, useConfirm } from "../components/ui.jsx";
import { SchedulerAPI, ProfileStore } from "../api.js";
import { uid } from "../lib/format.js";

const SOURCE_TYPES = ["local", "gdrive", "azure", "s3"];
const SOURCE_ICONS = { local: "💻", gdrive: "🗄️", azure: "🔷", s3: "🪣" };
const SOURCE_COLORS = { local: "#48c9d8", gdrive: "#34a853", azure: "#0078d4", s3: "#ff9900" };

function emptySource(type = "local") {
  return { source_id: "", source_type: type, pattern: "*", credentials: {}, path: "", recursive: true };
}

function SourceForm({ value, onChange }) {
  const v = value;
  const set = (patch) => onChange({ ...v, ...patch });

  return (
    <Card className="mb-md">
      <div className="grid grid-3 mb-sm">
        <Field label="Source type">
          <Select value={v.source_type} onChange={(e) => set({ source_type: e.target.value })}>
            {SOURCE_TYPES.map((t) => <option key={t} value={t}>{SOURCE_ICONS[t]} {t.toUpperCase()}</option>)}
          </Select>
        </Field>
        <Field label="Source ID" required>
          <Input value={v.source_id} onChange={(e) => set({ source_id: e.target.value })} placeholder="my-source" />
        </Field>
        <Field label="File pattern (glob)">
          <Input value={v.pattern} onChange={(e) => set({ pattern: e.target.value })} />
        </Field>
      </div>

      {v.source_type === "local" && (
        <>
          <div className="hint-box">📁 Scans a folder on the server running the app — not your local machine.</div>
          <div className="grid grid-2">
            <Field label="Folder path (on server)">
              <Input value={v.path || ""} onChange={(e) => set({ path: e.target.value })} placeholder="/data/incoming" />
            </Field>
            <Field label="Recursive">
              <Toggle checked={v.recursive ?? true} onChange={(val) => set({ recursive: val })} label={v.recursive ? "Yes" : "No"} />
            </Field>
          </div>
        </>
      )}

      {v.source_type === "gdrive" && (
        <>
          <div className="hint-box">
            ℹ️ Paste the folder ID from drive.google.com/drive/folders/&lt;folder_id&gt;. Share the folder with the service account email as Viewer.
          </div>
          <div className="grid grid-3">
            <Field label="Folder ID"><Input value={v.folder_id || ""} onChange={(e) => set({ folder_id: e.target.value })} /></Field>
            <Field label="Recursive"><Toggle checked={v.recursive ?? true} onChange={(val) => set({ recursive: val })} /></Field>
            <Field label="Include Shared Drives"><Toggle checked={v.include_shared_drives ?? true} onChange={(val) => set({ include_shared_drives: val })} /></Field>
          </div>
          <GdriveCredsForm creds={v.credentials} onChange={(c) => set({ credentials: c })} label="Google Drive" />
        </>
      )}

      {v.source_type === "azure" && (
        <>
          <div className="grid grid-2">
            <Field label="Container name"><Input value={v.container || ""} onChange={(e) => set({ container: e.target.value })} /></Field>
            <Field label="Prefix / virtual folder"><Input value={v.prefix || ""} onChange={(e) => set({ prefix: e.target.value })} /></Field>
          </div>
          <AzureCredsForm creds={v.credentials} onChange={(c) => set({ credentials: c })} />
        </>
      )}

      {v.source_type === "s3" && (
        <>
          <div className="grid grid-2">
            <Field label="Bucket name"><Input value={v.bucket || ""} onChange={(e) => set({ bucket: e.target.value })} /></Field>
            <Field label="Prefix / folder"><Input value={v.prefix || ""} onChange={(e) => set({ prefix: e.target.value })} /></Field>
          </div>
          <S3CredsForm creds={v.credentials} onChange={(c) => set({ credentials: c })} />
        </>
      )}
    </Card>
  );
}

// NOTE ON FIELD NAMES:
// The backend (scheduler_app_1.py / api_server.py) expects specific credential
// key names per source type and an explicit `auth_method`. Sending the wrong
// key name (e.g. "connection_string" instead of "connection_string_ref") means
// the backend can't find the secret and the source silently fails at scan time
// even though "Add source" appears to succeed. These forms match the backend
// exactly:
//   gdrive : { auth_method: "service_account" | "oauth", sa_json_encrypted }
//   azure  : { auth_method: "service_account" | "oauth", connection_string_ref | account_name }
//   s3     : { auth_method: "service_account" | "iam_role", region,
//              aws_access_key_id_ref, aws_secret_access_key_ref, aws_session_token_ref | role_arn }

function AuthPanel({ title, children }) {
  return (
    <div className="auth-panel">
      <div className="auth-panel-header">{title}</div>
      {children}
    </div>
  );
}

function SecretInputWithUpload({ label, value, onChange, placeholder, fileTypes = ".json" }) {
  // Mirrors scheduler_app_1.py's _secret_input_with_upload: a Paste/Upload
  // toggle, where Upload reads the file's contents directly into the same
  // field the Paste textarea would have set.
  const [mode, setMode] = useState("paste");
  const inputId = React.useId ? React.useId() : `${label}-upload`;

  const handleFile = (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => onChange(String(reader.result || ""));
    reader.onerror = () => onChange("");
    reader.readAsText(file);
    // allow re-selecting the same file later and still fire onChange
    e.target.value = "";
  };

  return (
    <div>
      <div className="row gap-sm mb-sm">
        <label className="row gap-xs" style={{ fontSize: 12.5 }}>
          <input type="radio" name={`${label}-mode`} checked={mode === "paste"}
            onChange={() => setMode("paste")} />
          Paste
        </label>
        <label className="row gap-xs" style={{ fontSize: 12.5 }}>
          <input type="radio" name={`${label}-mode`} checked={mode === "upload"}
            onChange={() => setMode("upload")} />
          Upload file
        </label>
      </div>
      {mode === "paste" ? (
        <Field label={label}>
          <Input type="password" value={value || ""}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder} />
        </Field>
      ) : (
        <Field label={label}>
          <input id={inputId} type="file" accept={fileTypes} onChange={handleFile} />
          {value ? (
            <div className="text-mid" style={{ fontSize: 11.5, marginTop: 4 }}>
              ✅ File loaded ({value.length.toLocaleString()} chars) — replaces the credential saved for this source only.
            </div>
          ) : (
            <div className="text-mid" style={{ fontSize: 11.5, marginTop: 4 }}>
              Leave empty to keep the credential already saved for this source.
            </div>
          )}
        </Field>
      )}
    </div>
  );
}

function GdriveCredsForm({ creds, onChange, label }) {
  const c = creds || {};
  const method = c.auth_method || "service_account";
  const set = (patch) => onChange({ ...c, ...patch });

  return (
    <AuthPanel title={`${label} — Authentication`}>
      <Field label="Auth method">
        <Select value={method} onChange={(e) => set({ auth_method: e.target.value })}>
          <option value="service_account">Service Account</option>
          <option value="oauth">OAuth (Application Default Credentials)</option>
        </Select>
      </Field>
      {method === "service_account" ? (
        <>
          <div className="hint-box mb-sm">
            🔒 Paste or upload the service account JSON key — encrypted and stored with this source only.
            The folder (or its Shared Drive) must be shared with the service account's client_email as at
            least Viewer, or nothing will be found.
          </div>
          <SecretInputWithUpload
            label="Service account JSON"
            value={c.sa_json_encrypted}
            onChange={(val) => set({ sa_json_encrypted: val })}
            placeholder='{"type": "service_account", "project_id": "...", ...}'
            fileTypes=".json,application/json"
          />
        </>
      ) : (
        <div className="hint-box">
          ℹ️ Application Default Credentials — the server's own identity is used; no secret stored here.
        </div>
      )}
    </AuthPanel>
  );
}

function AzureCredsForm({ creds, onChange }) {
  const c = creds || {};
  const method = c.auth_method || "service_account";
  const set = (patch) => onChange({ ...c, ...patch });

  return (
    <AuthPanel title="Azure Blob Storage — Authentication">
      <Field label="Auth method">
        <Select value={method} onChange={(e) => set({ auth_method: e.target.value })}>
          <option value="service_account">Service Account</option>
          <option value="oauth">OAuth (DefaultAzureCredential)</option>
        </Select>
      </Field>
      {method === "service_account" ? (
        <>
          <div className="hint-box mb-sm">🔒 Paste the connection string — encrypted and stored with this source only.</div>
          <Field label="Connection string">
            <Input type="password" value={c.connection_string_ref || ""}
              onChange={(e) => set({ connection_string_ref: e.target.value })}
              placeholder="DefaultEndpointsProtocol=https;AccountName=..." />
          </Field>
        </>
      ) : (
        <>
          <div className="hint-box mb-sm">ℹ️ DefaultAzureCredential — the server's own managed identity is used; no secret stored here.</div>
          <Field label="Storage account name">
            <Input value={c.account_name || ""} onChange={(e) => set({ account_name: e.target.value })} />
          </Field>
        </>
      )}
    </AuthPanel>
  );
}

function S3CredsForm({ creds, onChange }) {
  const c = creds || {};
  const method = c.auth_method || "service_account";
  const set = (patch) => onChange({ ...c, ...patch });

  return (
    <AuthPanel title="AWS S3 — Authentication">
      <div className="grid grid-2">
        <Field label="Auth method">
          <Select value={method} onChange={(e) => set({ auth_method: e.target.value })}>
            <option value="service_account">Service Account</option>
            <option value="iam_role">IAM Role</option>
          </Select>
        </Field>
        <Field label="AWS region (optional)">
          <Input value={c.region || ""} onChange={(e) => set({ region: e.target.value })} placeholder="us-east-1" />
        </Field>
      </div>
      {method === "service_account" ? (
        <>
          <div className="hint-box mb-sm">🔒 Paste the Access Key ID and Secret — encrypted and stored with this source only.</div>
          <div className="grid grid-2">
            <Field label="Access Key ID">
              <Input type="password" value={c.aws_access_key_id_ref || ""}
                onChange={(e) => set({ aws_access_key_id_ref: e.target.value })} placeholder="AKIA..." />
            </Field>
            <Field label="Secret Access Key">
              <Input type="password" value={c.aws_secret_access_key_ref || ""}
                onChange={(e) => set({ aws_secret_access_key_ref: e.target.value })} />
            </Field>
          </div>
          <Field label="Session Token (optional)">
            <Input type="password" value={c.aws_session_token_ref || ""}
              onChange={(e) => set({ aws_session_token_ref: e.target.value })} />
          </Field>
        </>
      ) : (
        <>
          <div className="hint-box mb-sm">ℹ️ STS AssumeRole — no long-lived keys stored; the server assumes this role at scan time.</div>
          <Field label="IAM Role ARN">
            <Input value={c.role_arn || ""} onChange={(e) => set({ role_arn: e.target.value })}
              placeholder="arn:aws:iam::123456789012:role/MyRole" />
          </Field>
        </>
      )}
    </AuthPanel>
  );
}

export default function Sources() {
  const [drafts, setDrafts] = useState(() => {
    try { return JSON.parse(localStorage.getItem("fps_draft_sources") || "[]"); } catch { return []; }
  });
  const [forms, setForms] = useState([emptySource()]);
  const [profiles, setProfiles] = useState(ProfileStore.all());
  const toast = useToast();
  const [confirm, confirmNode] = useConfirm();

  useEffect(() => {
    localStorage.setItem("fps_draft_sources", JSON.stringify(drafts));
  }, [drafts]);

  const addFormRow = () => setForms((f) => [...f, emptySource()]);
  const updateForm = (i, val) => setForms((f) => f.map((x, idx) => (idx === i ? val : x)));

  const commitDrafts = async () => {
    const missing = forms.some((f) => !f.source_id.trim());
    if (missing) return toast("Every source needs a Source ID", "error");

    let added = 0;
    for (const src of forms) {
      try {
        await SchedulerAPI.addSource({ source_type: src.source_type, source_id: src.source_id, config: src });
      } catch (e) {
        toast(`${src.source_id}: ${e.message}`, "error");
        continue;
      }
      setDrafts((d) => [...d, { ...src, _uid: uid("src") }]);
      added++;
    }
    if (added) {
      toast(`${added} source(s) added`, "success");
      setForms([emptySource()]);
    }
  };

  const removeDraft = async (i, src) => {
    setDrafts((d) => d.filter((_, idx) => idx !== i));
    try { await SchedulerAPI.removeSource(src.source_id); } catch {}
  };

  const clearAll = async () => {
    if (!(await confirm("Clear all draft sources?"))) return;
    setDrafts([]);
  };

  const attachedProfiles = Object.values(profiles).filter((p) => (p.sources || []).length);

  return (
    <>
      {confirmNode}
      <Panel title="Add sources" icon="🔌" subtitle="Configure a source, then attach it to a scheduler">
        <div className="hint-box mb-md">
          🔒 Credentials are sent to <code className="mono">/api/sources</code> and encrypted individually per source by the
          scheduler engine — never shared between sources.
        </div>
        {forms.map((f, i) => (
          <SourceForm key={i} value={f} onChange={(val) => updateForm(i, val)} />
        ))}
        <div className="row gap-sm">
          <Button variant="default" onClick={addFormRow}>+ Add another source</Button>
          <Button variant="primary" onClick={commitDrafts}>✅ Add source(s) to draft</Button>
        </div>
      </Panel>

      <Panel title="Connected sources (draft)" icon="📦" subtitle="Sources ready to attach to a scheduler">
        {drafts.length === 0 ? (
          <Empty icon="📭" title="No sources in draft yet" />
        ) : (
          <>
            <div className="flex-col gap-sm">
              {drafts.map((src, i) => {
                const color = SOURCE_COLORS[src.source_type] || "#888";
                const location = src.source_type === "local"
                  ? (src.path || "—")
                  : `${src.bucket || src.container || src.folder_id || "—"}/${src.prefix || ""}`.replace(/\/$/, "");
                return (
                  <div key={src._uid || i} className="source-chip" style={{ borderLeftColor: color }}>
                    <div className="row gap-sm">
                      <span>{SOURCE_ICONS[src.source_type]}</span>
                      <b>{src.source_id}</b>
                      <Badge status="neutral">{src.source_type.toUpperCase()}</Badge>
                    </div>
                    <div className="mono text-mid" style={{ fontSize: 12 }}>📂 {location} · 🔍 {src.pattern || "*"}</div>
                    <Button size="sm" variant="danger" onClick={() => removeDraft(i, src)}>🗑</Button>
                  </div>
                );
              })}
            </div>
            <Button variant="ghost" className="mt-md" onClick={clearAll}>🗑 Clear all draft sources</Button>
          </>
        )}
      </Panel>

      {attachedProfiles.length > 0 && (
        <Panel title="Sources attached to saved schedulers" icon="🗂️" defaultOpen={false}>
          {attachedProfiles.map((p) => (
            <div key={p.id} className="mb-md">
              <div className="row-between mb-sm">
                <b>{p.name}</b>
                <span className="mono text-low" style={{ fontSize: 11.5 }}>{p.id}</span>
              </div>
              {(p.sources || []).map((src, i) => (
                <div key={i} className="text-mid" style={{ fontSize: 12.5, padding: "4px 0" }}>
                  {SOURCE_ICONS[src.source_type] || "📦"} <b>{src.source_id}</b> · {src.source_type?.toUpperCase()}
                </div>
              ))}
            </div>
          ))}
        </Panel>
      )}

      <style>{`
        .hint-box { background: var(--accent-dim); color: var(--accent-hi); border-radius: 8px; padding: 10px 14px; font-size: 12.5px; }
        .flex-col { display: flex; flex-direction: column; }
        .auth-panel { border: 1px solid var(--border-soft); border-radius: 10px; padding: 14px; margin-top: 12px; background: var(--bg-3); }
        .auth-panel-header { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-mid); margin-bottom: 10px; }
        .source-chip {
          display: grid; grid-template-columns: 1fr 1fr auto; align-items: center; gap: 10px;
          background: var(--bg-3); border-left: 4px solid var(--accent); border-radius: 8px; padding: 10px 14px;
        }
      `}</style>
    </>
  );
}
