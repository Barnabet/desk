import { createHash } from 'node:crypto';
import { mkdir } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { writeAgentFile } from '../tools/agent-files';
import { resolveForTool } from '../tools/paths';
import type { ToolContext } from '../tools/types';

const MAX_NAME = 100;

/**
 * A file name for a page: `<host-and-path slug>-<8 hex of the full URL><ext>`, lowercase ASCII and at most 100
 * characters. The same URL always gets the same name, so saving it again replaces the earlier copy.
 */
export function pageFileName(url: URL, ext = '.md'): string {
  const hash = createHash('sha256').update(url.href).digest('hex').slice(0, 8);
  const slug = `${url.hostname}${url.pathname}`
    .normalize('NFKD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, MAX_NAME - ext.length - hash.length - 1)
    .replace(/-+$/, '');
  return `${slug ? `${slug}-` : ''}${hash}${ext}`;
}

/**
 * Writes a file into the agent's own workspace (`relPath` under it) the way file tools do: resolved inside the
 * agent's write roots and checked by the guard before its folder is made, written through `writeAgentFile`.
 * Returns the real path.
 */
export async function saveAgentFile(ctx: ToolContext, relPath: string, data: string | Uint8Array): Promise<string> {
  const target = await resolveForTool(join(ctx.workspace, relPath), 'write', ctx);
  await mkdir(dirname(target), { recursive: true });
  await writeAgentFile(target, data, ctx.sandbox.guard);
  return target;
}
