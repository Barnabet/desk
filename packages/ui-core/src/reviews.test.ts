import { describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import { acceptanceChip, criteriaDraft, linesOf, ranView, reviewVersion, reviewView } from './reviews';
import { reviewFixture } from './testing/automations';

describe('acceptanceChip', () => {
  it('says where the latest submission stands, nothing before one, and Reviewer for a reviewer', () => {
    expect(acceptanceChip({ acceptance: 'none', reviews_submission_id: null })).toBeNull();
    expect(acceptanceChip({ acceptance: 'in_review', reviews_submission_id: null })).toEqual({ label: 'In review', tone: 'run' });
    expect(acceptanceChip({ acceptance: 'changes_requested', reviews_submission_id: null })).toEqual({ label: 'Changes requested', tone: 'fail' });
    expect(acceptanceChip({ acceptance: 'accepted_with_limitations', reviews_submission_id: null })).toEqual({ label: 'Accepted with limitations', tone: 'done' });
    expect(acceptanceChip({ acceptance: 'none', reviews_submission_id: 's1' })).toEqual({ label: 'Reviewer', tone: 'idle' });
  });
});

describe('ranView', () => {
  it('says how a recorded command ended and whether that passed', () => {
    const r = reviewFixture().receipts.s2![0]!;
    expect(ranView({ ...r, outcome: 'timeout', exit_code: null, duration_ms: 125_000 })).toMatchObject({ ended: 'timed out', ok: false, duration: '2 min 5 s' });
    expect(ranView({ ...r, exit_code: 1, duration_ms: 340 })).toMatchObject({ ended: 'exit 1', ok: false, duration: '340 ms' });
  });
});

describe('reviewView', () => {
  it('orders submissions and reviews newest first, splits findings and offers the earlier reviewer again', () => {
    const v = reviewView(reviewFixture());
    expect(v.chip).toEqual({ label: 'Reviewed', tone: 'wait' });
    expect(v.current).toMatchObject({ seq: 2, tag: null, limitations: ['Only EUR checked'] });
    expect(v.earlier.map((s) => [s.seq, s.tag])).toEqual([[1, 'Superseded']]);
    expect(v.current!.ran).toEqual([{ id: 'rc1', command: 'pytest -q', tool: 'bash', ended: 'exit 0', ok: true, duration: '4.2 s' }]);
    expect(v.earlier[0]!.ran).toEqual([]);
    expect(reviewView({ ...reviewFixture(), receipts: undefined as never }).current!.ran).toEqual([]);
    expect(v.reviews).toEqual([
      expect.objectContaining({
        reviewerId: 'r',
        submissionSeq: 1,
        chip: { label: 'Issues found', tone: 'fail' },
        requirements: [{ criterion: 'Totals match the ledger', met: 'Not met', note: 'Off by one cent on March', ok: false }],
        notChecked: ['Currencies other than EUR'],
      }),
    ]);
    expect(v.open.map((f) => [f.id, f.blocking, f.raisedOn])).toEqual([['f1', true, 1]]);
    expect(v.resolved.map((f) => f.outcome)).toEqual(['Waived by Desk: Cosmetic']);
    expect(v.blocking).toBe(1);
    expect(v.reReviewer).toBe('r');
    expect(criteriaDraft(v)).toBe('Totals match the ledger');
  });

  it('tags the accepted submission, and offers no re-review of the submission already reviewed', () => {
    const f = reviewFixture();
    const v = reviewView({ ...f, acceptance: 'accepted', accepted_submission_id: 's1', submissions: f.submissions.slice(0, 1).map((s) => ({ ...s, superseded_by: null })) });
    expect(v.current?.tag).toBe('Accepted');
    expect(v.reReviewer).toBeNull();
  });

  it('says a fixed finding was fixed in which submission, and what a reviewer thread reviews', () => {
    const f = reviewFixture();
    const v = reviewView({ ...f, findings: [{ ...f.findings[0]!, state: 'fixed', fixed_in: 's2', resolved_by: 'reviewer' }] });
    expect(v.resolved[0]!.outcome).toBe('Fixed in submission 2');
    const reviewer = reviewView({ ...f, acceptance: 'none', submissions: [], reviews: [], findings: [], reviewing: { ...f.reviews[0]!, phase: null, submission_seq: 1 } });
    expect(reviewer.chip).toEqual({ label: 'Reviewer', tone: 'idle' });
    expect(reviewer.reviewing).toEqual({ builderId: 't', submissionSeq: 1, filed: false });
  });
});

describe('reviewVersion and linesOf', () => {
  it("moves with the thread's own events and with every review event", () => {
    const events = [ev(1, 'agent.status_changed', { status: 'done' }, { agent: 't' }), ev(2, 'finding.raised', { finding_id: 'f', review_id: 'rv', submission_id: 's', title: 'x', detail: '', blocking: false, reproducer: '' }, { agent: 'r' }), ev(3, 'agent.status_changed', { status: 'running' }, { agent: 'o' })];
    expect(reviewVersion(events, 't')).toBe('2');
    expect(reviewVersion(events.slice(0, 1), 't')).toBe('1');
    expect(reviewVersion([], 't')).toBe('0');
  });

  it('reads one item per line, without bullets or blanks', () => {
    expect(linesOf('- Totals match\n\n  * Runs offline \n• Fast')).toEqual(['Totals match', 'Runs offline', 'Fast']);
  });
});
