// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { TitleBar } from './TitleBar';

afterEach(cleanup);
beforeEach(() => installBridge());

const item = (id: string): AttentionItem => ({ id, kind: 'approval', project_id: 'p', project_name: 'P', agent_id: null, title: 't', detail: '', created_at: '', ref: {} });

describe('TitleBar', () => {
  it('shows places, the daemon state and the attention pill', () => {
    globalStore.set({
      ...initialGlobalState(),
      connection: { status: 'live' },
      system: { proxy: 'up', notices: [], lastSeq: 0 },
      attention: [item('a'), item('b'), item('c')],
      overview: [{ project: { id: 'p1', name: 'Onboarding revamp', goal: '', updated_at: '' }, desk_status: 'idle', threads: [], latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }],
    });
    render(<TitleBar route={{ name: 'project', id: 'p1', tab: 'conversation' }} />);
    const nav = screen.getByRole('navigation', { name: 'Places' });
    expect(nav.textContent).toContain('Map');
    expect(nav.textContent).toContain('Onboarding revamp');
    expect(screen.getByRole('link', { name: 'Onboarding revamp' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByText('deskd · proxy up')).toBeTruthy();
    expect(screen.getByRole('link', { name: '3 need you' }).getAttribute('href')).toBe('#/attention');
  });

  it('says when nothing needs you and when deskd is down', () => {
    globalStore.set({ ...initialGlobalState(), connection: { status: 'offline' } });
    render(<TitleBar route={{ name: 'map' }} />);
    expect(screen.getByText('deskd not running')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'All clear' })).toBeTruthy();
  });
});
