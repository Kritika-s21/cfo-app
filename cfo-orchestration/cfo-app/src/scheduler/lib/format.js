export function freqLabel(s) {
  const f = s.frequency;
  switch (f) {
    case "minutely": return `Every ${s.minutely_interval ?? 5}m`;
    case "hourly": return `Hourly :${String(s.hourly_minute ?? 0).padStart(2, "0")}`;
    case "daily": return `Daily ${String(s.daily_hour ?? 9).padStart(2, "0")}:${String(s.daily_minute ?? 0).padStart(2, "0")}`;
    case "weekly": return `Weekly ${s.weekly_day_name ?? "Monday"}`;
    case "monthly": return `Day ${s.monthly_dom ?? 1} monthly`;
    case "cron": return s.cron_expression || "cron";
    default: return f || "—";
  }
}

export function fmtDateTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function bytesToKB(n) {
  return (n / 1024).toFixed(1);
}

export function uid(prefix = "id") {
  return `${prefix}-${Math.random().toString(36).slice(2, 9)}`;
}
