import type { AttentionKind } from '@desk/protocol';

/** Flight-strip end-cap codes (design C): clearance, query, document hand-off, holding, failed, ground stop (a paused project). */
export const STRIP_CODE: Record<AttentionKind, 'APR' | 'ASK' | 'DOC' | 'HLD' | 'FLD' | 'GND'> = {
  approval: 'APR',
  question: 'ASK',
  needs_you: 'DOC',
  stalled: 'HLD',
  failed: 'FLD',
  paused: 'GND',
};
