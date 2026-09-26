const pad = (n: number) => String(n).padStart(2, '0');

/** Local wall-clock time, e.g. "10:42". */
export function clock(ts: string | number | Date): string {
  const d = new Date(ts);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** A short duration: "42s", "14m", "1h 5m", "3d". */
export function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

/** Time since `ts`: "now" under a minute, else a short duration. */
/** "just now", or "5m ago" style text for past moments. */
export const since = (ts: string, now: number): string => {
  const a = ago(ts, now);
  return a === 'now' ? 'just now' : `${a} ago`;
};

export function ago(ts: string, now: number): string {
  const ms = now - Date.parse(ts);
  return ms < 60_000 ? 'now' : duration(ms);
}

export function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

/** A token count in at most ~3 significant digits: 236, 7.7k, 402M, 3.2B (the thread's Usage tab, System's totals). */
export function tokens(n: number): string {
  if (n < 1000) return String(n);
  const units: Array<[number, string]> = [
    [1e3, 'k'],
    [1e6, 'M'],
    [1e9, 'B'],
  ];
  for (const [i, [size, unit]] of units.entries()) {
    const v = n / size;
    const text = v < 9.95 ? v.toFixed(1) : String(Math.round(v));
    // 999_700 rounds to "1000k"; the next unit says it better.
    if (Number(text) < 1000 || i === units.length - 1) return `${text}${unit}`;
  }
  return String(n);
}
