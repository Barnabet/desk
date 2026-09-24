import type { ReviewWarning, ReviewWarningKind } from '@desk/protocol';

/** Extensions treated as text for scanning; anything else is only listed. */
const TEXT = /\.(md|markdown|txt|py|js|mjs|cjs|ts|sh|bash|zsh|json|ya?ml|toml|html?|css|xml|csv|ini|cfg|r|rb|pl)$/i;
const SCRIPT_EXT = /\.(py|js|mjs|cjs|ts|sh|bash|zsh|rb|pl|r)$/i;

const RULES: Array<{ kind: ReviewWarningKind; re: RegExp }> = [
  // Claude Code runs these before the model sees the skill; Desk never does, but they deserve a look.
  { kind: 'exec-block', re: /!`[^`\n]+`|^\s*```!/ },
  { kind: 'pipe-to-shell', re: /\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b/i },
  { kind: 'base64-blob', re: /[A-Za-z0-9+/]{200,}={0,2}/ },
  { kind: 'invisible-unicode', re: /[​-‏‪-‮⁠-⁤⁦-⁩﻿\u{E0000}-\u{E007F}]/u },
  { kind: 'paste-site', re: /\b(pastebin\.com|paste\.ee|hastebin\.com|ghostbin\.|rentry\.co|transfer\.sh|0x0\.st)\b/i },
  { kind: 'memory-write', re: /\b(MEMORY\.md|CLAUDE\.md|AGENTS\.md|\.claude\/|memory_write|remember this forever)\b/ },
];

/** Whether a file is a script (by extension, a shebang, or an executable bit). */
export function isScript(path: string, mode: number, content?: Buffer): boolean {
  if (SCRIPT_EXT.test(path) || (mode & 0o111) !== 0) return true;
  return !!content && content.subarray(0, 2).toString('latin1') === '#!';
}

/**
 * Flags lines worth a human look before installing: embedded exec blocks, piping downloads into a shell, long base64,
 * invisible or bidirectional Unicode, paste sites and instructions touching agent memory files. Warnings, not verdicts.
 */
export function scanSkill(files: Array<{ path: string; content: Buffer }>): ReviewWarning[] {
  const out: ReviewWarning[] = [];
  for (const f of files) {
    if (!TEXT.test(f.path) && !(f.content.subarray(0, 2).toString('latin1') === '#!')) continue;
    const lines = f.content.toString('utf8').split('\n');
    lines.forEach((text, i) => {
      for (const rule of RULES) {
        const m = rule.re.exec(text);
        if (!m) continue;
        out.push({ file: f.path, line: i + 1, kind: rule.kind, excerpt: excerpt(text, m.index, m[0].length, rule.kind) });
      }
    });
  }
  return out;
}

function excerpt(line: string, at: number, len: number, kind: ReviewWarningKind): string {
  if (kind === 'invisible-unicode') {
    const cp = line.codePointAt(at)!;
    return `U+${cp.toString(16).toUpperCase().padStart(4, '0')} in: ${visible(line).slice(0, 120)}`;
  }
  if (kind === 'base64-blob') return `${line.slice(at, at + 40)}… (${len} characters)`;
  const start = Math.max(0, at - 40);
  return `${start > 0 ? '…' : ''}${line.slice(start, at + len + 40).trim()}${at + len + 40 < line.length ? '…' : ''}`;
}

const visible = (s: string) => s.replace(/[​-‏‪-‮⁠-⁤⁦-⁩﻿]/g, '⍰').replace(/[\u{E0000}-\u{E007F}]/gu, '⍰');
