const SHELL = new Set(['bash', 'bash_background', 'bash_readonly']);

/** A shell command reads as `$ command`; anything else as pretty JSON. */
export function describeArgs(tool: string, args: string): { command: string | null; pretty: string } {
  try {
    const v = JSON.parse(args) as Record<string, unknown>;
    const pretty = JSON.stringify(v, null, 2);
    const command = v['command'];
    if (SHELL.has(tool) && typeof command === 'string') return { command, pretty };
    const script = v['script'];
    if (tool === 'skill_run' && typeof script === 'string') {
      const rest = v['args'];
      return { command: [v['skill'], script, ...(Array.isArray(rest) ? rest : [])].filter(Boolean).join(' '), pretty };
    }
    return { command: null, pretty };
  } catch {
    return { command: null, pretty: args };
  }
}
