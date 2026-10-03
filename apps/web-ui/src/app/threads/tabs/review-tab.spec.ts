import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { ThreadReview, ThreadView } from '@desk/client';
import { reviewFixture } from '@desk/ui-core/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { ReviewTab } from './review-tab';

const thread = (id: string, title: string, extra: Partial<ThreadView> = {}): ThreadView => ({ id, project_id: 'p', title, archived_at: null, ...extra }) as unknown as ThreadView;
const threads = [thread('t', 'Comparator'), thread('r', 'Review: Comparator')];
const inputs = { thread: threads[0]!, threads, version: '1', now: Date.parse('2026-10-02T10:05:30.000Z') };

describe('ReviewTab', () => {
  it('shows the submission, findings, reviews and earlier submissions', async () => {
    const bridge = new FakeDeskBridge({ 'threads.review': () => reviewFixture() });
    await render(ReviewTab, { inputs, providers: bridge.providers });
    expect(await screen.findByText('March total is off by one cent')).toBeTruthy();
    expect(document.querySelector('.review-state > .chip')!.textContent).toBe('Reviewed');
    expect(document.querySelector('.review-state > span:last-child')!.textContent).toBe('Submission 2 · commit 2f9a1c0e2b · 1 file · 5m ago');
    expect(screen.getByLabelText('Reproducer').textContent).toBe('python compare.py --month 2026-03');
    expect(screen.getByText('Issues found')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Review: Comparator' }).getAttribute('href')).toBe('#/p/p/threads/r');
    expect(screen.getByText('Not met')).toBeTruthy();
    expect(screen.getByText('Not checked: Currencies other than EUR')).toBeTruthy();
    expect(screen.getByText('Superseded')).toBeTruthy();
    expect(screen.getByText('Waived by Desk: Cosmetic')).toBeTruthy();
    const ran = screen.getByRole('list', { name: 'Commands run on submission 2' });
    expect(within(ran).getByText('exit 0').className).toBe('met-yes');
    expect(ran.textContent).toContain('pytest -q');
    expect(ran.textContent).toContain('4.2 s');
    expect(screen.getByText('Desk saw no commands run on this commit with no uncommitted changes.')).toBeTruthy();
    expect(bridge.calls).toEqual([{ channel: 'threads.review', input: { id: 't' } }]);
  });

  it('waives a finding, then accepts with limitations and asks the same reviewer again', async () => {
    let review: ThreadReview = reviewFixture();
    const bridge = new FakeDeskBridge({
      'threads.review': () => review,
      'threads.waiveFinding': () => {
        review = { ...review, findings: review.findings.map((x) => ({ ...x, state: 'waived' as const, resolved_by: 'user' as const })) };
        return { ok: true };
      },
      'threads.accept': () => ({ submission_seq: 2, waived: 0 }),
      'threads.requestReview': () => ({ reviewer_id: 'r', review_id: 'rv2', reopened: true }),
    });
    await render(ReviewTab, { inputs, providers: bridge.providers });
    fireEvent.click(await screen.findByRole('button', { name: 'Accept with limitations' }));
    expect(screen.getByText('Waive the 1 open blocking finding below first, or request changes.')).toBeTruthy();
    const form = screen.getByRole('group', { name: 'Accept with limitations' });
    expect((within(form).getByRole('button', { name: 'Accept with limitations' }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole('button', { name: 'Waive' }));
    fireEvent.input(screen.getByLabelText('Why waive it'), { target: { value: 'Known rounding' } });
    fireEvent.click(screen.getByRole('button', { name: 'Waive finding' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'threads.waiveFinding')?.input).toEqual({ findingId: 'f1', req: { reason: 'Known rounding' } }));
    expect(await screen.findByText('Open findings (0)')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Accept with limitations' }));
    expect(screen.queryByText(/Waive the 1 open blocking/)).toBeNull();
    fireEvent.input(screen.getByLabelText('Limitations, one per line'), { target: { value: '- Only EUR checked\n\nNo March' } });
    fireEvent.click(within(screen.getByRole('group', { name: 'Accept with limitations' })).getByRole('button', { name: 'Accept with limitations' }));
    await waitFor(() =>
      expect(bridge.calls.find((c) => c.channel === 'threads.accept')?.input).toEqual({ id: 't', req: { decision: 'accepted_with_limitations', limitations: ['Only EUR checked', 'No March'] } }),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Request a re-review' }));
    expect((screen.getByLabelText('Acceptance criteria, one per line') as HTMLTextAreaElement).value).toBe('Totals match the ledger');
    expect(screen.getByText('Ask Review: Comparator again')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('group', { name: 'Request a review' })).getByRole('button', { name: 'Request a review' }));
    await waitFor(() =>
      expect(bridge.calls.find((c) => c.channel === 'threads.requestReview')?.input).toEqual({ id: 't', req: { criteria: ['Totals match the ledger'], reviewer_id: 'r' } }),
    );
  });

  it('says why a decision failed', async () => {
    const bridge = new FakeDeskBridge({
      'threads.review': () => reviewFixture({ findings: [] }),
      'threads.accept': () => {
        throw { code: 'conflict', message: 'Comparator has not submitted anything yet.', status: 409 };
      },
    });
    await render(ReviewTab, { inputs, providers: bridge.providers });
    fireEvent.click(await screen.findByRole('button', { name: 'Request changes' }));
    fireEvent.input(screen.getByLabelText('What needs to change'), { target: { value: 'Round per total.' } });
    fireEvent.click(within(screen.getByRole('group', { name: 'Request changes' })).getByRole('button', { name: 'Request changes' }));
    expect(await screen.findByText('Comparator has not submitted anything yet.')).toBeTruthy();
  });

  it('leaves the decisions to the Mac on a phone', async () => {
    const phone = new FakeDeskBridge({ 'threads.review': () => reviewFixture() });
    phone.remote = true;
    await render(ReviewTab, { inputs, providers: phone.providers });
    expect(await screen.findByText('Accept, request changes or a review on your Mac.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Waive' })).toBeNull();
  });

  it('points a reviewer thread at the thread it reviews', async () => {
    const f = reviewFixture();
    const bridge = new FakeDeskBridge({ 'threads.review': () => ({ ...f, acceptance: 'none', submissions: [], reviews: [], findings: [], reviewing: { ...f.reviews[0]!, phase: 'final', submission_seq: 1 } }) });
    await render(ReviewTab, { inputs: { ...inputs, thread: threads[1]! }, providers: bridge.providers });
    const p = await screen.findByText(/This thread reviews/);
    expect(p.textContent).toBe("This thread reviews submission 1 of Comparator, and has filed its review. Its findings and verdict are on that thread's Review tab.");
  });

  it('explains a thread with nothing submitted', async () => {
    const f = reviewFixture();
    const empty = new FakeDeskBridge({ 'threads.review': () => ({ ...f, acceptance: 'none', submissions: [], reviews: [], findings: [] }) });
    await render(ReviewTab, { inputs, providers: empty.providers });
    expect(await screen.findByRole('heading', { name: 'Nothing submitted yet' })).toBeTruthy();
  });
});
