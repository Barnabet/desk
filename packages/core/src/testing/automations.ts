import type { FakeReply, Script } from '@desk/fake-model';
import type { AutomationDefinition } from '@desk/protocol';
import { getDeskAgent, type AgentRow } from '../state/queries';
import type { Runtime, RuntimeOptions } from '../runtime/runtime';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from './harness';

/** A clock tests move by hand; pass `clock.now` as RuntimeOptions.now. */
export class FakeClock {
  constructor(public t = Date.parse('2026-09-28T06:00:00.000Z')) {}
  now = (): Date => new Date(this.t);
  advance(ms: number): void {
    this.t += ms;
  }
  set(iso: string): void {
    this.t = Date.parse(iso);
  }
}

/** A runtime with a project whose Desk and threads use the fake model. */
export async function automationHarness(
  opts: { script?: Script | FakeReply[]; clock?: FakeClock; extra?: Partial<RuntimeOptions> } = {},
): Promise<{ h: Harness; rt: Runtime; projectId: string; desk: AgentRow; clock: FakeClock }> {
  const clock = opts.clock ?? new FakeClock();
  // Events carry the fake time too: schedule cursors come from event times (spec §2.3) and are compared with `now`.
  const h = await createHarness({ script: opts.script ?? [], now: clock.now });
  const rt = newRuntime(h, { now: clock.now, ...opts.extra });
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
  return { h, rt, projectId, desk: getDeskAgent(h.store.db, projectId)!, clock };
}

/** A one-step automation: wait one minute. */
export const waitDef = (over: Partial<Record<keyof AutomationDefinition, unknown>> = {}) => ({
  title: 'Wait a bit',
  steps: [{ id: 'w', title: 'Wait', kind: 'wait', minutes: 1 }],
  ...over,
});

/** Ask me, then tell Desk on either answer through routes. */
export const askDef = (over: Partial<Record<keyof AutomationDefinition, unknown>> = {}) => ({
  title: 'Ask then tell',
  steps: [
    { id: 'ask', title: 'Go ahead?', kind: 'ask', question: 'Publish?' },
    { id: 'yes', title: 'Tell yes', kind: 'tell_desk', text: 'Approved: {{steps.ask.outputs.note}}' },
    { id: 'no', title: 'Tell no', kind: 'tell_desk', text: 'Rejected' },
  ],
  edges: [
    { from: 'ask', to: 'yes' },
    { from: 'ask', to: 'no', route: 'rejected' },
  ],
  ...over,
});
