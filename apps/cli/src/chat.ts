import { createInterface } from 'node:readline';
import type { DeskClient } from './client';
import type { CliIO } from './commands';
import { createRenderer } from './format';

const HELP = `Commands:
  /threads                 list threads
  /approve <id> [note]     approve a pending action
  /deny <id> [note]        deny a pending action
  /tell <thread> <text>    message a thread directly
  /stop <thread>           stop a thread
  /quit                    leave (Desk keeps working)
Anything else is sent to Desk.`;

/** Interactive session: live project stream + line input to Desk. */
export async function runChat(client: DeskClient, project: { id: string; name: string }, io: CliIO): Promise<void> {
  const overview = await client.get(`/projects/${project.id}`);
  const render = createRenderer(io.out, { deskId: overview.desk.id });
  const live = overview.threads.filter((t: any) => !t.archived_at).length;
  io.out(`Desk for "${project.name}" — ${live} thread(s), ${overview.approvals.length} pending approval(s). /help for commands.\n`);
  const close = await client.stream(project.id, overview.last_seq, render);
  const rl = createInterface({ input: process.stdin, terminal: false });
  try {
    for await (const raw of rl) {
      const line = raw.trim();
      if (!line) continue;
      try {
        if (line === '/quit' || line === '/exit') break;
        if (line === '/help') io.out(`${HELP}\n`);
        else if (line === '/threads') {
          const threads = await client.get<any[]>(`/projects/${project.id}/threads`);
          io.out(`${threads.map((t) => `  ${t.id} "${t.title}" [${t.status}]`).join('\n') || '  (no threads)'}\n`);
        } else if (/^\/(approve|deny) /.test(line)) {
          const [cmd, id, ...note] = line.slice(1).split(/\s+/);
          await client.post(`/approvals/${id}/resolve`, { decision: cmd === 'approve' ? 'approved' : 'denied', ...(note.length ? { note: note.join(' ') } : {}) });
        } else if (line.startsWith('/tell ')) {
          const [, id, ...words] = line.split(/\s+/);
          await client.post(`/threads/${id}/messages`, { text: words.join(' ') });
        } else if (line.startsWith('/stop ')) {
          await client.post(`/threads/${line.split(/\s+/)[1]}/stop`);
        } else if (line.startsWith('/')) io.out(`Unknown command. ${HELP}\n`);
        else await client.post(`/projects/${project.id}/messages`, { text: line });
      } catch (err) {
        io.err(`error: ${err instanceof Error ? err.message : String(err)}\n`);
      }
    }
  } finally {
    rl.close();
    close();
  }
}
