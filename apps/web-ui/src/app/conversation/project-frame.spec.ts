import { computed, signal } from '@angular/core';
import { render, screen } from '@testing-library/angular';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import { NowService } from '../core/now.service';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { FakeDeskBridge, provideGlobal } from '../testing/fake-bridge';
import { ProjectFrame } from './project-frame';

afterEach(() => {
  vi.restoreAllMocks();
});

const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Onboarding revamp', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, desk_reasoning_effort: null, thread_reasoning_effort: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, automation_run_id: null, automation_step_id: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: null,
  threads: [],
  approvals: [],
  last_seq: 2,
});

describe('ProjectFrame', () => {
  it('shows no diagram, and logs why, when the geometry throws', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    // The time the geometry is computed for: it throws once `broken`, inside the frame's geometry.
    const tick = signal(Date.now());
    let broken = false;
    const now = computed(() => {
      const t = tick();
      if (broken) throw new Error('geometry boom');
      return t;
    });
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': () => {
        bridge.emit('desk:event', ev(1, 'project.created', { name: 'Onboarding revamp', goal: 'g', instructions: '' }));
        bridge.emit('desk:event', ev(2, 'message.user', { text: 'Relaunch onboarding next month' }, { agent: 'd' }));
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    const view = await render(`<div deskProjectFrame projectId="p" mode="desk"><div></div></div>`, {
      imports: [ProjectFrame],
      providers: [
        ...bridge.providers,
        provideGlobal({ ...initialGlobalState(), connection: { status: 'live' } }),
        { provide: SESSION_RELEASE_DELAY, useValue: 0 },
        { provide: NowService, useValue: { now } },
      ],
    });
    await vi.waitFor(() => expect(document.querySelector('section[deskLineDiagram]')).not.toBeNull());
    expect(logged).not.toHaveBeenCalled();

    broken = true;
    tick.set(tick() + 15_000);
    await view.fixture.whenStable();
    expect(document.querySelector('section[deskLineDiagram]')).toBeNull();
    expect(logged).toHaveBeenCalledWith('Desk line diagram error', expect.objectContaining({ message: 'geometry boom' }));
    // The frame and its body stay; only the diagram goes.
    expect(document.querySelector('.project-frame > .project-frame-body')).not.toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
