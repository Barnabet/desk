// packages/core/src/tools/step.ts
import { z } from 'zod';
import { defineTool } from './types';

/** A step agent's `complete`: records the step's result (checked against its output keys and routes) and ends. */
export const stepCompleteTool = defineTool({
  name: 'complete',
  description:
    "Finish this step. Call exactly once, when the step is done and verified. `summary`: what you did and found, for the run's report and later steps. `outputs`: values for this step's output keys (listed in your instructions; only those keys). `route`: one of this step's routes, if it has any and one applies.",
  input: z.object({
    summary: z.string().min(1).max(2000),
    outputs: z.record(z.string(), z.unknown()).optional(),
    route: z.string().optional(),
  }),
  async execute({ summary, outputs = {}, route }, ctx) {
    ctx.services.recordStepResult(ctx.agentId, { status: 'succeeded', summary, outputs: outputs as never, route: route ?? null });
    return { content: 'Step result recorded.', yield: { status: 'done', reason: summary.split('\n')[0]!.slice(0, 200) } };
  },
});

/** A step agent's way out when it cannot do the step correctly: nobody is there to ask. */
export const failStepTool = defineTool({
  name: 'fail_step',
  description:
    "End this step as failed, with the reason. Use it instead of guessing when you cannot do the step correctly: a missing or unusable input, a blocked tool, or a consequential choice the brief does not settle. The automation's error handling decides what happens next.",
  input: z.object({ reason: z.string().min(1).max(2000) }),
  async execute({ reason }, ctx) {
    ctx.services.recordStepResult(ctx.agentId, { status: 'failed', error: reason });
    return { content: 'Step failure recorded.', yield: { status: 'failed', reason: reason.split('\n')[0]!.slice(0, 200) } };
  },
});

export const stepTools = [stepCompleteTool, failStepTool];
