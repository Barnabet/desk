import type { NodeLockEntry } from '@desk/protocol';
import { parseSkillMd } from '../skills/store';

/** Helpers for `pnpm catalog:pin` and `catalog:check` (packages/core/scripts/catalog.ts). */

type LockPackage = { name?: string; version?: string; integrity?: string; dev?: boolean; link?: boolean; os?: string[]; cpu?: string[] };

/**
 * Flattens an npm lockfile (v2 or v3) into the catalog's lock entries: every installed package with its exact
 * version, integrity and location. Dev dependencies and workspace links are left out; os/cpu are kept so platform
 * packages install only where they match.
 */
export function flattenLock(lock: { lockfileVersion?: number; packages?: Record<string, LockPackage> }): NodeLockEntry[] {
  if (!lock.packages || (lock.lockfileVersion ?? 0) < 2) throw new Error('Expected an npm lockfile (lockfileVersion 2 or 3)');
  const out: NodeLockEntry[] = [];
  for (const [path, p] of Object.entries(lock.packages)) {
    if (!path || p.dev || p.link) continue;
    if (!path.startsWith('node_modules/')) throw new Error(`Unexpected lock location ${path}`);
    const name = p.name ?? path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length);
    if (!p.version || !p.integrity) throw new Error(`${path} has no version or integrity in the lock`);
    out.push({ name, version: p.version, integrity: p.integrity, path, ...(p.os ? { os: p.os } : {}), ...(p.cpu ? { cpu: p.cpu } : {}) });
  }
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

/** The SPDX id of a skill's licence, from its frontmatter or a recognisable licence text; null when unknown. */
export function detectLicense(text: string | null, skillMd: string): string | null {
  const fm = parseSkillMd(skillMd).frontmatter;
  if (typeof fm.license === 'string' && /^[A-Za-z0-9.+-]+$/.test(fm.license)) return fm.license;
  if (typeof fm.license === 'string' && /^MIT License$/i.test(fm.license.trim())) return 'MIT';
  if (!text) return null;
  if (/^\s*MIT License/i.test(text) || /Permission is hereby granted, free of charge/.test(text)) return 'MIT';
  if (/Apache License,?\s+Version 2\.0/i.test(text)) return 'Apache-2.0';
  if (/Attribution-ShareAlike 4\.0/i.test(text)) return 'CC-BY-SA-4.0';
  return null;
}

/** Quotes a word for /bin/sh when it needs it. */
export function shellWord(s: string): string {
  return /^[\w./=:@-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`;
}
