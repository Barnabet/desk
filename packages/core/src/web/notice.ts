import { lastProjectNotice } from '../state/queries';
import type { ToolContext } from '../tools/types';
import { WebApiError } from './errors';

const NOTICE_EVERY_MS = 60 * 60_000;
const NAMES = { serper: 'Serper', firecrawl: 'Firecrawl' } as const;

/**
 * Tells the user once an hour per project when a web key is refused or its account is empty (401, 402, 403): the tools
 * fall back to keyless engines, so without this nobody would notice. Other failures say nothing here.
 */
export function reportKeyProblem(ctx: ToolContext, err: unknown, now: number = Date.now()): void {
  if (!(err instanceof WebApiError) || !err.keyProblem) return;
  const code = `web_key_${err.provider}`;
  const last = lastProjectNotice(ctx.services.store.db, ctx.projectId, code);
  if (last && now - Date.parse(last.ts) < NOTICE_EVERY_MS) return;
  const what = err.status === 402 ? 'is out of credits' : 'refused the key';
  ctx.services.store.append({
    project_id: ctx.projectId,
    agent_id: null,
    type: 'system.notice',
    payload: {
      level: 'warning',
      code,
      message: `${NAMES[err.provider]} ${what} (HTTP ${err.status}), so agents are using the keyless fallback. Check the key in System → Web search and fetch.`,
    },
  });
}
