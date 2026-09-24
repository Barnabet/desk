import { describe, expect, it } from 'vitest';
import { ago, bytes, clock, duration, plural } from './format';

describe('format', () => {
  it('formats durations, ages, sizes and counts', () => {
    expect(duration(42_000)).toBe('42s');
    expect(duration(14 * 60_000)).toBe('14m');
    expect(duration(65 * 60_000)).toBe('1h 5m');
    expect(duration(120 * 60_000)).toBe('2h');
    expect(duration(3 * 86_400_000)).toBe('3d');
    const now = Date.parse('2026-09-24T11:27:00Z');
    expect(ago('2026-09-24T11:26:30Z', now)).toBe('now');
    expect(ago('2026-09-24T11:23:00Z', now)).toBe('4m');
    expect(bytes(812)).toBe('812 B');
    expect(bytes(1946)).toBe('1.9 KB');
    expect(bytes(3_355_443)).toBe('3.2 MB');
    expect(plural(1, 'thread')).toBe('1 thread');
    expect(plural(3, 'thread')).toBe('3 threads');
    expect(plural(2, 'reply', 'replies')).toBe('2 replies');
  });

  it('shows local wall-clock time', () => {
    const d = new Date(2026, 8, 24, 9, 5);
    expect(clock(d)).toBe('09:05');
    expect(clock(d.toISOString())).toBe('09:05');
  });
});
