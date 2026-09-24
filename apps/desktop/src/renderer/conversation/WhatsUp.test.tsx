// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ProjectState, ThreadView } from '@desk/client';
import { WhatsUp } from './WhatsUp';

afterEach(cleanup);

const now = Date.parse('2026-09-25T10:00:00Z');
const thread = (id: string, status: ThreadView['status'], archived_at: string | null = null) => ({ id, status, archived_at }) as ThreadView;
const project = (whatsUp: ProjectState['whatsUp']) =>
  ({ threads: [thread('a', 'running'), thread('b', 'done'), thread('c', 'done', '2026-09-24T10:00:00Z')], whatsUp }) as unknown as ProjectState;

describe('WhatsUp', () => {
  it("shows Desk's text in its voice, with how long ago Desk wrote it", () => {
    render(<WhatsUp project={project({ text: 'Renderer R3 is wiring live playback. **Next:** review it.', ts: '2026-09-25T09:56:00Z' })} now={now} />);
    const region = screen.getByRole('region', { name: "What's up" });
    expect(region.textContent).toContain("What's up · 4m ago");
    expect(region.querySelector('.md-voice')?.textContent).toBe('Renderer R3 is wiring live playback. Next: review it.');
    expect(region.querySelector('strong')?.textContent).toBe('Next:');
  });

  it('reads "just now" for a fresh one, and never renders HTML from the text', () => {
    render(<WhatsUp project={project({ text: 'Starting. <img src=x onerror="alert(1)">', ts: '2026-09-25T09:59:40Z' })} now={now} />);
    const region = screen.getByRole('region', { name: "What's up" });
    expect(region.textContent).toContain("What's up · just now");
    expect(region.querySelector('img')).toBeNull();
  });

  it('shows the live thread counts until Desk has written one', () => {
    render(<WhatsUp project={project(null)} now={now} />);
    expect(screen.getByRole('region', { name: "What's up" }).textContent).toBe("What's upDesk and 2 threads. 1 running, 0 waiting, 1 done.");
  });
});
