/**
 * What a paired phone may do (docs/web.md, "From your phone"): read what the app shows, talk to Desk and threads,
 * answer questions and approvals one at a time, upload to the Library, and run or answer automations. Everything else,
 * such as policy, sources, models, skills, automation switches and grants, deskd itself and browsing the Mac's folders,
 * stays on the Mac. An allow list, so an operation added later is refused on phones until it is listed here.
 */
export const PHONE_OPS: ReadonlySet<string> = new Set([
  'usage',
  'projects.list',
  'projects.get',
  'projects.send',
  'projects.chat',
  'projects.plan',
  'projects.usage',
  'projects.events',
  'threads.list',
  'threads.get',
  'threads.transcript',
  'threads.send',
  'threads.stop',
  'threads.archive',
  'threads.diff',
  'threads.review',
  'threads.files',
  'threads.file',
  'services.logs',
  'services.stop',
  'approvals.list',
  'approvals.resolve',
  'attention.list',
  'attention.dismiss',
  'automations.list',
  'automations.get',
  'automations.versions',
  'automations.version',
  'automations.runs',
  'automations.getRun',
  'automations.run',
  'automations.cancelRun',
  'automations.answer',
  'automations.stopStep',
  'automations.log',
  'automations.files',
  'automations.file',
  'memory.list',
  'memory.add',
  'memory.correct',
  'memory.remove',
  'attachments.get',
  'library.list',
  'library.upload',
  'library.file',
  'skills.list',
  'skills.get',
  'skills.file',
  'skills.history',
  'skills.version',
  'skills.versionFile',
  'builtins.list',
  'builtins.get',
  'builtins.file',
  'catalog.list',
  'catalog.file',
  'models.list',
  'config.get',
  'config.endpoint',
  'broker.snapshot',
  'broker.watch',
  'broker.unwatch',
  'system.runtimes',
  'daemon.status',
  'app.info',
  'app.settings',
]);

export const PHONE_REFUSED = 'Only on your Mac: a paired phone cannot do this.';

/** Why a paired phone may not run this operation with this input, or null when it may. */
export function phoneRefusal(op: string, input: unknown): string | null {
  if (!PHONE_OPS.has(op)) return PHONE_REFUSED;
  // `remember` adds a standing grant to an automation: phones approve one action at a time.
  if (op === 'approvals.resolve' && typeof input === 'object' && input !== null && (input as { remember?: unknown }).remember === true) {
    return 'Only on your Mac: a paired phone approves one action at a time, without remembering it.';
  }
  return null;
}
