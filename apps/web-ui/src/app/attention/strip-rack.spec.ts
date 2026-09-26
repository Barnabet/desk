import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { AttentionBays } from '@desk/client';
import type { AttentionItem } from '@desk/protocol';
import { FlightStrip } from './flight-strip';
import { StripRack } from './strip-rack';

const NOW = Date.now();
const minutesAgo = (m: number) => new Date(NOW - m * 60_000).toISOString();

const approval: AttentionItem = { id: 'approval:a1', kind: 'approval', project_id: 'p', project_name: 'Tax 2026', agent_id: 't', title: 'Signup checklist wants to run bash', detail: '', created_at: minutesAgo(60), ref: { approval_id: 'a1', thread_id: 't' } };
const question: AttentionItem = { id: 'question:7', kind: 'question', project_id: 'p', project_name: 'Tax 2026', agent_id: 'd', title: 'Data source or teammate first?', detail: '', created_at: minutesAgo(4), ref: { event_id: 7, options: ['Data source', 'Teammate'] } };
const stalled: AttentionItem = { id: 'stalled:u:9', kind: 'stalled', project_id: 'q', project_name: 'Launch', agent_id: 'u', title: 'Pricing page has stalled', detail: 'Waiting on the API key', created_at: minutesAgo(180), ref: { thread_id: 'u', event_id: 9 } };

const bays = (): AttentionBays => ({ clearance: [approval], queries: [question], handoffs: [], holding: [stalled] });
const TITLES: Record<string, string> = { t: 'Signup checklist' };
const threadTitle = (id: string): string | null => TITLES[id] ?? null;

const RACK = `<section deskStripRack [bays]="bays" [now]="now" [selectedId]="selectedId" [threadTitle]="threadTitle" (pick)="pick($event)"></section>`;

/** The rack of `bays()`, with `selectedId` selected; returns the region and the pick spy. */
async function rack(selectedId: string | null = 'question:7') {
  const pick = vi.fn();
  await render(RACK, { imports: [StripRack], componentProperties: { bays: bays(), now: NOW, selectedId, threadTitle, pick } });
  return { pick, region: screen.getByRole('region', { name: 'Strip rack' }) };
}

describe('StripRack and FlightStrip', () => {
  it('racks four bays with their counts, and says so when one is empty', async () => {
    const { region } = await rack();
    expect(region.tagName).toBe('SECTION');
    expect(region.className).toBe('rack');
    expect(within(region).getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual([
      'CLEARANCE: Approvals, 1',
      'QUERIES: Questions, 1',
      'HANDOFFS: From reports, 0',
      'HOLDING: Stalled, failed or paused, 1',
    ]);
    const handoffs = within(region).getByRole('group', { name: 'HANDOFFS: From reports, 0' });
    expect(handoffs.querySelector('.bay-title')!.textContent).toBe('HANDOFFS');
    expect(handoffs.querySelector('.bay-sub')!.textContent).toBe('From reports · 0');
    expect(within(handoffs).getByText('Nothing here').className).toBe('bay-empty');
    expect(within(handoffs).queryByRole('button')).toBeNull();
  });

  it("shows each strip's code, age, project, title, who and wait, and marks the selected one", async () => {
    await rack();
    const apr = screen.getByRole('button', { name: /^APR, / });
    expect(apr.getAttribute('aria-label')).toBe('APR, Tax 2026: Signup checklist wants to run bash. Thread Signup checklist. Waiting 1h.');
    expect(apr.getAttribute('type')).toBe('button');
    expect(apr.className).toBe('strip strip-approval');
    expect(apr.getAttribute('aria-current')).toBeNull();
    expect([...apr.querySelectorAll('.strip-code, .strip-age, .strip-project, .strip-text, .strip-name, .strip-tag')].map((e) => e.textContent)).toEqual([
      'APR',
      '1h',
      'Tax 2026',
      'Signup checklist wants to run bash',
      'Signup checklist',
      'bash',
    ]);
    const aprBar = apr.querySelectorAll('svg rect')[1]!;
    expect(aprBar.getAttribute('width')).toBe('38');
    expect(aprBar.getAttribute('fill')).toBe('var(--accent)');

    const ask = screen.getByRole('button', { name: /^ASK, / });
    expect(ask.getAttribute('aria-label')).toBe('ASK, Tax 2026: Data source or teammate first?. Asked by Desk. Waiting 4m.');
    expect(ask.className).toBe('strip strip-question selected');
    expect(ask.getAttribute('aria-current')).toBe('true');
    expect(ask.querySelector('.strip-tag')!.textContent).toBe('2 options');
    // A fresh item still shows a sliver of the gauge.
    expect(Number(ask.querySelectorAll('svg rect')[1]!.getAttribute('width'))).toBeCloseTo(3.04);

    const hld = screen.getByRole('button', { name: /^HLD, / });
    expect(hld.querySelector('.strip-name')!.textContent).toBe('Pricing page');
    expect(hld.querySelector('.strip-tag')!.className).toBe('strip-tag wait');
    expect(hld.querySelector('.strip-tag')!.textContent).toBe('stalled');
    expect(hld.querySelector('.strip-age-big')!.textContent).toBe('3h');
    const hldBar = hld.querySelectorAll('svg rect')[1]!;
    expect(hldBar.getAttribute('width')).toBe('76');
    expect(hldBar.getAttribute('fill')).toBe('var(--wait)');
  });

  it('picks a strip by its item id', async () => {
    const { pick, region } = await rack(null);
    expect(region.querySelector('[aria-current]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^HLD, / }));
    expect(pick).toHaveBeenCalledWith('stalled:u:9');
  });

  it('keeps only the cap, the title and the chevron on a compact strip', async () => {
    const pick = vi.fn();
    await render(`<button deskFlightStrip [item]="item" [now]="now" [threadTitle]="threadTitle" [compact]="true" (pick)="pick()"></button>`, {
      imports: [FlightStrip],
      componentProperties: { item: approval, now: NOW, threadTitle, pick },
    });
    const strip = screen.getByRole('button', { name: /^APR, Tax 2026: Signup checklist wants to run bash\./ });
    expect(strip.className).toBe('strip strip-approval compact');
    expect(strip.querySelector('.strip-who')).toBeNull();
    expect(strip.querySelector('svg')).toBeNull();
    expect(strip.querySelector('.strip-text')!.textContent).toBe('Signup checklist wants to run bash');
    expect(strip.querySelector('.strip-chevron')!.textContent).toBe('›');
    fireEvent.click(strip);
    expect(pick).toHaveBeenCalledTimes(1);
  });
});
