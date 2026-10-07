/**
 * useScheduler.js
 * ================
 * React hook that reads schedule status from the FastAPI backend
 * and exposes toggle / reload helpers.
 *
 * Usage inside App.jsx:
 *
 *   import { useScheduler } from "./hooks/useScheduler";
 *
 *   const { schedules, loading, toggle, reload } = useScheduler(token);
 */

import { useCallback, useEffect, useState } from "react";

const API_BASE = process.env.REACT_APP_API_URL || "http://localhost:8000";

export function useScheduler(token) {
  const [schedules, setSchedules] = useState([]);
  const [loading,   setLoading]   = useState(false);
  const [error,     setError]     = useState(null);

  // ── helpers ─────────────────────────────────────────────────────────────
  const headers = { Authorization: `Bearer ${token}` };

  const fetchSchedules = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const r    = await fetch(`${API_BASE}/api/schedules`, { headers });
      const data = await r.json();
      setSchedules(data.schedules || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  // ── auto-refresh every 15 seconds ───────────────────────────────────────
  useEffect(() => {
    fetchSchedules();
    const id = setInterval(fetchSchedules, 15_000);
    return () => clearInterval(id);
  }, [fetchSchedules]);

  // ── toggle a source on/off ───────────────────────────────────────────────
  const toggle = useCallback(async (scheduleId) => {
    const r    = await fetch(`${API_BASE}/api/schedules/${scheduleId}/toggle`, {
      method: "PUT", headers,
    });
    const data = await r.json();
    setSchedules(prev =>
      prev.map(s => s.id === scheduleId ? { ...s, on: data.on, running: data.on } : s)
    );
    return data;
  }, [token]);

  // ── hot-reload schedules.json ────────────────────────────────────────────
  const reload = useCallback(async () => {
    await fetch(`${API_BASE}/api/schedules/reload`, { method: "POST", headers });
    await fetchSchedules();
  }, [token, fetchSchedules]);

  return { schedules, loading, error, toggle, reload, refresh: fetchSchedules };
}
