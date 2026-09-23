import { z } from 'zod';
import { defineTool } from './types';

export const completeTool = defineTool({
  name: 'complete',
  description:
    'Finish your assignment. Call exactly once, when the work is done or cannot be done, with an honest summary: what was done, what was not, and how it was verified.',
  input: z.object({ summary: z.string().min(1) }),
  async execute({ summary }) {
    return { content: 'Completion recorded.', yield: { status: 'done', reason: summary } };
  },
});
