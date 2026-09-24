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
};

export const initialGlobalState = (): GlobalState => ({
  connection: { status: 'starting' },
  health: null,
  overview: [],
  attention: [],
  system: { proxy: 'unknown', notices: [], lastSeq: 0 },
});
