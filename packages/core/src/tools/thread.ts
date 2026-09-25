import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { parseSkillMd } from '../skills/store';
import { readAgentFile } from './agent-files';
import { resolveInside } from './paths';
import { listArtifacts } from '../library/library';
import { getAgent } from '../state/queries';
import { defineTool, type ToolContext } from './types';

function parentOf(ctx: ToolContext): string {
  const parent = getAgent(ctx.services.store.db, ctx.agentId)?.parent_id;
  if (!parent) throw new Error('This agent has no Desk to report to');
  return parent;
}

export const completeTool = defineTool({
  name: 'complete',
  description:
    'Finish your assignment. Call exactly once, when the work is done or cannot be done, with an honest summary: what was done, what was not, and how it was verified. List library paths of artifacts you published, and skill draft directories you prepared.',
  input: z.object({
    summary: z.string().min(1),
    artifacts: z.array(z.string()).default([]),
    skill_drafts: z.array(z.string()).optional().describe('Skill draft directories in your workspace (each with a SKILL.md) for Desk to review and install'),
  }),
  async execute({ summary, artifacts, skill_drafts = [] }, ctx) {
    if (artifacts.length) {
      const known = new Set(listArtifacts(ctx.services.store.db, ctx.projectId).map((a) => a.path));
      const missing = artifacts.filter((a) => !known.has(a));
      if (missing.length) throw new Error(`Not in the library: ${missing.join(', ')}. Publish them with library_publish first.`);
    }
    const drafts = await Promise.all(
      skill_drafts.map(async (d) => {
        const dir = await resolveInside(d, [ctx.workspace], ctx.workspace);
        const md = join(dir, 'SKILL.md');
        if (!existsSync(md)) throw new Error(`${d} has no SKILL.md`);
        parseSkillMd((await readAgentFile(md, ctx.sandbox.guard)).toString('utf8'));
        return dir;
      }),
    );
    ctx.services.store.append({
      project_id: ctx.projectId,
      agent_id: ctx.agentId,
      type: 'agent.result',
      payload: { summary, artifacts, ...(drafts.length ? { skill_drafts: drafts } : {}) },
    });
    return { content: 'Completion recorded.', yield: { status: 'done', reason: summary.split('\n')[0]!.slice(0, 200) } };
  },
});

export const messageDeskTool = defineTool({
  name: 'message_desk',
  description:
    'Send a message to Desk, the project coordinator: a progress `update`, a `question`, or a `blocker`. After a question or blocker, call wait_for_reply unless you can keep working meanwhile.',
  input: z.object({ kind: z.enum(['update', 'question', 'blocker']), text: z.string().min(1) }),
  async execute({ kind, text }, ctx) {
    ctx.services.deliver(ctx.agentId, parentOf(ctx), kind, text);
    return 'Sent to Desk.';
  },
});

export const waitForReplyTool = defineTool({
  name: 'wait_for_reply',
  description: 'Pause until Desk or the user replies. Your next turn starts with their message.',
  input: z.object({}),
  async execute() {
    return { content: 'Waiting for a reply.', yield: { status: 'waiting', reason: 'Waiting for a reply' } };
  },
});

export const threadCoordinationTools = [messageDeskTool, waitForReplyTool, completeTool];
