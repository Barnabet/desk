export type DiffLine = { kind: 'same' | 'add' | 'del'; text: string };

const MAX = 4000;

/** A line diff (longest common subsequence). Very long inputs fall back to "all removed, all added". */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  if (a.length * b.length > MAX * MAX / 4) return [...a.map((text) => ({ kind: 'del' as const, text })), ...b.map((text) => ({ kind: 'add' as const, text }))];
  const n = a.length;
  const m = b.length;
  const lcs: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i]! });
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) out.push({ kind: 'del', text: a[i++]! });
    else out.push({ kind: 'add', text: b[j++]! });
  }
  while (i < n) out.push({ kind: 'del', text: a[i++]! });
  while (j < m) out.push({ kind: 'add', text: b[j++]! });
  return out;
}

/** Keeps changed lines with `context` unchanged lines around them; gaps become a single `…` marker (null). */
export function withContext(lines: DiffLine[], context = 3): Array<DiffLine | null> {
  const keep = new Set<number>();
  lines.forEach((l, i) => {
    if (l.kind !== 'same') for (let k = i - context; k <= i + context; k++) keep.add(k);
  });
  const out: Array<DiffLine | null> = [];
  lines.forEach((l, i) => {
    if (keep.has(i)) out.push(l);
    else if (out.at(-1) !== null) out.push(null);
  });
  if (out[0] === null) out.shift();
  if (out.at(-1) === null) out.pop();
  return out;
}

export type FileChange = { path: string; change: 'added' | 'removed' | 'changed' | 'same'; before?: number; after?: number };

/** Compares two file lists by path and size. */
export function diffFiles(before: Array<{ path: string; size: number }>, after: Array<{ path: string; size: number }>): FileChange[] {
  const a = new Map(before.map((f) => [f.path, f.size]));
  const b = new Map(after.map((f) => [f.path, f.size]));
  const paths = [...new Set([...a.keys(), ...b.keys()])].sort();
  return paths.map((path) => {
    const x = a.get(path);
    const y = b.get(path);
    if (x === undefined) return { path, change: 'added', after: y! };
    if (y === undefined) return { path, change: 'removed', before: x };
    return { path, change: x === y ? 'same' : 'changed', before: x, after: y };
  });
}
