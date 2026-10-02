import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { and, asc, desc, eq } from 'drizzle-orm';
import { quoteLines, snippet, type Acceptance, type ModelInfo, type ProjectSettings } from '@desk/protocol';
import type { Db } from '../db/open';
import { findings, reviews, submissions } from '../db/schema';
import type { AgentRow } from '../state/queries';

export type SubmissionRow = typeof submissions.$inferSelect;
export type ReviewRow = typeof reviews.$inferSelect;
export type FindingRow = typeof findings.$inferSelect;

export const getSubmission = (db: Db, id: string): SubmissionRow | undefined => db.select().from(submissions).where(eq(submissions.id, id)).get();

/** A thread's submissions, oldest first. */
export const listSubmissions = (db: Db, threadId: string): SubmissionRow[] =>
  db.select().from(submissions).where(eq(submissions.thread_id, threadId)).orderBy(asc(submissions.seq)).all();

/** A thread's current submission: the one nothing superseded. */
export const latestSubmission = (db: Db, threadId: string): SubmissionRow | undefined =>
  db.select().from(submissions).where(eq(submissions.thread_id, threadId)).orderBy(desc(submissions.seq)).limit(1).get();

export const getReview = (db: Db, id: string): ReviewRow | undefined => db.select().from(reviews).where(eq(reviews.id, id)).get();

/** Every review of a thread's submissions, oldest first. */
export const reviewsOfBuilder = (db: Db, builderId: string): ReviewRow[] =>
  db.select().from(reviews).where(eq(reviews.builder_id, builderId)).orderBy(asc(reviews.created_at), asc(reviews.id)).all();

/** A reviewer thread's latest review (a re-review adds one). */
export const currentReviewOf = (db: Db, reviewerId: string): ReviewRow | undefined =>
  db.select().from(reviews).where(eq(reviews.reviewer_id, reviewerId)).orderBy(desc(reviews.created_at), desc(reviews.id)).limit(1).get();

export const getFinding = (db: Db, id: string): FindingRow | undefined => db.select().from(findings).where(eq(findings.id, id)).get();

/** Every finding about a thread's work, oldest first. */
export const findingsOfBuilder = (db: Db, builderId: string): FindingRow[] =>
  db.select().from(findings).where(eq(findings.builder_id, builderId)).orderBy(asc(findings.created_at), asc(findings.id)).all();

/** Findings still open against a thread's work, from any of its reviews. */
export const openFindings = (db: Db, builderId: string): FindingRow[] =>
  db.select().from(findings).where(and(eq(findings.builder_id, builderId), eq(findings.state, 'open'))).orderBy(asc(findings.created_at), asc(findings.id)).all();

/** SHA-256 of a file, streamed. */
export function sha256File(path: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const hash = createHash('sha256');
    createReadStream(path)
      .on('error', reject)
      .on('data', (chunk) => hash.update(chunk))
      .on('end', () => resolve(hash.digest('hex')));
  });
}

/**
 * The reviewer's model (spec §10 decision 3): the request's, else the project's `review_model`, else Desk's model when
 * its family differs from the builder's, else the first configured model of another family, else the thread model.
 */
export function chooseReviewModel(settings: ProjectSettings, builderModel: string, models: { get(id: string): ModelInfo; list(): ModelInfo[] }, requested?: string): string {
  if (requested) return requested;
  if (settings.review_model) return settings.review_model;
  const family = safeFamily(models, builderModel);
  if (family) {
    if (safeFamily(models, settings.desk_model) !== family && safeFamily(models, settings.desk_model)) return settings.desk_model;
    const other = models.list().find((m) => m.family !== family);
    if (other) return other.id;
  }
  return settings.thread_model;
}

function safeFamily(models: { get(id: string): ModelInfo }, id: string): string | undefined {
  try {
    return models.get(id).family;
  } catch {
    return undefined;
  }
}

/** One finding on one line, for notices and prompts: the reviewer's words as snippets. */
export const formatFindingLine = (f: FindingRow): string =>
  `- ${f.id} ${f.blocking ? '(blocking) ' : ''}${snippet(f.title, 160)} [${f.state}]${f.state === 'open' ? `; reproduce: ${snippet(f.reproducer, 300)}` : ''}`;

/** A submission on one line: `Submission 2: commit 3f9a1c0, 2 artifacts`. */
export function formatSubmissionLine(s: SubmissionRow): string {
  const parts = [s.commit ? `commit ${s.commit.slice(0, 10)}` : 'no commit', `${s.artifacts.length} artifact${s.artifacts.length === 1 ? '' : 's'}`];
  return `Submission ${s.seq} (${s.id}): ${parts.join(', ')}`;
}

/** The builder's own report, quoted: what a reviewer is shown once it has formed its view. */
export function builderReport(t: AgentRow, s: SubmissionRow): string {
  const list = (label: string, items: string[]) => (items.length ? `${label}:\n${quoteLines(items.map((i) => `- ${i}`).join('\n'))}` : `${label}: (none given)`);
  return [
    `The builder's report on submission ${s.seq}, in its own words:`,
    `Summary:\n${quoteLines(t.result_summary ?? '(none)')}`,
    list('Requirements it says it met', s.claims),
    list('Limitations it reported', s.limitations),
    `How it says it verified:\n${quoteLines(s.evidence || '(not said)')}`,
  ].join('\n');
}

/** A filed review as the reviewer thread's result: what Desk reads in the completion notice. */
export function renderReview(r: ReviewRow, own: FindingRow[], builderTitle: string): string {
  const req = r.requirements.map((q) => `- [${q.met}] ${q.criterion}${q.note ? `: ${q.note}` : ''}`);
  const open = own.filter((f) => f.state === 'open');
  const blocking = open.filter((f) => f.blocking).length;
  return [
    `Review of "${builderTitle}": ${r.verdict?.replace(/_/g, ' ') ?? 'no verdict'}${open.length ? `, ${open.length} open finding${open.length === 1 ? '' : 's'} (${blocking} blocking)` : ''}.`,
    ...(req.length ? ['Requirements:', ...req] : []),
    ...(open.length ? ['Open findings:', ...open.map(formatFindingLine)] : []),
    ...(r.not_checked.length ? [`Not checked: ${r.not_checked.join('; ')}`] : []),
  ].join('\n');
}

/**
 * A reviewer's brief, written by the runtime (spec §3.1): the builder's own assignment, the criteria, the focus and
 * what was submitted. The builder's report is withheld until the reviewer's initial assessment.
 */
export function reviewBrief(t: AgentRow, s: SubmissionRow, criteria: string[], focus?: string): string {
  const what = [
    `Submission ${s.seq} by the thread "${t.title ?? 'untitled'}" (${t.id}).`,
    s.commit
      ? `Your workspace is a git worktree on your own review branch at the submitted commit ${s.commit}. The builder's changes: git diff ${s.base ?? `${s.commit}~1`} ${s.commit}. Nothing you change there reaches the builder's branch.`
      : 'It has no commit: check the library artifacts below and whatever the assignment names (the project sources are readable).',
    ...(s.artifacts.length ? ['Library artifacts (immutable copies; read them with library_read or from the library folder):', ...s.artifacts.map((a) => `- ${a.path} (sha256 ${a.sha256.slice(0, 12)})`)] : []),
  ];
  return [
    `You are an independent reviewer. Decide whether the work submitted by the thread "${t.title ?? 'untitled'}" meets the acceptance criteria below. Form your own view from the work itself: the builder's report (its summary, claims and evidence) is withheld until you file your initial assessment.`,
    '',
    '## Acceptance criteria',
    ...criteria.map((c) => `- ${c}`),
    ...(focus ? ['', '## Focus', focus] : []),
    '',
    '## What was submitted',
    ...what,
    '',
    "## The builder's original assignment",
    t.brief ?? '(none)',
  ].join('\n');
}

/**
 * A thread's reviews for Desk's read_thread: its acceptance, latest submission, reviews and open findings; for a
 * reviewer, what it reviews. Undefined when there is nothing to say.
 */
export function reviewSummary(db: Db, t: AgentRow): string | undefined {
  if (t.reviews_submission_id) {
    const r = currentReviewOf(db, t.id);
    return r ? `Reviewing: submission ${getSubmission(db, r.submission_id)?.seq ?? '?'} of thread ${r.builder_id} (review ${r.id}, ${r.phase ?? 'not assessed yet'})` : undefined;
  }
  const sub = latestSubmission(db, t.id);
  if (!sub) return undefined;
  const reviewsOf = reviewsOfBuilder(db, t.id);
  const open = openFindings(db, t.id);
  return [
    `Acceptance: ${t.acceptance.replace(/_/g, ' ')}`,
    `Latest ${formatSubmissionLine(sub)}`,
    ...reviewsOf.map((r) => `- review ${r.id} by thread ${r.reviewer_id} of submission ${getSubmission(db, r.submission_id)?.seq ?? '?'}: ${r.phase === 'final' ? (r.verdict ?? '').replace(/_/g, ' ') : 'in progress'}`),
    ...(open.length ? ['Open findings:', ...open.map(formatFindingLine)] : []),
  ].join('\n');
}

/** A thread's reviews as the apps show them (GET /threads/:id/review). */
export interface ThreadReview {
  acceptance: Acceptance;
  accepted_submission_id: string | null;
  /** Oldest first; the last one is current. */
  submissions: SubmissionRow[];
  /** Reviews of this thread's submissions, oldest first. */
  reviews: ReviewRow[];
  /** Findings about this thread's work, oldest first. */
  findings: FindingRow[];
  /** For a reviewer thread: the review it is doing (or last did), with the reviewed submission's number. */
  reviewing: (ReviewRow & { submission_seq: number | null }) | null;
}

export function threadReview(db: Db, t: AgentRow): ThreadReview {
  const reviewing = t.reviews_submission_id ? currentReviewOf(db, t.id) : undefined;
  return {
    acceptance: t.acceptance,
    accepted_submission_id: t.accepted_submission_id,
    submissions: listSubmissions(db, t.id),
    reviews: reviewsOfBuilder(db, t.id),
    findings: findingsOfBuilder(db, t.id),
    reviewing: reviewing ? { ...reviewing, submission_seq: getSubmission(db, reviewing.submission_id)?.seq ?? null } : null,
  };
}
