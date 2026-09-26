import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { render } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState, type GlobalState } from '@desk/bff/contract';
import type { ProjectSummary } from '@desk/protocol';
import { GlobalStore } from '../core/global.store';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { injectSkills, scopeArg, whoLabel } from './data';

const summary = (id: string, name: string, threads: unknown[] = []) =>
  ({ project: { id, name, goal: '', updated_at: 't' }, desk_status: 'idle', threads, latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }) as unknown as ProjectSummary;
const state = (overview: ProjectSummary[]): GlobalState => ({ ...initialGlobalState(), connection: { status: 'live' }, overview });
const sk = (name: string, scope: 'global' | 'project', version = 1) => ({ name, scope, description: `${name} does things`, dir: `/s/${name}`, version });

/** A component that only holds injectSkills(), as SkillsScreen does. */
@Component({ selector: 'desk-skills-probe', template: '' })
class Probe {
  readonly skills = injectSkills();
}

async function setup(handlers: FakeHandlers, overview: ProjectSummary[]) {
  const bridge = new FakeDeskBridge(handlers);
  const view = await render(Probe, { providers: [...bridge.providers, provideGlobal(state(overview))] });
  const lists = () => bridge.calls.filter((c) => c.channel === 'skills.list').map((c) => c.input);
  return { bridge, lists, fixture: view.fixture, skills: view.fixture.componentInstance.skills };
}

/** Lets every pending bridge answer land (a macrotask runs after all queued microtasks). */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.useRealTimers();
});

describe('scopeArg and whoLabel', () => {
  it('names the project only for project skills', () => {
    expect(scopeArg({ scope: 'global', name: 'brand-voice' })).toEqual({});
    expect(scopeArg({ scope: 'project', projectId: 'p1', name: 'brand-voice' })).toEqual({ projectId: 'p1' });
  });

  it('says who made a version: you, a thread, Desk, a built-in skill, or the catalog with its commit', () => {
    const titles = new Map([['t1', 'Welcome emails']]);
    expect(whoLabel(null, titles)).toBe('Unknown');
    expect(whoLabel('user', titles)).toBe('You');
    expect(whoLabel('agent:t1', titles)).toBe('Welcome emails');
    expect(whoLabel('agent:d9', titles)).toBe('Desk');
    expect(whoLabel('builtin:word-documents', titles)).toBe('Built into Desk');
    expect(whoLabel(`catalog:word-documents@${'a'.repeat(40)}`, titles)).toBe('Catalog · aaaaaaa');
    expect(whoLabel('catalog:word-documents@builtin-0123456789ab', titles)).toBe('Catalog');
    expect(whoLabel('import', titles)).toBe('import');
  });
});

describe('injectSkills', () => {
  it('joins global and per-project lists with the threads using them', async () => {
    const t1 = { id: 't1', title: 'Welcome emails', status: 'running', skills: ['brand-voice', 'email-sequence'] };
    // The global list is held until the loading state is checked: render's whenStable could otherwise see it answered.
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { skills, lists } = await setup(
      {
        'skills.list': async ({ projectId }: { projectId?: string }) => {
          if (projectId === 'p1') return [sk('brand-voice', 'project', 3), sk('email-sequence', 'global')];
          await gate;
          return [sk('brand-voice', 'global'), sk('email-sequence', 'global')];
        },
      },
      [summary('p1', 'Onboarding', [t1])],
    );
    expect(skills.status()).toBe('loading');
    release();
    await vi.waitFor(() => expect(skills.status()).toBe('ready'));
    expect(lists()).toEqual([{}, { projectId: 'p1' }]);
    expect(skills.nodes().map((n) => [n.key, n.version, n.shadows, n.shadowedIn, n.usedBy.map((u) => u.threadId)])).toEqual([
      ['global:brand-voice', 1, false, ['p1'], []],
      ['global:email-sequence', 1, false, [], ['t1']],
      ['project:p1:brand-voice', 3, true, [], ['t1']],
    ]);
    expect(skills.nodes()[2]?.projectName).toBe('Onboarding');
  });

  it('counts a project it cannot list as empty, and lists again when the projects change', async () => {
    const { skills, lists } = await setup(
      {
        'skills.list': ({ projectId }: { projectId?: string }) => {
          if (projectId === 'p1') throw { code: 'not_found', message: 'No project p1', status: 404 };
          return projectId ? [sk('receipts', 'project')] : [];
        },
      },
      [summary('p1', 'Onboarding')],
    );
    await vi.waitFor(() => expect(skills.status()).toBe('ready'));
    expect(skills.nodes()).toEqual([]);
    TestBed.inject(GlobalStore).set(state([summary('p1', 'Onboarding'), summary('p2', 'Tax')]));
    await vi.waitFor(() => expect(skills.nodes().map((n) => n.key)).toEqual(['project:p2:receipts']));
    expect(lists()).toEqual([{}, { projectId: 'p1' }, {}, { projectId: 'p1' }, { projectId: 'p2' }]);
  });

  it('sends no list when an overview push leaves the projects as they were, and still follows what the threads use', async () => {
    const t1 = { id: 't1', title: 'Welcome emails', status: 'running', skills: ['brand-voice'] };
    const { skills, lists, fixture } = await setup({ 'skills.list': ({ projectId }: { projectId?: string }) => (projectId ? [] : [sk('brand-voice', 'global'), sk('email-sequence', 'global')]) }, [
      summary('p1', 'Onboarding', [t1]),
    ]);
    await vi.waitFor(() => expect(skills.status()).toBe('ready'));
    const used = () => skills.nodes().map((n) => [n.key, n.usedBy.map((u) => u.threadId)]);
    expect(used()).toEqual([
      ['global:brand-voice', ['t1']],
      ['global:email-sequence', []],
    ]);
    expect(lists()).toEqual([{}, { projectId: 'p1' }]);
    TestBed.inject(GlobalStore).set(state([summary('p1', 'Onboarding', [{ ...t1, skills: ['email-sequence'] }])]));
    await fixture.whenStable();
    await settle();
    expect(used()).toEqual([
      ['global:brand-voice', []],
      ['global:email-sequence', ['t1']],
    ]);
    expect(lists()).toEqual([{}, { projectId: 'p1' }]);
  });

  it('keeps the newest lists when an older refresh answers last', async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    let calls = 0;
    const { skills, lists } = await setup(
      {
        'skills.list': async () => {
          if (++calls > 1) return [sk('new-skill', 'global')];
          await gate;
          return [sk('old-skill', 'global')];
        },
      },
      [],
    );
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => expect(skills.nodes().map((n) => n.key)).toEqual(['global:new-skill']));
    release();
    await gate;
    await settle();
    expect(lists()).toEqual([{}, {}]);
    expect(skills.nodes().map((n) => n.key)).toEqual(['global:new-skill']);
  });

  it('lists nothing more once its component is gone: no focus listener, no 30 s timer', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const { skills, lists, fixture } = await setup({ 'skills.list': () => [sk('brand-voice', 'global')] }, []);
    await vi.waitFor(() => expect(skills.status()).toBe('ready'));
    vi.advanceTimersByTime(30_000);
    await vi.waitFor(() => expect(lists()).toHaveLength(2));
    fixture.destroy();
    window.dispatchEvent(new Event('focus'));
    vi.advanceTimersByTime(60_000);
    await settle();
    expect(lists()).toHaveLength(2);
  });

  it('lists again when the window gets focus, and keeps the last lists when that fails', async () => {
    let fail = false;
    const { skills, lists } = await setup(
      {
        'skills.list': () => {
          if (fail) throw { code: 'internal', message: 'deskd is not answering' };
          return [sk('brand-voice', 'global')];
        },
      },
      [],
    );
    await vi.waitFor(() => expect(skills.status()).toBe('ready'));
    fail = true;
    window.dispatchEvent(new Event('focus'));
    await vi.waitFor(() => expect(skills.error()).toBe('deskd is not answering'));
    expect(lists()).toEqual([{}, {}]);
    expect(skills.status()).toBe('ready');
    expect(skills.nodes().map((n) => n.key)).toEqual(['global:brand-voice']);
  });

  it('is an error until a global list arrives', async () => {
    const { skills } = await setup({ 'skills.list': () => Promise.reject({ code: 'internal', message: 'deskd is not answering' }) }, []);
    await vi.waitFor(() => expect(skills.status()).toBe('error'));
    expect(skills.error()).toBe('deskd is not answering');
    expect(skills.nodes()).toEqual([]);
  });
});
