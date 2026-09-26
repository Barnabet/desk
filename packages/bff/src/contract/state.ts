import type { SystemState } from '@desk/client';
import type { AttentionItem, HealthResponse, ProjectSummary } from '@desk/protocol';

export type ConnectionStatus = 'starting' | 'offline' | 'connecting' | 'live' | 'reconnecting' | 'mismatch';

/** What every window and the tray share: pushed by the broker on `desk:global`. */
export type GlobalState = {
  connection: { status: ConnectionStatus; detail?: string };
  health: HealthResponse | null;
  overview: ProjectSummary[];
  attention: AttentionItem[];
  system: SystemState;
  /** Skill runtimes being set up (keyed like the Skills route: global:<name> or project:<id>:<name>), and a counter bumped whenever one changes state. */
  runtimes: { progress: Record<string, RuntimeProgress>; seq: number };
};

export type RuntimeProgress = { step: string; done?: number; total?: number };

/** The key the Skills screen uses for a skill. */
export const runtimeKey = (scope: 'global' | 'project' | 'builtin', projectId: string | null | undefined, name: string) =>
  scope === 'project' ? `project:${projectId}:${name}` : `${scope}:${name}`;

export const initialGlobalState = (): GlobalState => ({
  connection: { status: 'starting' },
  health: null,
  overview: [],
  attention: [],
  system: { proxy: 'unknown', notices: [], lastSeq: 0 },
  runtimes: { progress: {}, seq: 0 },
});
