import { z } from 'zod';
import { MemoryKind } from '@desk/protocol';
import { formatMemoryLine, searchMemory, threadTitles } from '../memory/memory';
import { defineTool } from './types';

export const memorySearchTool = defineTool({
  name: 'memory_search',
  description: 'Search the shared project memory (facts, decisions, preferences, contacts, notes) by keywords.',
  input: z.object({ query: z.string().min(1) }),
  async execute({ query }, ctx) {
    const hits = searchMemory(ctx.services.store.db, ctx.projectId, query);
    const titles = threadTitles(ctx.services.store.db, ctx.projectId);
    return hits.length ? hits.map((m) => formatMemoryLine(m, titles)).join('\n') : 'No matching memory';
  },
});

export const memoryWriteTool = defineTool({
  name: 'memory_write',
  description:
    'Record a durable project fact, decision, preference, contact or note in shared memory. If it replaces an older entry (e.g. a changed date), pass that entry id as `supersedes` instead of contradicting it.',
  input: z.object({ kind: MemoryKind, content: z.string().min(1), supersedes: z.string().optional() }),
  async execute({ kind, content, supersedes }, ctx) {
    const id = ctx.services.writeMemory(ctx.projectId, { kind, content, ...(supersedes ? { supersedes } : {}) }, `agent:${ctx.agentId}`);
    return `Saved memory ${id}${supersedes ? ` (supersedes ${supersedes})` : ''}`;
  },
});

export const memoryTools = [memorySearchTool, memoryWriteTool];
