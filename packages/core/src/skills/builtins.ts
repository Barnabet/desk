import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { BuiltinsFile, type BuiltinEntry, type CatalogRuntime } from '@desk/protocol';
import { eq } from 'drizzle-orm';
import { readTree, treeDigest } from '../catalog/digest';
import type { Db } from '../db/open';
import { builtinSkillSettings } from '../db/schema';
import manifestData from './builtins.json' with { type: 'json' };

/** Whether the user left a built-in skill on (the default). */
export function builtinEnabled(db: Db, name: string): boolean {
  const row = db.select({ enabled: builtinSkillSettings.enabled }).from(builtinSkillSettings).where(eq(builtinSkillSettings.name, name)).get();
  return row?.enabled ?? true;
}

export function loadBuiltinsManifest(data: unknown = manifestData): BuiltinsFile {
  return BuiltinsFile.parse(data);
}

/** What a runtime is built from; `digest` identifies the build, so a different one means rebuild. */
export type RuntimeSpec = { digest: string; runtime: CatalogRuntime };

/** A built-in environment's key: what gets installed. A change to scripts alone keeps the environment. */
export function runtimeKey(rt: CatalogRuntime): string {
  const spec = JSON.stringify({ python: rt.python ?? null, node: rt.node?.lock ?? null, extras: rt.extras ?? [] });
  return `sha256:${createHash('sha256').update(spec).digest('hex')}`;
}

/**
 * Desk's own skills, shipped with the app in `root` (the bundled catalog/skills; the repo's in development) and
 * listed in builtins.json. Read-only; `verify()` checks each tree against its digest once, at start.
 */
export class BuiltinSkills {
  readonly root: string;
  private readonly manifest: BuiltinsFile;
  private readonly byName: Map<string, BuiltinEntry>;
  private readonly damage = new Map<string, string>();

  constructor(private readonly o: { root: string; manifest?: BuiltinsFile; enabled?: (name: string) => boolean }) {
    this.root = o.root;
    this.manifest = o.manifest ?? loadBuiltinsManifest();
    this.byName = new Map(this.manifest.skills.map((s) => [s.name, s]));
  }

  get updated(): string {
    return this.manifest.updated;
  }

  names(): string[] {
    return [...this.byName.keys()].sort();
  }

  entry(name: string): BuiltinEntry | undefined {
    return this.byName.get(name);
  }

  dir(name: string): string {
    return join(this.root, name);
  }

  verify(): void {
    this.damage.clear();
    for (const e of this.manifest.skills) {
      try {
        if (treeDigest(readTree(this.dir(e.name))) !== e.digest) {
          this.damage.set(e.name, `${e.name} does not match the copy Desk shipped with. Reinstall Desk (in development: pnpm builtins:pin).`);
        }
      } catch (err) {
        this.damage.set(e.name, `${e.name} cannot be read: ${(err as Error).message}`);
      }
    }
  }

  broken(name: string): string | null {
    return this.damage.get(name) ?? null;
  }

  enabled(name: string): boolean {
    return this.o.enabled?.(name) ?? true;
  }

  /** In the manifest, intact and switched on: what agents may see and use. */
  usable(name: string): boolean {
    return this.byName.has(name) && !this.damage.has(name) && this.enabled(name);
  }

  runtimeSpec(name: string): RuntimeSpec {
    const e = this.byName.get(name);
    if (!e) throw new Error(`Unknown built-in skill: ${name}`);
    return { digest: runtimeKey(e.runtime), runtime: e.runtime };
  }
}
