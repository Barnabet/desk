import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const root = fileURLToPath(new URL('./', import.meta.url));
const sources = (dir: string): string[] =>
  readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? sources(p) : /\.tsx?$/.test(f) ? [p] : [];
  });

describe('renderer styles', () => {
  it('come from @desk/ui-styles, loaded once by main.tsx; only the tray keeps a sheet of its own', () => {
    const imports = sources(root).flatMap((file) => [...readFileSync(file, 'utf8').matchAll(/^import '([^']+)';$/gm)].map((m) => `${file.slice(root.length)}: ${m[1]}`));
    expect(imports.sort()).toEqual(['main.tsx: @desk/ui-styles/index.css', 'tray/TrayPopover.tsx: ./tray.css']);
  });
});
