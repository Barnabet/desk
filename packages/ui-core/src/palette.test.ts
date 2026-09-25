import { describe, expect, it } from 'vitest';
import { rankPalette, score, type PaletteItem } from './palette';

const item = (group: PaletteItem['group'], title: string, detail?: string): PaletteItem => ({ id: `${group}:${title}`, group, title, route: '#/', ...(detail ? { detail } : {}) });

describe('palette ranking', () => {
  it('scores prefixes over word starts over substrings', () => {
    expect(score(item('Projects', 'Onboarding revamp'), 'onb')).toBe(3);
    expect(score(item('Projects', 'Onboarding revamp'), 'rev')).toBe(2);
    expect(score(item('Skills', 'email-sequence'), 'seq')).toBe(2);
    expect(score(item('Projects', 'Onboarding revamp'), 'vamp')).toBe(1);
    expect(score(item('Projects', 'Tax', 'file by April'), 'april')).toBe(1);
    expect(score(item('Projects', 'Tax'), 'zzz')).toBe(0);
  });

  it('orders by group then score, and shows only places and projects without a query', () => {
    const items = [item('Threads', 'Welcome emails'), item('Projects', 'Email launch'), item('Go to', 'Map'), item('Skills', 'email-sequence'), item('Memory', 'We email on Tuesdays')];
    expect(rankPalette(items, '').map((i) => i.title)).toEqual(['Map', 'Email launch']);
    expect(rankPalette(items, 'email').map((i) => i.title)).toEqual(['Email launch', 'Welcome emails', 'email-sequence', 'We email on Tuesdays']);
  });
});
