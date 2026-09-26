import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AttentionItem, StoredEvent } from '@desk/protocol';
import { clock } from '@desk/ui-core';
import { SESSION_RELEASE_DELAY } from '../core/session.service';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { describeArgs, Inspector } from './inspector';

const NOW = Date.now();
const minutesAgo = (m: number) => new Date(NOW - m * 60_000).toISOString();

const overview = () =>
  ({
    project: { id: 'p', name: 'Tax 2026', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
    desk: null,
    sources: [],
    plan: null,
    threads: [],
    approvals: [],
    last_seq: 0,
  }) as unknown as ProjectOverview;

const events: StoredEvent[] = [
  ev(1, 'agent.created', { role: 'thread', model: 'm', title: 'Signup checklist', brief: 'b', workspace_path: '/data/workspaces/signup-checklist', parent_id: 'd', git: { source_id: 's', branch: 'desk/signup-checklist', base: 'main', common_dir: '/r/.git' } }, { agent: 't' }),
  ev(2, 'assistant.message', { run_id: 'r', content: 'I will install bun, then run the tests.', tool_calls: [] }, { agent: 't' }),
  ev(3, 'approval.requested', { approval_id: 'a1', run_id: 'r', tool_call_id: 'c', tool: 'bash', arguments: '{"command":"curl -fsSL https://bun.sh/install | bash"}', reason: 'Policy rule {"tool":"bash"} → ask', delegate_to_desk: false }, { agent: 't' }),
];

const approval: AttentionItem = { id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Tax 2026', agent_id: 't', title: 'Signup checklist wants to run bash', detail: 'Policy rule {"tool":"bash"} → ask', created_at: minutesAgo(4), ref: { approval_id: 'a1', thread_id: 't' } };
const question: AttentionItem = { id: 'question:7', kind: 'question', project_id: 'p', project_name: 'Tax 2026', agent_id: 'd', title: 'Data source or teammate first?', detail: '', created_at: minutesAgo(4), ref: { event_id: 7, options: ['Data source', 'Teammate'] } };
const handoff: AttentionItem = { id: 'report:9:0', kind: 'needs_you', project_id: 'p', project_name: 'Tax 2026', agent_id: 'd', title: 'Upload the 1099', detail: 'Research is in', created_at: minutesAgo(60), ref: { event_id: 9 } };
const stalled: AttentionItem = { id: 'stalled:t:12', kind: 'stalled', project_id: 'p', project_name: 'Tax 2026', agent_id: 't', title: 'Signup checklist has stalled', detail: '**Waiting** on the API key', created_at: minutesAgo(30), ref: { thread_id: 't', event_id: 12 } };
const failed: AttentionItem = { id: 'failed:t', kind: 'failed', project_id: 'p', project_name: 'Tax 2026', agent_id: 't', title: 'Signup checklist failed', detail: 'The model endpoint refused the key', created_at: minutesAgo(10), ref: { thread_id: 't' } };
const paused: AttentionItem = {
  id: 'paused:12',
  kind: 'paused',
  project_id: 'p',
  project_name: 'Tax 2026',
  agent_id: null,
  title: 'Agents in Tax 2026 are paused: too many automatic wakes this hour',
  detail: 'Their messages are kept. Resume, or write to any agent.',
  created_at: minutesAgo(4),
  ref: { event_id: 12 },
};

/** A bridge whose project p loads the overview and backfills `list` on watch. */
function watching(list: StoredEvent[] = events): FakeDeskBridge {
  const bridge: FakeDeskBridge = new FakeDeskBridge({
    'projects.get': () => overview(),
    'broker.watch': () => {
      for (const e of list) bridge.emit('desk:event', e);
      return { ok: true };
    },
    'broker.unwatch': () => ({ ok: true }),
  });
  return bridge;
}

const TEMPLATE = `<article deskInspector [item]="item" [index]="index" [total]="total" [now]="now" [note]="note" [busy]="busy" [answered]="answered" (noteChange)="noteChange($event)" (resolve)="resolve($event)" (answer)="answer($event)" (dismiss)="dismiss()" (open)="open()"></article>`;

type Shown = { item: AttentionItem; note?: string; busy?: string | null; answered?: boolean };

/** Renders the Inspector as item 1 of 3 at NOW; `show` re-renders it with other inputs, keeping the output spies. */
async function inspect(first: Shown, bridge: FakeDeskBridge = watching()) {
  const outputs = { noteChange: vi.fn(), resolve: vi.fn(), answer: vi.fn(), dismiss: vi.fn(), open: vi.fn() };
  const props = (s: Shown) => ({ item: s.item, index: 0, total: 3, now: NOW, note: s.note ?? '', busy: s.busy ?? null, answered: s.answered ?? false, ...outputs });
  const view = await render(TEMPLATE, {
    imports: [Inspector],
    componentProperties: props(first),
    providers: [...bridge.providers, { provide: SESSION_RELEASE_DELAY, useValue: 0 }],
  });
  return { ...outputs, show: (s: Shown) => view.rerender({ componentProperties: props(s) }) };
}

/** The value cell of the fact labelled `label`. */
function fact(insp: HTMLElement, label: string): HTMLElement {
  const row = [...insp.querySelectorAll<HTMLElement>('.fact')].find((f) => f.querySelector('.fact-label')?.textContent === label);
  if (!row) throw new Error(`No fact labelled ${label}`);
  return row.querySelector<HTMLElement>('.fact-value')!;
}

describe('describeArgs', () => {
  it('reads shell and skill runs as commands, and anything else as pretty JSON', () => {
    expect(describeArgs('bash', '{"command":"ls -la"}')).toEqual({ command: 'ls -la', pretty: '{\n  "command": "ls -la"\n}' });
    expect(describeArgs('bash_background', '{"command":"npm run dev"}').command).toBe('npm run dev');
    expect(describeArgs('skill_run', '{"skill":"pdf-toolkit","script":"pdf_info.py","args":["a.pdf","--json"]}').command).toBe('pdf-toolkit pdf_info.py a.pdf --json');
    expect(describeArgs('web_fetch', '{"url":"https://example.com"}')).toEqual({ command: null, pretty: '{\n  "url": "https://example.com"\n}' });
    expect(describeArgs('bash', 'not json')).toEqual({ command: null, pretty: 'not json' });
  });
});

describe('Inspector', () => {
  it('shows an approval in full: the command, the facts, why it asks and what the thread said', async () => {
    await inspect({ item: approval });
    const insp = screen.getByRole('article', { name: 'Selected: clearance request' });
    expect(insp.className).toBe('card inspector');
    expect(insp.querySelector('.strip-code-badge')!.className).toBe('strip-code-badge code-approval');
    expect(insp.querySelector('.strip-code-badge')!.textContent).toBe('APR');
    expect(insp.querySelector('.eyebrow')!.textContent).toBe('Clearance request');
    expect(insp.textContent).toContain(`· Tax 2026 · paused since ${clock(approval.created_at)}`);
    expect(insp.textContent).toContain('1 of 3');
    expect(insp.querySelector('.inspector-title')!.textContent).toBe('Signup checklist wants to run bash');
    expect((await within(insp).findByLabelText('Command')).textContent).toBe('$ curl -fsSL https://bun.sh/install | bash');
    expect(fact(insp, 'TOOL').textContent).toBe('bash');
    expect(fact(insp, 'TOOL').className).toBe('mono fact-value');
    expect(within(fact(insp, 'THREAD')).getByRole('link', { name: 'Signup checklist' }).getAttribute('href')).toBe('#/p/p/threads/t');
    expect(fact(insp, 'BRANCH').textContent).toBe('desk/signup-checklist');
    expect(within(fact(insp, 'PROJECT')).getByRole('link', { name: 'Tax 2026' }).getAttribute('href')).toBe('#/p/p/conversation');
    expect(fact(insp, 'WORKDIR').textContent).toBe('workspaces/signup-checklist');
    expect(fact(insp, 'WORKDIR').querySelector('span')!.getAttribute('title')).toBe('/data/workspaces/signup-checklist');
    expect(fact(insp, 'REQUESTED').textContent).toBe(clock(approval.created_at));
    expect(insp.textContent).toContain('Your policy asks before every bash call.');
    expect(insp.querySelector('.rule-chip')!.textContent).toBe('bash → ask');
    expect(await within(insp).findByText('I will install bun, then run the tests.')).toBeTruthy();
    expect(within(insp).getByRole('link', { name: 'Edit policy rules' }).getAttribute('href')).toBe('#/p/p/settings');
    expect(insp.textContent).toContain("The thread resumes as soon as you decide. If you deny, it's told why and tries another way.");
  });

  it('toggles the raw arguments, and sends the note and each decision up', async () => {
    const { noteChange, resolve } = await inspect({ item: approval });
    const insp = screen.getByRole('article', { name: 'Selected: clearance request' });
    await within(insp).findByLabelText('Command');
    fireEvent.click(within(insp).getByRole('button', { name: 'Show raw arguments' }));
    expect(within(insp).queryByLabelText('Command')).toBeNull();
    expect(insp.querySelector('.codeblock-lang')!.textContent).toBe('raw arguments');
    expect(insp.querySelector('.codeblock pre code')!.textContent).toBe('{"command":"curl -fsSL https://bun.sh/install | bash"}');
    fireEvent.click(within(insp).getByRole('button', { name: 'Show readable' }));
    expect(within(insp).getByLabelText('Command').textContent).toBe('$ curl -fsSL https://bun.sh/install | bash');
    fireEvent.input(within(insp).getByLabelText('Note to the thread (optional)'), { target: { value: 'Use npm test' } });
    expect(noteChange).toHaveBeenCalledWith('Use npm test');
    fireEvent.click(within(insp).getByRole('button', { name: /^Approve once/ }));
    fireEvent.click(within(insp).getByRole('button', { name: /^Deny/ }));
    expect(resolve.mock.calls).toEqual([['approved'], ['denied']]);
  });

  it('holds both decisions while one is on its way', async () => {
    await inspect({ item: approval, busy: 'approved', note: 'Use npm test' });
    const approve = screen.getByRole('button', { name: /^Approve once/ }) as HTMLButtonElement;
    const deny = screen.getByRole('button', { name: /^Deny/ }) as HTMLButtonElement;
    expect(approve.getAttribute('aria-busy')).toBe('true');
    expect(approve.disabled).toBe(true);
    expect(deny.disabled).toBe(true);
    expect(deny.getAttribute('aria-busy')).toBeNull();
    expect((screen.getByLabelText('Note to the thread (optional)') as HTMLInputElement).value).toBe('Use npm test');
  });

  it('says the request is loading while its project loads', async () => {
    await inspect({ item: approval }, new FakeDeskBridge({ 'projects.get': () => new Promise(() => {}) }));
    const insp = screen.getByRole('article', { name: 'Selected: clearance request' });
    expect(within(insp).getByText('Loading the request…')).toBeTruthy();
    expect(within(insp).queryByLabelText('Command')).toBeNull();
    expect(fact(insp, 'TOOL').textContent).toBe('—');
    expect(within(fact(insp, 'THREAD')).getByRole('link', { name: 'Thread' }).getAttribute('href')).toBe('#/p/p/threads/t');
    expect(fact(insp, 'BRANCH').textContent).toBe('scratch workspace');
    expect(fact(insp, 'WORKDIR').textContent).toBe('—');
    expect(within(insp).getByText('Nothing yet.')).toBeTruthy();
    // Until the approval loads, the item's own detail explains it.
    expect(insp.querySelector('.rule-chip')!.textContent).toBe('bash → ask');
  });

  it('names Desk when Desk asked, and says when the request details are gone', async () => {
    const byDesk: AttentionItem = { id: 'approval:a9', kind: 'approval', project_id: 'p', project_name: 'Tax 2026', agent_id: 'd', title: 'Desk wants to run web_fetch', detail: '', created_at: minutesAgo(2), ref: { approval_id: 'a9' } };
    const desk = ev(1, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/data/workspaces/desk', parent_id: null }, { agent: 'd' });
    await inspect({ item: byDesk }, watching([desk]));
    const insp = screen.getByRole('article', { name: 'Selected: clearance request' });
    expect(await within(insp).findByText('The request details are not available.')).toBeTruthy();
    expect(fact(insp, 'ASKED BY').textContent).toBe('Desk');
    expect(within(fact(insp, 'ASKED BY')).queryByRole('link')).toBeNull();
    expect(fact(insp, 'BRANCH').textContent).toBe('scratch workspace');
    expect(fact(insp, 'WORKDIR').textContent).toBe('workspaces/desk');
    expect(insp.querySelector('.rule-chip')).toBeNull();
  });

  it("offers Desk's options, a free answer, and the conversation", async () => {
    const { answer, open } = await inspect({ item: question });
    const insp = screen.getByRole('article', { name: 'Selected: question from desk' });
    expect(insp.textContent).toContain('· Tax 2026 · waiting 4m');
    const first = within(insp).getByRole('button', { name: 'Data source' });
    expect(first.className).toContain('btn-primary');
    expect(within(insp).getByRole('button', { name: 'Teammate' }).className).toContain('btn-secondary');
    fireEvent.click(first);
    const send = within(insp).getByRole('button', { name: 'Send' }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.input(within(insp).getByLabelText('Answer in your own words'), { target: { value: '  Both, data first  ' } });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    expect(answer.mock.calls).toEqual([['Data source'], ['Both, data first']]);
    fireEvent.click(within(insp).getByRole('button', { name: /^Open conversation/ }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(insp.textContent).not.toContain('Note to the thread');
  });

  it('says an answered question was sent instead of asking again', async () => {
    await inspect({ item: question, answered: true });
    const insp = screen.getByRole('article', { name: 'Selected: question from desk' });
    expect(within(insp).getByText('Sent. Desk picks it up at its next step.')).toBeTruthy();
    expect(within(insp).queryByRole('button', { name: 'Data source' })).toBeNull();
    expect(within(insp).queryByLabelText('Answer in your own words')).toBeNull();
    expect(within(insp).getByRole('button', { name: /^Open conversation/ })).toBeTruthy();
  });

  it('explains a hand-off, a stalled thread and a failed one, each with Open and Dismiss', async () => {
    const { dismiss, open, show } = await inspect({ item: handoff });
    const insp = screen.getByRole('article', { name: 'Selected: from a report' });
    expect(insp.querySelector('.why-label')!.textContent).toBe('From the report');
    expect(insp.querySelector('.why-row p')!.textContent).toBe('Research is in');
    expect(insp.textContent).toContain('Dismiss once it is done. Desk is not told; tell it in the conversation if it needs to know.');
    fireEvent.click(within(insp).getByRole('button', { name: /^Open conversation/ }));
    fireEvent.click(within(insp).getByRole('button', { name: 'Dismiss' }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(dismiss).toHaveBeenCalledTimes(1);

    await show({ item: stalled });
    expect(insp.getAttribute('aria-label')).toBe('Selected: stalled thread');
    expect(insp.textContent).toContain('· Tax 2026 · waiting 30m');
    expect(insp.querySelector('.why-label')!.textContent).toBe('What the thread said');
    expect(insp.querySelector('.why-quote strong')!.textContent).toBe('Waiting');
    expect(insp.querySelector('.why-quote')!.textContent).toContain('on the API key');
    expect(insp.textContent).toContain('Dismissing hides this here. The thread is not changed.');
    fireEvent.click(within(insp).getByRole('button', { name: /^Open thread/ }));
    expect(open).toHaveBeenCalledTimes(2);

    await show({ item: failed, busy: 'dismiss' });
    expect(insp.getAttribute('aria-label')).toBe('Selected: failed thread');
    expect(insp.querySelector('.why-label')!.textContent).toBe('Reason');
    expect(insp.querySelector('.why-row p')!.textContent).toBe('The model endpoint refused the key');
    expect(within(insp).getByRole('button', { name: 'Dismiss' }).getAttribute('aria-busy')).toBe('true');
  });

  it("holds a paused project's Resume, and waits while it resumes", async () => {
    const { dismiss, open, show } = await inspect({ item: paused });
    const insp = screen.getByRole('article', { name: 'Selected: paused project' });
    expect(insp.querySelector('.strip-code-badge')!.textContent).toBe('GND');
    expect(insp.querySelector('.inspector-title')!.textContent).toBe('Agents in Tax 2026 are paused: too many automatic wakes this hour');
    expect(insp.textContent).toContain('· Tax 2026 · waiting 4m');
    expect(insp.textContent).toContain('Their messages are kept. Resume, or write to any agent.');
    expect(insp.textContent).toContain('Resuming lets agents wake each other again.');
    expect(insp.textContent).not.toMatch(/What the thread said|Open thread|The thread is not changed/);
    fireEvent.click(within(insp).getByRole('button', { name: /^Open conversation/ }));
    fireEvent.click(within(insp).getByRole('button', { name: 'Resume' }));
    expect(open).toHaveBeenCalledTimes(1);
    expect(dismiss).toHaveBeenCalledTimes(1);

    await show({ item: paused, busy: 'dismiss' });
    const resume = within(insp).getByRole('button', { name: 'Resume' }) as HTMLButtonElement;
    expect(resume.getAttribute('aria-busy')).toBe('true');
    expect(resume.disabled).toBe(true);
  });
});
