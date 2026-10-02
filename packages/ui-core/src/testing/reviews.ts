import type { ThreadReview } from '@desk/client';

const T = '2026-10-02T10:00:00.000Z';

/**
 * A builder thread `t` whose first submission a reviewer `r` found a blocking problem in (still open), then a second
 * submission waiting on a re-review: what `threads.review` answers, for the Review tab's tests in both UIs.
 */
export function reviewFixture(over: Partial<ThreadReview> = {}): ThreadReview {
  const sub = (seq: number, superseded: string | null) => ({
    id: `s${seq}`,
    project_id: 'p',
    thread_id: 't',
    seq,
    commit: `${seq}f9a1c0e2b7d4a6c8e0f1a3b5c7d9e1f2a4b6c8d`,
    base: 'base000',
    artifacts: seq === 2 ? [{ path: 'reports/compare.md', sha256: 'ab'.repeat(32) }] : [],
    claims: ['Totals match the ledger'],
    limitations: seq === 2 ? ['Only EUR checked'] : [],
    evidence: 'Ran pytest: 12 passed',
    superseded_by: superseded,
    created_at: T,
  });
  return {
    acceptance: 'reviewed',
    accepted_submission_id: null,
    submissions: [sub(1, 's2'), sub(2, null)],
    reviews: [
      {
        id: 'rv1',
        project_id: 'p',
        submission_id: 's1',
        builder_id: 't',
        reviewer_id: 'r',
        criteria: ['Totals match the ledger'],
        focus: null,
        requested_by: 'desk',
        revealed: true,
        phase: 'final',
        verdict: 'issues_found',
        requirements: [{ criterion: 'Totals match the ledger', met: 'no', note: 'Off by one cent on March' }],
        not_checked: ['Currencies other than EUR'],
        created_at: T,
        updated_at: T,
      },
    ],
    findings: [
      {
        id: 'f1',
        project_id: 'p',
        review_id: 'rv1',
        submission_id: 's1',
        builder_id: 't',
        title: 'March total is off by one cent',
        detail: 'Rounding happens per line instead of per total.',
        blocking: true,
        reproducer: 'python compare.py --month 2026-03',
        state: 'open',
        fixed_in: null,
        reason: null,
        resolved_by: null,
        created_at: T,
        resolved_at: null,
      },
      {
        id: 'f2',
        project_id: 'p',
        review_id: 'rv1',
        submission_id: 's1',
        builder_id: 't',
        title: 'Header typo',
        detail: '',
        blocking: false,
        reproducer: '',
        state: 'waived',
        fixed_in: null,
        reason: 'Cosmetic',
        resolved_by: 'desk',
        created_at: T,
        resolved_at: T,
      },
    ],
    reviewing: null,
    ...over,
  };
}
