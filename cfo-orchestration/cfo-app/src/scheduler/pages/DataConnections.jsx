import React, { useState, useEffect, useCallback } from "react";
import { Panel, Card, Tabs, Button, Input, Select, TextArea, Field, Empty, Badge, useToast } from "../components/ui.jsx";
import { ConnectionsAPI } from "../api.js";

export default function DataConnections() {
  const [tab, setTab] = useState("sql");

  return (
    <>
      <Panel title="🔌 Data Connections" icon="🔌"
        subtitle="Connect your own SQL databases and choose where vector data is stored, instead of the single database/folder baked into the environment. Pick a connection per-scheduler (Sources page) and per-presentation (Presentation Generator page).">
        <Tabs
          active={tab}
          onChange={setTab}
          tabs={[
            { key: "sql", label: "SQL Databases", icon: "🗄" },
            { key: "vector", label: "Vector Stores", icon: "🧠" },
          ]}
        />
        <div className="mt-md">
          {tab === "sql" && <SqlProfiles />}
          {tab === "vector" && <VectorProfiles />}
        </div>
      </Panel>
    </>
  );
}

// Builds the exact connection-string format the backend already parses:
// semicolon "Server=...;Database=...;UID=...;PWD=..." for SQL Server (see
// db_connections.py / sql_ingestion.py's _parse_sql_server_credentials and
// _to_sqlalchemy_url), or a SQLAlchemy URL for postgres/mysql.
function buildConnStr(dbType, { host, port, database, username, password }) {
  const h = host.trim();
  const db = database.trim();
  const u = username.trim();
  const p = password;
  if (dbType === "mssql") {
    const server = port ? `${h},${port}` : h;
    return `Server=${server};Database=${db};UID=${u};PWD=${p};Driver={ODBC Driver 17 for SQL Server}`;
  }
  const enc = (s) => encodeURIComponent(s);
  if (dbType === "postgresql") {
    return `postgresql://${enc(u)}:${enc(p)}@${h}:${port || 5432}/${db}`;
  }
  if (dbType === "mysql") {
    return `mysql+pymysql://${enc(u)}:${enc(p)}@${h}:${port || 3306}/${db}`;
  }
  return "";
}

const DB_DEFAULTS = {
  mssql: { port: "1433", label: "SQL Server" },
  postgresql: { port: "5432", label: "PostgreSQL" },
  mysql: { port: "3306", label: "MySQL" },
};

function SqlProfiles() {
  const [profiles, setProfiles] = useState(null);
  const [name, setName] = useState("");
  const [dbType, setDbType] = useState("mssql");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(DB_DEFAULTS.mssql.port);
  const [database, setDatabase] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [rawConnStr, setRawConnStr] = useState("");
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const toast = useToast();

  const onDbTypeChange = (v) => {
    setDbType(v);
    if (DB_DEFAULTS[v]) setPort(DB_DEFAULTS[v].port);
  };

  const load = useCallback(() => {
    ConnectionsAPI.listSql().then((r) => setProfiles(r.profiles || [])).catch((e) => toast(e.message, "error"));
  }, [toast]);
  useEffect(() => { load(); }, [load]);

  const submit = async () => {
    if (!name.trim()) {
      toast("Connection name is required.", "error");
      return;
    }
    let connStr;
    if (advanced) {
      if (!rawConnStr.trim()) {
        toast("Connection string is required.", "error");
        return;
      }
      connStr = rawConnStr.trim();
    } else {
      if (!host.trim() || !database.trim() || !username.trim() || !password) {
        toast("Host, database, username, and password are all required.", "error");
        return;
      }
      connStr = buildConnStr(dbType, { host, port, database, username, password });
    }
    setSaving(true);
    try {
      await ConnectionsAPI.addSql({ name: name.trim(), connection_string: connStr, db_type: dbType });
      toast(`Saved SQL connection "${name.trim()}"`, "success");
      setName(""); setHost(""); setDatabase(""); setUsername(""); setPassword(""); setRawConnStr("");
      load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  const test = async (id) => {
    setTestingId(id);
    try {
      const r = await ConnectionsAPI.testSql(id);
      toast(r.ok ? "Connected successfully." : `Connection failed: ${r.error}`, r.ok ? "success" : "error");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setTestingId(null);
    }
  };

  const del = async (id) => {
    if (!window.confirm("Delete this SQL connection?")) return;
    setDeletingId(id);
    try {
      await ConnectionsAPI.deleteSql(id);
      load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <Card className="mb-md">
        <div className="section-label mb-sm">Add a SQL database</div>
        <div className="flex-col gap-sm">
          <Field label="Connection name">
            <Input placeholder="e.g. Marketing Analytics DB" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Database type">
            <Select value={dbType} onChange={(e) => onDbTypeChange(e.target.value)}>
              <option value="mssql">SQL Server (mssql)</option>
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
              <option value="other">Other</option>
            </Select>
          </Field>

          {!advanced && dbType !== "other" ? (
            <>
              <div className="grid grid-2">
                <Field label="Host / server">
                  <Input placeholder="e.g. myserver.database.windows.net" value={host} onChange={(e) => setHost(e.target.value)} />
                </Field>
                <Field label="Port">
                  <Input placeholder={DB_DEFAULTS[dbType]?.port} value={port} onChange={(e) => setPort(e.target.value)} />
                </Field>
              </div>
              <Field label="Database name">
                <Input placeholder="e.g. Sales_1" value={database} onChange={(e) => setDatabase(e.target.value)} />
              </Field>
              <div className="grid grid-2">
                <Field label="Username">
                  <Input placeholder="e.g. app_user" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
                </Field>
                <Field label="Password">
                  <div className="row gap-sm">
                    <Input type={showPassword ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} style={{ flex: 1 }} autoComplete="new-password" />
                    <Button size="sm" variant="ghost" onClick={() => setShowPassword((s) => !s)}>{showPassword ? "🙈" : "👁"}</Button>
                  </div>
                </Field>
              </div>
              <button className="link-btn" onClick={() => setAdvanced(true)}>Paste a raw connection string instead →</button>
            </>
          ) : (
            <>
              <Field label="Connection string">
                <TextArea
                  rows={3}
                  placeholder={"mssql+pyodbc://user:password@myserver.database.windows.net/mydb?driver=ODBC+Driver+17+for+SQL+Server\n\nor the semicolon form: Server=...;Database=...;UID=...;PWD=..."}
                  value={rawConnStr}
                  onChange={(e) => setRawConnStr(e.target.value)}
                />
              </Field>
              {dbType !== "other" && (
                <button className="link-btn" onClick={() => setAdvanced(false)}>← Use separate host/username/password fields instead</button>
              )}
            </>
          )}

          <div className="text-mid" style={{ fontSize: 12 }}>🔒 Your password is combined into a connection string and stored encrypted at rest using the same master key as your source credentials (see Settings). It is never shown again after saving.</div>
          <div>
            <Button variant="primary" loading={saving} onClick={submit}>➕ Add connection</Button>
          </div>
        </div>
      </Card>

      <div className="section-label mb-sm">Your SQL databases</div>
      {!profiles ? <Empty icon="⏳" title="Loading…" /> : profiles.length === 0 ? (
        <Empty icon="🗄" title="No SQL databases added yet" hint="The app will fall back to the SQL_CONNECTION_STRING environment variable." />
      ) : (
        <div className="flex-col gap-sm">
          {profiles.map((p) => (
            <Card key={p.id}>
              <div className="row-between">
                <div>
                  <b>{p.name}</b> <Badge status="accent">{p.db_type}</Badge>
                  <div className="mono text-mid mt-sm" style={{ fontSize: 12 }}>{p.masked || "(unreadable — check SCHED_MASTER_KEY)"}</div>
                </div>
                <div className="row gap-sm">
                  <Button size="sm" loading={testingId === p.id} onClick={() => test(p.id)}>🧪 Test</Button>
                  <Button size="sm" variant="danger" loading={deletingId === p.id} onClick={() => del(p.id)}>🗑 Delete</Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
      <style>{`.section-label{font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-mid)}
        .flex-col{display:flex;flex-direction:column}
        .link-btn{background:none;border:none;color:var(--accent-hi);font-size:12.5px;cursor:pointer;padding:2px 0;text-align:left;text-decoration:underline;width:fit-content}`}</style>
    </>
  );
}

function VectorProfiles() {
  const [profiles, setProfiles] = useState(null);
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [collection, setCollection] = useState("file_ingestion");
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const toast = useToast();

  const load = useCallback(() => {
    ConnectionsAPI.listVector().then((r) => setProfiles(r.profiles || [])).catch((e) => toast(e.message, "error"));
  }, [toast]);
  useEffect(() => { load(); }, [load]);

  const submit = async () => {
    if (!name.trim() || !path.trim()) {
      toast("Name and folder path are both required.", "error");
      return;
    }
    setSaving(true);
    try {
      await ConnectionsAPI.addVector({ name: name.trim(), db_path: path.trim(), collection_name: collection.trim() || "file_ingestion" });
      toast(`Saved vector store "${name.trim()}"`, "success");
      setName(""); setPath(""); setCollection("file_ingestion");
      load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setSaving(false);
    }
  };

  const test = async (id) => {
    setTestingId(id);
    try {
      const r = await ConnectionsAPI.testVector(id);
      toast(r.ok ? `Reachable. Existing tables: ${(r.existing_tables || []).join(", ") || "(none yet)"}` : `Could not open path: ${r.error}`, r.ok ? "success" : "error");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setTestingId(null);
    }
  };

  const del = async (id) => {
    if (!window.confirm("Delete this vector store connection?")) return;
    setDeletingId(id);
    try {
      await ConnectionsAPI.deleteVector(id);
      load();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <>
      <Card className="mb-md">
        <div className="section-label mb-sm">Add a vector store location</div>
        <div className="text-mid mb-sm" style={{ fontSize: 12.5 }}>Point LanceDB at any folder you have write access to — a local path, a mounted network drive, etc. Each store keeps its own collection name.</div>
        <div className="flex-col gap-sm">
          <Field label="Store name">
            <Input placeholder="e.g. Marketing Embeddings" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Folder path">
            <Input placeholder={"/data/vector_stores/marketing  or  D:\\vector_stores\\marketing"} value={path} onChange={(e) => setPath(e.target.value)} />
          </Field>
          <Field label="Collection name">
            <Input value={collection} onChange={(e) => setCollection(e.target.value)} />
          </Field>
          <div>
            <Button variant="primary" loading={saving} onClick={submit}>➕ Add vector store</Button>
          </div>
        </div>
      </Card>

      <div className="section-label mb-sm">Your vector stores</div>
      {!profiles ? <Empty icon="⏳" title="Loading…" /> : profiles.length === 0 ? (
        <Empty icon="🧠" title="No vector stores added yet" hint="The app will fall back to VECTOR_DB_PATH (default ./lance_db)." />
      ) : (
        <div className="flex-col gap-sm">
          {profiles.map((p) => (
            <Card key={p.id}>
              <div className="row-between">
                <div>
                  <b>{p.name}</b> <span className="text-mid" style={{ fontSize: 12 }}>collection: <span className="mono">{p.collection_name}</span></span>
                  <div className="mono text-mid mt-sm" style={{ fontSize: 12 }}>{p.db_path}</div>
                </div>
                <div className="row gap-sm">
                  <Button size="sm" loading={testingId === p.id} onClick={() => test(p.id)}>🧪 Test</Button>
                  <Button size="sm" variant="danger" loading={deletingId === p.id} onClick={() => del(p.id)}>🗑 Delete</Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
      <style>{`.section-label{font-weight:700;font-size:13px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-mid)}
        .flex-col{display:flex;flex-direction:column}`}</style>
    </>
  );
}
