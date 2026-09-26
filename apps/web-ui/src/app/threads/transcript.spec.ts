import { afterEveryRender, computed, Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { emptyMessages, type TranscriptEntry } from '@desk/client';
import { narrate } from '@desk/ui-core';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { Transcript, type ComposerMode, type Depth } from './transcript';

const TS = '2026-09-24T10:18:00.000Z';
const brief: TranscriptEntry = { kind: 'brief', id: 'e:1', ts: TS, text: 'Draft five emails.' };
const running: TranscriptEntry = { kind: 'status', id: 'e:2', ts: TS, status: 'running', reason: null };
const read = (content: string): TranscriptEntry => ({ kind: 'tools', id: 'tools:3', ts: TS, calls: [{ id: 'c1', name: 'read_file', arguments: '{"path":"brief.md"}', status: 'ok', content }] });
const result: TranscriptEntry = { kind: 'result', id: 'e:6', ts: TS, summary: 'Five drafts published', artifacts: ['emails/01.md'] };
const asked: TranscriptEntry = { kind: 'incoming', id: 'e:4', ts: TS, fromAgentId: 'f', fromLabel: 'thread "Frontend" (f)', messageKind: 'question', text: 'Which currency?' };

const TEMPLATE = `<aside deskTranscript projectId="p" threadId="t" [rows]="rows()" [entries]="entries()" [reviewRounds]="2" [messages]="messages" [sent]="sent" [selected]="selected" [depth]="depth" [composer]="composer" (selectStop)="onSelect($event)" (depthChange)="onDepth($event)" (pair)="onPair($event)"><button type="button">Stop</button></aside>`;

/** Renders the transcript of thread t; `entries` is a signal, so a case can land new entries. */
async function mount(list: TranscriptEntry[], o: { selected?: number | null; depth?: Depth; composer?: ComposerMode; handlers?: FakeHandlers } = {}) {
  const bridge = new FakeDeskBridge(o.handlers ?? {});
  const entries = signal(list);
  const props = {
    entries,
    rows: computed(() => narrate(entries())),
    messages: emptyMessages(),
    sent: new Map<string, number>(),
    selected: o.selected ?? null,
    depth: o.depth ?? 'narrative',
    composer: o.composer ?? ({ kind: 'steer', hint: 'For new work, message Desk.' } as ComposerMode),
    onSelect: vi.fn(),
    onDepth: vi.fn(),
    onPair: vi.fn(),
  };
  await render(TEMPLATE, { imports: [Transcript], componentProperties: props, providers: bridge.providers });
  return { ...props, bridge, tr: screen.getByRole('complementary', { name: 'Transcript' }) };
}

describe('Transcript', () => {
  it('numbers the Narrative stops, marks the selected one, holds the actions, and tells which stop was clicked', async () => {
    const r = await mount([brief, running, read('the brief'), result], { selected: 2 });
    expect(within(r.tr).getByLabelText('Stop 1')).toBeTruthy();
    expect([...r.tr.querySelectorAll('.tr-title')].map((t) => t.textContent)).toEqual([
      'Brief from Desk',
      expect.stringMatching(/^Used 1 tool · read_file · \d\d:\d\d$/),
      expect.stringMatching(/^Reported to Desk · \d\d:\d\d$/),
    ]);
    expect(document.getElementById('tr-stop-2')!.className).toBe('tr-entry selected');
    expect(document.getElementById('tr-stop-1')!.className).toBe('tr-entry');
    expect(within(r.tr.querySelector<HTMLElement>('.transcript-head')!).getByRole('button', { name: 'Stop' })).toBeTruthy();
    expect(within(r.tr).getByRole('link', { name: 'emails/01.md' }).getAttribute('href')).toBe('#/p/p/library?file=emails%2F01.md');
    expect(r.tr.querySelector('#tr-stop-2 .toolgroup-names')!.textContent).toBe('read_file');
    fireEvent.click(document.getElementById('tr-stop-3')!);
    expect(r.onSelect).toHaveBeenCalledWith(3);
  });

  it('shows every entry in Every step, with full tool calls and a Show all for long output', async () => {
    const r = await mount([brief, running, read('x'.repeat(7000))], { depth: 'steps' });
    expect(within(r.tr).getByText(/^Status → running · \d\d:\d\d$/)).toBeTruthy();
    // The status change starts no stop: its badge is blank.
    expect(r.tr.querySelectorAll('.tr-num-blank')).toHaveLength(1);
    expect(within(r.tr).getByText('read_file')).toBeTruthy();
    const lengths = () => [...r.tr.querySelectorAll('.tr-call code')].map((c) => c.textContent!.length);
    expect(lengths()).toEqual([JSON.stringify({ path: 'brief.md' }, null, 2).length, 6002]);
    fireEvent.click(within(r.tr).getByRole('button', { name: 'Show all 7,000 characters' }));
    await waitFor(() => expect(lengths()[1]).toBe(7000));
    fireEvent.click(within(r.tr).getByRole('button', { name: 'Narrative' }));
    expect(r.onDepth).toHaveBeenCalledWith('narrative');
  });

  it('steers with Enter, keeps Shift+Enter for a new line, and shows the send until it lands', async () => {
    const r = await mount([brief, read('the brief')], { handlers: { 'threads.send': () => ({ ok: true }) } });
    const box = within(r.tr).getByLabelText('Steer this thread') as HTMLTextAreaElement;
    expect(box.getAttribute('placeholder')).toBe('Steer this thread. It reads this at its next step.');
    expect(within(r.tr).getByText('For new work, message Desk.')).toBeTruthy();
    fireEvent.input(box, { target: { value: 'Keep it short' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(r.bridge.calls).toEqual([]);
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(r.bridge.calls).toEqual([{ channel: 'threads.send', input: { id: 't', text: 'Keep it short' } }]));
    expect(await within(r.tr).findByText('You · steering…')).toBeTruthy();
    expect(box.value).toBe('');
    r.entries.set([brief, read('the brief'), { kind: 'steer', id: 'e:9', ts: TS, text: 'Keep it short' }]);
    await waitFor(() => expect(within(r.tr).queryByText('You · steering…')).toBeNull());
    expect(within(r.tr).getByText(/^You steered · \d\d:\d\d$/)).toBeTruthy();
  });

  it('asks an idle thread, and resumes it with the message only after confirming', async () => {
    const r = await mount([brief], { composer: { kind: 'ask', reopen: 'Resume' }, handlers: { 'threads.send': () => ({ ok: true }) } });
    const box = within(r.tr).getByLabelText('Ask this thread');
    expect(box.getAttribute('placeholder')).toBe('Ask about its work. It answers from its context.');
    expect(within(r.tr).getByText('It answers from its context; its result stays as it is.')).toBeTruthy();
    expect(within(r.tr).queryByRole('button', { name: 'Steer' })).toBeNull();
    fireEvent.input(box, { target: { value: 'Carry on with the footer.' } });
    fireEvent.click(within(r.tr).getByRole('button', { name: 'Resume with this…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Resume this thread?' });
    expect(dialog.textContent).toContain('Resuming lets it change its work; its result and branch may change.');
    expect(r.bridge.calls).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(r.bridge.calls).toEqual([{ channel: 'threads.send', input: { id: 't', text: 'Carry on with the footer.' } }]));
    expect(await within(r.tr).findByText('You · steering…')).toBeTruthy();
    fireEvent.input(box, { target: { value: 'How long are they?' } });
    fireEvent.click(within(r.tr).getByRole('button', { name: 'Ask' }));
    await waitFor(() => expect(r.bridge.calls.at(-1)?.input).toEqual({ id: 't', text: 'How long are they?', question: true }));
    expect(await within(r.tr).findByText('You · asking…')).toBeTruthy();
  });

  it('turns the box off for an archived thread', async () => {
    const r = await mount([brief], { composer: { kind: 'off', hint: 'This thread is archived.' } });
    expect((within(r.tr).getByLabelText('Steer this thread') as HTMLTextAreaElement).disabled).toBe(true);
    expect(within(r.tr).getByText('This thread is archived.')).toBeTruthy();
    expect((within(r.tr).getByRole('button', { name: 'Steer' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows another thread's message as a card in its stop, whose sender opens the pair sheet without selecting the stop", async () => {
    const r = await mount([brief, asked]);
    const card = r.tr.querySelector<HTMLElement>('#tr-stop-2 .tr-card')!;
    expect(card.className).toBe('tr-card tr-card-in');
    expect(card.querySelector('.tr-card-head')!.textContent).toMatch(/^Frontend asked · \d\d:\d\d$/);
    expect(within(card).getByText('Which currency?')).toBeTruthy();
    expect(r.tr.querySelector('#tr-stop-2 .tr-title')!.textContent).toMatch(/^Messages · \d\d:\d\d$/);
    fireEvent.click(within(card).getByRole('button', { name: 'Frontend' }));
    expect(r.onPair).toHaveBeenCalledWith('f');
    expect(r.onSelect).not.toHaveBeenCalled();
  });

  it('follows new entries while the list is at its bottom, stays where it was scrolled up to, and never renders on a scroll', async () => {
    const r = await mount([brief, read('the brief')]);
    const list = r.tr.querySelector<HTMLElement>('.transcript-list')!;
    // jsdom lays nothing out: a 1000 px list in a 100 px box.
    Object.defineProperty(list, 'scrollHeight', { value: 1000 });
    Object.defineProperty(list, 'clientHeight', { value: 100 });
    let renders = 0;
    afterEveryRender(() => renders++, { injector: TestBed.inject(Injector) });
    await new Promise((res) => setTimeout(res, 50)); // adding a render hook schedules a render itself
    renders = 0;

    // Plain DOM events: testing-library's fireEvent would run change detection itself.
    const scroll = () => list.dispatchEvent(new Event('scroll'));
    list.scrollTop = 200;
    for (let i = 0; i < 5; i++) scroll();
    await new Promise((res) => setTimeout(res, 50));
    expect(renders).toBe(0);
    r.entries.set([brief, read('the brief'), result]);
    await waitFor(() => expect(r.tr.querySelectorAll('.tr-entry')).toHaveLength(3));
    expect(list.scrollTop).toBe(200);

    list.scrollTop = 880;
    scroll();
    r.entries.set([brief, read('the brief'), result, { kind: 'steer', id: 'e:9', ts: TS, text: 'Shorter, please' }]);
    await waitFor(() => expect(list.scrollTop).toBe(1000));
  });
});
