// Computes a "next run" timestamp to display for a running scheduler.
//
// For minutely/hourly-by-minute schedules we anchor off the moment the
// scheduler was actually started (`run_started_at`), not off "now" rounded
// to the nearest interval — so "started a 5-min scheduler just now" shows
// the scan happening 5 minutes from *that* moment, matching what the
// backend's APScheduler IntervalTrigger actually does.
//
// For calendar-style schedules (daily/weekly/monthly) there's no "since
// start" anchor in Streamlit either — those are wall-clock triggers — so we
// compute the next matching wall-clock time from now, same as
// NewScheduler.jsx's preview.

const WEEKDAYS_MON0 = [0, 1, 2, 3, 4, 5, 6]; // Monday=0 convention used elsewhere in this app

export function computeNextRun(sched) {
  if (!sched || sched.status !== "running") return null;
  const now = new Date();
  const freq = sched.frequency;

  if (freq === "minutely") {
    const intervalMs = Math.max(1, sched.minutely_interval || 5) * 60000;
    const anchor = new Date(sched.run_started_at || sched.created_at || now);
    let next = new Date(anchor.getTime() + intervalMs);
    while (next <= now) next = new Date(next.getTime() + intervalMs);
    return next;
  }

  if (freq === "hourly") {
    const anchor = new Date(sched.run_started_at || now);
    const next = new Date(anchor);
    next.setMinutes(sched.hourly_minute || 0, 0, 0);
    while (next <= now) next.setHours(next.getHours() + 1);
    return next;
  }

  if (freq === "daily") {
    const next = new Date(now);
    next.setHours(sched.daily_hour ?? 9, sched.daily_minute ?? 0, 0, 0);
    if (next <= now) next.setDate(next.getDate() + 1);
    return next;
  }

  if (freq === "weekly") {
    const next = new Date(now);
    const target = sched.weekly_weekday ?? 0;
    next.setHours(sched.weekly_hour ?? 9, sched.weekly_minute ?? 0, 0, 0);
    let diff = (target - ((next.getDay() + 6) % 7) + 7) % 7;
    if (diff === 0 && next <= now) diff = 7;
    next.setDate(next.getDate() + diff);
    return next;
  }

  if (freq === "monthly") {
    const next = new Date(now);
    next.setDate(sched.monthly_dom || 1);
    next.setHours(sched.monthly_hour ?? 9, sched.monthly_minute ?? 0, 0, 0);
    if (next <= now) next.setMonth(next.getMonth() + 1);
    return next;
  }

  // cron / unknown: no client-side preview available
  return null;
}

export function fmtNextRun(sched) {
  const next = computeNextRun(sched);
  if (!next) return null;
  return next.toLocaleString(undefined, {
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
