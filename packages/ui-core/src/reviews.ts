import type { FindingRow, ReceiptRow, ReviewRow, SubmissionRow, ThreadReview } from '@desk/client';
import type { Acceptance, StoredEvent } from '@desk/protocol';

/** A chip's wording and colour, in the status chip's tones (`chip-<tone>`). */
export type ReviewChip = { label: string; tone: 'run' | 'wait' | 'done' | 'fail' | 'idle' };

const ACCEPTANCE: Record<Exclude<Acceptance, 'none'>, ReviewChip> = {
  in_review: { label: 'In review', tone: 'run' },
  reviewed: { label: 'Reviewed', tone: 'wait' },
  changes_requested: { label: 'Changes requested', tone: 'fail' },
  accepted: { label: 'Accepted', tone: 'done' },
  accepted_with_limitations: { label: 'Accepted with limitations', tone: 'done' },
};

/**
 * The chip a thread shows on its card and head (reviews and acceptance spec §7): where its latest submission stands, or
 * `Reviewer` for a reviewer thread. Null when there is nothing to say.
 */
export function acceptanceChip(t: { acceptance: Acceptance; reviews_submission_id: string | null }): ReviewChip | null {
  if (t.reviews_submission_id) return { label: 'Reviewer', tone: 'idle' };
  return t.acceptance === 'none' ? null : ACCEPTANCE[t.acceptance];
}

const VERDICT: Record<string, ReviewChip> = {
  no_material_issues: { label: 'No material issues', tone: 'done' },
  issues_found: { label: 'Issues found', tone: 'fail' },
  could_not_review: { label: 'Could not review', tone: 'wait' },
};

const MET: Record<string, string> = { yes: 'Met', no: 'Not met', unknown: 'Not established' };

const FINDING_STATE: Record<string, string> = { open: 'Open', fixed: 'Fixed', waived: 'Waived', withdrawn: 'Withdrawn' };

export type FindingView = {
  id: string;
  title: string;
  detail: string;
  reproducer: string;
  blocking: boolean;
  open: boolean;
  /** `Fixed in submission 2`, `Waived by you: reason`; null while open. */
  outcome: string | null;
  /** The submission it was raised against. */
  raisedOn: number | null;
};

export type ReviewItemView = {
  id: string;
  reviewerId: string;
  submissionSeq: number | null;
  /** The verdict once filed, else where the review is. */
  chip: ReviewChip;
  requestedBy: 'desk' | 'user';
  criteria: string[];
  focus: string | null;
  requirements: Array<{ criterion: string; met: string; note: string; ok: boolean | null }>;
  notChecked: string[];
};

export type SubmissionView = {
  id: string;
  seq: number;
  commit: string | null;
  artifacts: Array<{ path: string; sha256: string }>;
  claims: string[];
  limitations: string[];
  evidence: string;
  createdAt: string;
  /** `Accepted`, `Accepted with limitations` or `Superseded`; null for the current one while undecided. */
  tag: string | null;
  /** Commands Desk recorded on its commit with no uncommitted changes, newest first (receipts spec 2026-10-03 §1). */
  ran: RanView[];
};

/** One recorded command: how it ended (`exit 0`, `timed out`…), whether that is a pass, and how long it took. */
export type RanView = { id: string; command: string; tool: string; ended: string; ok: boolean; duration: string };

/** `340 ms`, `1.2 s`, `42 s`, `2 min 5 s`. */
export function receiptDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s - m * 60)} s`;
}

export function ranView(r: ReceiptRow): RanView {
  const ended = r.outcome === 'timeout' ? 'timed out' : r.outcome === 'aborted' ? 'stopped' : r.outcome === 'killed' ? 'killed' : `exit ${r.exit_code}`;
  return { id: r.id, command: r.command, tool: r.tool, ended, ok: r.outcome === 'exit' && r.exit_code === 0, duration: receiptDuration(r.duration_ms) };
}

export type ReviewView = {
  chip: ReviewChip | null;
  current: SubmissionView | null;
  /** Older submissions, newest first. */
  earlier: SubmissionView[];
  /** Reviews, newest first. */
  reviews: ReviewItemView[];
  open: FindingView[];
  resolved: FindingView[];
  /** Open blocking findings: accepting needs them waived first. */
  blocking: number;
  /** The reviewer a re-review would reopen: the latest review's, when that review is of an earlier submission. */
  reReviewer: string | null;
  /** For a reviewer thread: the thread it reviews and the submission. */
  reviewing: { builderId: string; submissionSeq: number | null; filed: boolean } | null;
};

const who = (by: string | null) => (by === 'user' ? 'you' : by === 'desk' ? 'Desk' : by === 'reviewer' ? 'the reviewer' : 'someone');

function findingView(f: FindingRow, seqOf: (id: string | null) => number | null): FindingView {
  const outcome =
    f.state === 'open'
      ? null
      : f.state === 'fixed'
        ? `Fixed${seqOf(f.fixed_in) ? ` in submission ${seqOf(f.fixed_in)}` : ''}`
        : `${FINDING_STATE[f.state] ?? f.state} by ${who(f.resolved_by)}${f.reason ? `: ${f.reason}` : ''}`;
  return { id: f.id, title: f.title, detail: f.detail, reproducer: f.reproducer, blocking: f.blocking, open: f.state === 'open', outcome, raisedOn: seqOf(f.submission_id) };
}

function reviewItem(r: ReviewRow, seqOf: (id: string | null) => number | null): ReviewItemView {
  const chip: ReviewChip =
    r.phase === 'final' && r.verdict ? (VERDICT[r.verdict] ?? { label: r.verdict, tone: 'idle' }) : r.phase === 'initial' ? { label: 'Initial view filed', tone: 'run' } : { label: 'Reviewing', tone: 'run' };
  return {
    id: r.id,
    reviewerId: r.reviewer_id,
    submissionSeq: seqOf(r.submission_id),
    chip,
    requestedBy: r.requested_by,
    criteria: r.criteria,
    focus: r.focus,
    requirements: r.requirements.map((q) => ({ criterion: q.criterion, met: MET[q.met] ?? q.met, note: q.note, ok: q.met === 'yes' ? true : q.met === 'no' ? false : null })),
    notChecked: r.not_checked,
  };
}

/** What the Review tab shows, from `threads.review` (reviews and acceptance spec §7). */
export function reviewView(r: ThreadReview): ReviewView {
  const seqs = new Map(r.submissions.map((s) => [s.id, s.seq]));
  const seqOf = (id: string | null) => (id ? (seqs.get(id) ?? null) : null);
  const latest = r.submissions.at(-1) ?? null;
  const decided = r.acceptance === 'accepted' || r.acceptance === 'accepted_with_limitations';
  const sub = (s: SubmissionRow): SubmissionView => ({
    id: s.id,
    seq: s.seq,
    commit: s.commit,
    artifacts: s.artifacts,
    claims: s.claims,
    limitations: s.limitations,
    evidence: s.evidence,
    createdAt: s.created_at,
    tag: decided && s.id === r.accepted_submission_id ? ACCEPTANCE[r.acceptance as 'accepted'].label : s.superseded_by ? 'Superseded' : null,
    ran: (r.receipts?.[s.id] ?? []).map(ranView),
  });
  const findings = r.findings.map((f) => findingView(f, seqOf));
  const open = findings.filter((f) => f.open);
  const lastReview = r.reviews.at(-1);
  return {
    chip: acceptanceChip({ acceptance: r.acceptance, reviews_submission_id: r.reviewing ? r.reviewing.submission_id : null }),
    current: latest ? sub(latest) : null,
    earlier: r.submissions.slice(0, -1).reverse().map(sub),
    reviews: [...r.reviews].reverse().map((x) => reviewItem(x, seqOf)),
    open,
    resolved: findings.filter((f) => !f.open),
    blocking: open.filter((f) => f.blocking).length,
    reReviewer: lastReview && latest && lastReview.submission_id !== latest.id ? lastReview.reviewer_id : null,
    reviewing: r.reviewing ? { builderId: r.reviewing.builder_id, submissionSeq: r.reviewing.submission_seq, filed: r.reviewing.phase === 'final' } : null,
  };
}

const REVIEW_EVENTS = new Set(['submission.created', 'review.requested', 'review.assessed', 'finding.raised', 'finding.resolved', 'acceptance.recorded']);

/**
 * Changes whenever something the Review tab shows does: the thread's own events, and every review event (a reviewer's
 * findings are its own events, not the builder's).
 */
export function reviewVersion(events: readonly StoredEvent[], threadId: string): string {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.agent_id === threadId || REVIEW_EVENTS.has(e.type)) return String(e.id);
  }
  return '0';
}

/** Text typed one item per line, without blanks: criteria and limitations. */
export const linesOf = (text: string): string[] =>
  text
    .split('\n')
    .map((l) => l.replace(/^\s*[-*•]\s*/, '').trim())
    .filter(Boolean);

/** The first line of a review request's criteria box: the thread's own claims, as a start. */
export const criteriaDraft = (v: ReviewView): string => (v.current?.claims.length ? v.current.claims.join('\n') : '');

/** Short commit hash. */
export const shortCommit = (commit: string) => commit.slice(0, 10);
