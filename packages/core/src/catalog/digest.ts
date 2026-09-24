import { createHash } from 'node:crypto';
import { lstatSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ValidationError } from '../errors';

const sha256 = (b: Buffer | string) => createHash('sha256').update(b).digest('hex');

/**
 * The catalog digest of a skill tree: sha256 over the sorted `path\0size\0sha256(content)\n` records.
 * Independent of file order, modes and timestamps; any change to a path or a byte changes it.
 */
export function treeDigest(files: Array<{ path: string; content: Buffer }>): string {
  const records = files
    .map((f) => `${f.path}\0${f.content.length}\0${sha256(f.content)}\n`)
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return `sha256:${sha256(records.join(''))}`;
}

/** Reads a directory tree into memory (regular files only; links are refused), with paths relative to `dir`. */
export function readTree(dir: string): Array<{ path: string; content: Buffer; mode: number }> {
  const out: Array<{ path: string; content: Buffer; mode: number }> = [];
  const walk = (rel: string) => {
    for (const name of readdirSync(join(dir, rel)).sort()) {
      const r = rel ? `${rel}/${name}` : name;
      const st = lstatSync(join(dir, r));
      if (st.isSymbolicLink()) throw new ValidationError(`${r} is a symbolic link, which skills cannot contain`);
      if (st.isDirectory()) walk(r);
      else if (st.isFile()) out.push({ path: r, content: readFileSync(join(dir, r)), mode: st.mode & 0o777 });
    }
  };
  walk('');
  return out;
}
