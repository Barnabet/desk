import { z } from 'zod';
import { Acceptance, ReasoningEffort, RequirementCheck, ReviewVerdict } from '@desk/protocol';
import { newId } from '../ids';
import { builderReport, currentReviewOf, findingsOfBuilder, getFinding, getReview, getSubmission, renderReview, type ReviewRow } from '../reviews/reviews';
import { getAgent } from '../state/queries';
import { requireThread } from './desk';
import { defineTool, type ToolContext } from './types';

/** The calling reviewer's current review (spec §3.3). */
function myReview(ctx: ToolContext): ReviewRow {
  const r = currentReviewOf(ctx.services.store.db, ctx.agentId);
  if (!r) throw new Error('You have no review assigned.');
  return r;
}

export const raiseFindingTool = defineTool({
  name: 'raise_finding',
  description:
    'Record one material problem with the submission you review: what is wrong, whether it blocks acceptance, and how to see it (a command you ran, a file and line, or exact steps). Raise only problems you reproduced yourself.',
  input: z.object({
    title: z.string().min(1).max(200),
    detail: z.string().min(1).describe('What is wrong and why it matters'),
    blocking: z.boolean().describe('True when the work must not be accepted until this is fixed'),
    reproducer: z.string().min(1).describe('How to see it: a command and its output, a file and line, or steps'),
  }),
  async execute({ title, detail, blocking, reproducer }, ctx) {
    const r = myReview(ctx);
    if (r.phase === 'final') throw new Error('Your review is filed; wait for a re-review to raise more findings.');
    const id = `f_${newId()}`;
    ctx.services.store.append({
      project_id: ctx.projectId,
      agent_id: ctx.agentId,
      type: 'finding.raised',
      payload: { finding_id: id, review_id: r.id, submission_id: r.submission_id, title, detail, blocking, reproducer },
    });
    return `Finding ${id} recorded${blocking ? ' (blocking)' : ''}.`;
  },
});

export const resolveFindingTool = defineTool({
  name: 'resolve_finding',
  description:
    "Close an open finding about the work you review: `fixed` when the submission you are re-reviewing fixes it (check it yourself), `withdrawn` for one of your own findings you no longer stand by. Leave findings that still hold open.",
  input: z.object({ finding_id: z.string(), outcome: z.enum(['fixed', 'withdrawn']), note: z.string().min(1) }),
  async execute({ finding_id, outcome, note }, ctx) {
    const db = ctx.services.store.db;
    const r = myReview(ctx);
    if (r.phase === 'final') throw new Error('Your review is filed.');
    const f = getFinding(db, finding_id);
    if (!f || f.builder_id !== r.builder_id) throw new Error(`Unknown finding: ${finding_id}`);
    if (f.state !== 'open') throw new Error(`${finding_id} is already ${f.state}.`);
    if (outcome === 'fixed' && f.submission_id === r.submission_id) throw new Error(`${finding_id} was raised on the submission you are reviewing; it can only be fixed by a later one.`);
    if (outcome === 'withdrawn' && getReview(db, f.review_id)?.reviewer_id !== ctx.agentId) throw new Error('Only its reviewer withdraws a finding.');
    ctx.services.store.append({
      project_id: ctx.projectId,
      agent_id: ctx.agentId,
      type: 'finding.resolved',
      payload: { finding_id, outcome, ...(outcome === 'fixed' ? { submission_id: r.submission_id } : {}), reason: note, by: 'reviewer' },
    });
    return `${finding_id} marked ${outcome}.`;
  },
});

export const submitAssessmentTool = defineTool({
  name: 'submit_assessment',
  description:
    "File your assessment of the submission. The first call is your initial assessment, formed from the work alone: its result shows you the builder's own report (summary, claims, limitations, evidence). Reconcile the two, then call it again with your final review, which ends your work. `no_material_issues` is a good outcome when it is true. List in not_checked everything you did not verify.",
  input: z.object({
    verdict: ReviewVerdict,
    requirements: z.array(RequirementCheck).describe('Each acceptance criterion: met yes, no or unknown, with a short note'),
    not_checked: z.array(z.string().min(1)).default([]),
  }),
  async execute({ verdict, requirements, not_checked }, ctx) {
    const db = ctx.services.store.db;
    const r = myReview(ctx);
    if (r.phase === 'final') throw new Error('Your review is already filed.');
    const sub = getSubmission(db, r.submission_id);
    const builder = getAgent(db, r.builder_id);
    if (!sub || !builder) throw new Error('The submission you review no longer exists.');
    const own = findingsOfBuilder(db, r.builder_id).filter((f) => f.review_id === r.id);
    const openBlocking = findingsOfBuilder(db, r.builder_id).filter((f) => f.state === 'open' && f.blocking);
    if (verdict === 'no_material_issues' && openBlocking.length) {
      throw new Error(`${openBlocking.length} blocking finding(s) are still open (${openBlocking.map((f) => f.id).join(', ')}): resolve them or choose issues_found.`);
    }
    const base = { review_id: r.id, submission_id: r.submission_id, builder_id: r.builder_id, verdict, requirements, not_checked };
    if (!r.revealed) {
      ctx.services.store.append({ project_id: ctx.projectId, agent_id: ctx.agentId, type: 'review.assessed', payload: { ...base, phase: 'initial' } });
      return [
        'Initial assessment recorded.',
        '',
        builderReport(db, builder, sub),
        '',
        'Compare it with what you found. Check any claim you have not verified yet, raise findings for real problems (withdraw any you no longer stand by with resolve_finding), then call submit_assessment again with your final review.',
      ].join('\n');
    }
    const filed = { ...r, phase: 'final' as const, verdict, requirements, not_checked };
    const summary = renderReview(filed, own, builder.title ?? 'untitled');
    ctx.services.store.append([
      { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'review.assessed', payload: { ...base, phase: 'final' } },
      { project_id: ctx.projectId, agent_id: ctx.agentId, type: 'agent.result', payload: { summary, artifacts: [] } },
    ]);
    return { content: 'Review filed.', yield: { status: 'done', reason: summary.split('\n')[0]!.slice(0, 200) } };
  },
});

/** A reviewer thread's own tools, in place of complete (spec §3.3). */
export const reviewerTools = [raiseFindingTool, resolveFindingTool, submitAssessmentTool];

export const requestReviewTool = defineTool({
  name: 'request_review',
  description:
    "Have a finished thread's latest submission reviewed by an independent reviewer thread, against explicit acceptance criteria. The reviewer gets the thread's assignment, your criteria and the submitted commit or artifacts, but not the thread's own report until it has formed its view. Pass `reviewer` (an earlier reviewer of this thread) to re-review a new submission with the same critic. Use it for work whose correctness matters: code others build on, numbers people act on, anything the user will rely on.",
  input: z.object({
    thread_id: z.string(),
    criteria: z.array(z.string().min(1)).min(1).describe('What the work must satisfy to be accepted, one check each'),
    focus: z.string().optional().describe('A narrow question to look at closely'),
    model: z.string().optional().describe("Defaults to the project's review model, else a model of another family than the builder's"),
    reasoning_effort: ReasoningEffort.optional(),
    reviewer: z.string().optional().describe('An earlier reviewer of this thread, to re-review its new submission'),
  }),
  async execute({ thread_id, criteria, focus, model, reasoning_effort, reviewer }, ctx) {
    const t = requireThread(ctx, thread_id);
    const rv = reviewer ? requireThread(ctx, reviewer).id : undefined;
    const out = await ctx.services.requestReview({
      threadId: t.id,
      criteria,
      ...(focus ? { focus } : {}),
      ...(model ? { model } : {}),
      ...(reasoning_effort ? { reasoningEffort: reasoning_effort } : {}),
      ...(rv ? { reviewerId: rv } : {}),
      by: 'desk',
    });
    return out.reopened
      ? `Reopened reviewer ${out.reviewerId} on the new submission (review ${out.reviewId}).`
      : `Spawned reviewer ${out.reviewerId} (review ${out.reviewId}). Its review comes back as its result.`;
  },
});

export const acceptSubmissionTool = defineTool({
  name: 'accept_submission',
  description:
    "Record your decision about a thread's latest submission: `accepted`, `accepted_with_limitations` (say which, e.g. \"UI accepted; engine accuracy not established\") or `changes_requested` (its open findings and your note go back to it as a revision, review rounds apply). Open blocking findings must be waived with a reason before accepting. Acceptance is recorded against that exact version: a new submission resets it.",
  input: z.object({
    thread_id: z.string(),
    decision: Acceptance.extract(['accepted', 'accepted_with_limitations', 'changes_requested']),
    limitations: z.array(z.string().min(1)).optional(),
    waive: z.array(z.object({ finding_id: z.string(), reason: z.string().min(1) })).optional(),
    note: z.string().optional(),
  }),
  async execute({ thread_id, decision, limitations, waive, note }, ctx) {
    const t = requireThread(ctx, thread_id);
    const out = ctx.services.acceptSubmission({
      threadId: t.id,
      decision,
      ...(limitations ? { limitations } : {}),
      ...(waive ? { waive: waive.map((w) => ({ findingId: w.finding_id, reason: w.reason })) } : {}),
      ...(note ? { note } : {}),
      by: 'desk',
    });
    const waived = out.waived ? `, ${out.waived} finding(s) waived` : '';
    return decision === 'changes_requested'
      ? `Changes requested on submission ${out.submissionSeq}${waived}; the thread has been sent back.`
      : `Submission ${out.submissionSeq} ${decision.replace(/_/g, ' ')}${waived}.`;
  },
});

export const deskReviewTools = [requestReviewTool, acceptSubmissionTool];
