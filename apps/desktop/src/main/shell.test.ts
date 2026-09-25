import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { AttentionItem, ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../shared/state';
import { attentionRoute, notificationFor } from './notify';
import { AppSettingsStore } from './settings';
import { trayIconBitmap } from './trayIcon';
import { trayModel } from './trayModel';

const item = (kind: AttentionItem['kind'], id: string, title = `${kind} title`): AttentionItem => ({
  id,
  kind,
  project_id: 'p',
  project_name: 'Onboarding',
  agent_id: null,
  title,
  detail: '',
  created_at: '2026-09-24T10:00:00.000Z',
  ref: {},
});

describe('app settings', () => {
  it('defaults, persists and survives a corrupt file', () => {
    const dir = mkdtempSync(join(tmpdir(), 'desk-settings-'));
    try {
      const file = join(dir, 'settings.json');
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: true, appearance: 'system' });
      new AppSettingsStore(file).update({ notifications: false });
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: false, appearance: 'system' });
      expect(new AppSettingsStore(file).update({ appearance: 'dark' })).toEqual({ notifications: false, appearance: 'dark' });
      expect(new AppSettingsStore(file).update({})).toEqual({ notifications: false, appearance: 'dark' });
      writeFileSync(file, '{"notifications":false}');
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: false, appearance: 'system' });
      writeFileSync(file, '{nope');
      expect(new AppSettingsStore(file).get()).toEqual({ notifications: true, appearance: 'system' });
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe('notifications', () => {
  it('words each kind and routes to the attention item', () => {
    expect(notificationFor(item('approval', 'approval:a1', 'Signup checklist wants to run bash'))).toEqual({ title: 'Onboarding: approval needed', body: 'Signup checklist wants to run bash' });
    expect(notificationFor(item('question', 'q')).title).toBe('Onboarding: Desk has a question');
    expect(notificationFor(item('failed', 'f')).title).toBe('Onboarding: a thread failed');
    expect(notificationFor(item('paused', 'paused:9', 'Agents in Onboarding are paused: too many automatic wakes this hour'))).toEqual({
      title: 'Onboarding: agents paused',
      body: 'Agents in Onboarding are paused: too many automatic wakes this hour',
    });
    expect(notificationFor(item('question', 'q', 'x'.repeat(500))).body.length).toBeLessThanOrEqual(200);
    expect(attentionRoute(item('approval', 'approval:a/1'))).toBe('#/attention?item=approval%3Aa%2F1');
  });
});

describe('tray model', () => {
  it('shows the count, the first items, what is running, and the daemon state', () => {
    const running: ProjectSummary = {
      project: { id: 'p', name: 'Onboarding', goal: '', updated_at: '' },
      desk_status: 'idle',
      threads: [
        { id: 't1', title: 'A', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '', updated_at: '' },
        { id: 't2', title: 'B', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: [], review_round: 0, created_at: '', updated_at: '' },
      ],
      latest_report: null,
      plan_progress: { done: 0, total: 0 },
      attention_count: 0,
    };
    const items = Array.from({ length: 7 }, (_, i) => item(i === 0 ? 'approval' : 'needs_you', `i${i}`));
    const m = trayModel({ ...initialGlobalState(), connection: { status: 'live' }, attention: items, overview: [running], system: { proxy: 'up', notices: [], lastSeq: 0 } });
    expect(m.title).toBe('7');
    expect(m.tooltip).toBe('Desk: 7 need you');
    const labels = m.items.map((i) => ('separator' in i ? '—' : i.label));
    expect(labels[0]).toBe('7 need you');
    expect(labels[1]).toMatch(/^APR/);
    expect(labels).toContain('and 2 more…');
    expect(labels).toContain('2 threads running');
    expect(labels).toContain('deskd running · proxy up');
    expect(labels.slice(-2)).toEqual(['Open Desk', 'Quit Desk']);
    const offline = trayModel({ ...initialGlobalState(), connection: { status: 'offline' } });
    expect([offline.title, offline.tooltip]).toEqual(['', 'Desk: deskd is not running']);
    expect(offline.items.map((i) => ('separator' in i ? '—' : i.label))).toContain('deskd is not running');
  });
});

describe('tray icon', () => {
  it('draws an antialiased ring with a centre dot in template colours', () => {
    const size = 32;
    const px = trayIconBitmap(size);
    expect(px.length).toBe(size * size * 4);
    const alpha = (x: number, y: number) => px[(y * size + x) * 4 + 3]!;
    expect(alpha(16, 16)).toBe(255);
    expect(alpha(0, 0)).toBe(0);
    expect(alpha(16, 16 - Math.round((5.5 * size) / 14))).toBeGreaterThan(128);
    expect(px[(16 * size + 16) * 4]).toBe(0);
  });
});
