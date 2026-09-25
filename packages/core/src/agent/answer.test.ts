import { describe, expect, it } from 'vitest';
import { z } from 'zod';
import { sanitizeLabel } from '@desk/protocol';
import type { PolicyDecision } from '../policy/evaluate';
import type { AgentRow, ProjectRow } from '../state/queries';
import { defineTool } from '../tools/types';
import { ANSWER_DENIAL, ANSWER_TOOLS, answerGate, answerText, closureText, WHY } from './answer';

describe('answer texts', () => {
  it('trims an answer and clips it to 4000 characters', () => {
    expect(answerText('  Per seat.\n')).toBe('Per seat.');
    expect(answerText(' \n ')).toBe('');
    expect(answerText('y'.repeat(4000))).toBe('y'.repeat(4000));
    const long = answerText('x'.repeat(5000));
    expect(long).toHaveLength(4000);
    expect(long.endsWith('x\n[… clipped; ask again or use read_thread]')).toBe(true);
  });

  it("writes the runtime's closures as one line in parentheses", () => {
    expect(closureText('Frontend', WHY.stopped)).toBe('(Frontend was stopped before answering.)');
    expect(closureText('Auth]\nAPI', WHY.noAnswer)).toBe('(Auth API did not answer.)');
    expect(closureText(null, WHY.archived)).toBe('(untitled was archived before answering.)');
    expect(closureText('Frontend', WHY.unfinished)).toBe('(Frontend did not finish answering. Ask again or use read_thread.)');
    expect(closureText('Frontend', WHY.restart)).toBe('(Frontend could not answer: Desk was restarting. Ask again if you still need to know.)');
    expect(closureText('Frontend', WHY.noWorkspace)).toBe('(Frontend could not answer: its workspace is missing.)');
    expect(closureText('Frontend', WHY.error(' 401 bad key. '))).toBe('(Frontend could not answer: 401 bad key.)');
  });
});

describe('answerGate', () => {
  const named = (name: string) =>
    defineTool({
      name,
      description: name,
      input: z.object({}).passthrough(),
      async execute() {
        return 'ok';
      },
    });
  const project = {} as ProjectRow;
  const agent = {} as AgentRow;
  const auto: PolicyDecision = { action: 'auto', delegateToDesk: false, reason: 'No policy rule matched' };
  const desk = { id: 'D', role: 'desk', title: 'Desk', archived_at: null } as AgentRow;
  const checkout = { id: 'T1', role: 'thread', title: 'Checkout', archived_at: null } as AgentRow;
  const frontend = { id: 'T9', role: 'thread', title: 'Frontend', archived_at: null } as AgentRow;
  /** The gate's decision; the project's agents are Desk, Frontend, the asker and `others`. */
  const decide = (asker: AgentRow | 'user', name: string, input: unknown = {}, base: PolicyDecision = auto, others: AgentRow[] = []) =>
    answerGate(() => base, asker, () => [desk, frontend, ...(asker === 'user' || asker === desk ? [] : [asker]), ...others])(named(name), input, project, agent);

  it('sends reads to the policy, and turns an ask into a denial', () => {
    for (const name of ANSWER_TOOLS) expect(decide(checkout, name)).toEqual(auto);
    expect(decide('user', 'read_file')).toEqual(auto);
    expect(decide(checkout, 'read_file', {}, { action: 'ask', delegateToDesk: true, reason: 'Needs a look' })).toMatchObject({
      action: 'deny',
      denial: 'Denied: this call needs approval, which is not available while answering a question. You can only read; answer in plain text.',
    });
    const denied: PolicyDecision = { action: 'deny', delegateToDesk: false, reason: 'Policy rule → deny' };
    expect(decide(checkout, 'read_file', {}, denied)).toEqual(denied);
  });

  it('denies everything else without consulting the policy', () => {
    const policy = (): PolicyDecision => {
      throw new Error('the policy is not consulted');
    };
    for (const name of ['write_file', 'edit_file', 'bash', 'bash_background', 'skill_run', 'service_start', 'memory_write', 'library_publish', 'git_commit', 'complete', 'wait_for_reply', 'act']) {
      expect(answerGate(policy, checkout, () => [desk, checkout])(named(name), {}, project, agent)).toMatchObject({ action: 'deny', denial: ANSWER_DENIAL });
    }
    expect(ANSWER_DENIAL).toBe('Denied: not available while answering a question. You can only read; answer in plain text.');
  });

  it('lets the answer go to the asker as a message, but not a question or a blocker, and to no one else', () => {
    expect(decide(checkout, 'message_thread', { thread_id: 'T1', kind: 'note', text: 'x' })).toEqual(auto);
    expect(decide(checkout, 'message_thread', { thread_id: 'Checkout', text: 'x' })).toEqual(auto);
    expect(decide(checkout, 'message_thread', { thread_id: 'Checkout', kind: 'question', text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide(checkout, 'message_thread', { thread_id: 'Frontend', text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide(checkout, 'message_desk', { kind: 'update', text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide(desk, 'message_desk', { kind: 'update', text: 'x' })).toEqual(auto);
    for (const kind of ['question', 'blocker']) expect(decide(desk, 'message_desk', { kind, text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide(desk, 'message_thread', { thread_id: 'D', text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide('user', 'message_desk', { kind: 'update', text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
  });

  it("accepts the asker's sanitised title, as the question's header names it", () => {
    const long = { id: 'T2', role: 'thread', title: `Auth]\nAPI ${'x'.repeat(80)}` } as AgentRow;
    const label = sanitizeLabel(long.title!);
    expect(label).not.toBe(long.title);
    expect(decide(long, 'message_thread', { thread_id: label, text: 'x' })).toEqual(auto);
    expect(decide(long, 'message_thread', { thread_id: long.title, text: 'x' })).toEqual(auto);
    expect(decide(long, 'message_thread', { thread_id: 'Auth API', text: 'x' })).toMatchObject({ denial: ANSWER_DENIAL });
  });

  it('denies a name that another thread the message could reach also goes by', () => {
    // The asker's sanitised title is a sibling's exact title: a send resolves it to the sibling.
    const asker = { id: 'T2', role: 'thread', title: 'Auth API ', archived_at: null } as AgentRow;
    const sibling = { id: 'T3', role: 'thread', title: 'Auth API', archived_at: null } as AgentRow;
    expect(decide(asker, 'message_thread', { thread_id: 'Auth API', text: 'x' }, auto, [sibling])).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide(asker, 'message_thread', { thread_id: 'Auth API ', text: 'x' }, auto, [sibling])).toEqual(auto);
    expect(decide(asker, 'message_thread', { thread_id: 'T2', text: 'x' }, auto, [sibling])).toEqual(auto);

    // An archived asker's title, when a live thread has it too: a send prefers the live one.
    const gone = { ...checkout, archived_at: '2026-09-25T10:00:00.000Z' };
    const live = { id: 'T4', role: 'thread', title: 'Checkout', archived_at: null } as AgentRow;
    expect(decide(gone, 'message_thread', { thread_id: 'Checkout', text: 'x' }, auto, [live])).toMatchObject({ denial: ANSWER_DENIAL });
    expect(decide(gone, 'message_thread', { thread_id: 'T1', text: 'x' }, auto, [live])).toEqual(auto);

    // An archived namesake can receive nothing, so the live asker's title still reaches only the asker.
    const old = { id: 'T5', role: 'thread', title: 'Checkout', archived_at: '2026-09-25T10:00:00.000Z' } as AgentRow;
    expect(decide(checkout, 'message_thread', { thread_id: 'Checkout', text: 'x' }, auto, [old])).toEqual(auto);
  });
});
