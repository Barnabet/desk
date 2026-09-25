import { z } from 'zod';
import { SkillName, SkillScope } from './domain';

/** The skill catalog: reviewed Agent Skills pinned to a commit and a content digest (spec: 2026-09-24-skill-catalog-design). */

export const CatalogCategory = z.enum(['research', 'documents', 'writing', 'planning', 'code']);
export type CatalogCategory = z.infer<typeof CatalogCategory>;

const noDots = (p: string) => !p.split('/').some((s) => s === '.' || s === '..');

export const CatalogSource = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('github'),
    repo: z.string().regex(/^[\w.-]+\/[\w.-]+$/),
    /** Directory of the skill inside the repo; '' for the repo root. */
    path: z.string().regex(/^(|[\w.@-]+(\/[\w.@-]+)*)$/).refine(noDots, 'Paths cannot contain . or .. segments'),
    sha: z.string().regex(/^[0-9a-f]{40}$/),
    /**
     * For large repositories: the skill's files (relative to `path`), fetched one by one from raw.githubusercontent.com
     * at `sha` instead of downloading the whole archive. The digest covers them the same way.
     */
    files: z.array(z.string().regex(/^[\w.@ -]+(\/[\w.@ -]+)*$/).refine(noDots, 'Paths cannot contain . or .. segments')).max(200).optional(),
    /** In files mode: which of `files` are executable. */
    executable: z.array(z.string()).optional(),
    /** In files mode: the repository's licence file (repo-relative), kept with skills that have none of their own. */
    license_file: z.string().regex(/^[\w.-]+(\/[\w.-]+)*$/).refine(noDots).optional(),
  }),
  /** Shipped with Desk under catalog/skills (first-party skills). */
  z.object({ type: z.literal('builtin'), path: z.string().regex(/^[\w.-]+(\/[\w.-]+)*$/).refine(noDots, 'Paths cannot contain . or .. segments') }),
]);
export type CatalogSource = z.infer<typeof CatalogSource>;

export const NodeLockEntry = z.object({
  name: z.string().min(1),
  version: z.string().min(1),
  integrity: z.string().regex(/^sha(256|384|512)-[A-Za-z0-9+/=]+$/),
  /** Install location relative to the environment, e.g. node_modules/@resvg/resvg-js. */
  path: z.string().regex(/^node_modules\/(@[\w.-]+\/)?[\w.-]+(\/node_modules\/(@[\w.-]+\/)?[\w.-]+)*$/),
  /** Platform-specific packages (prebuilt binaries): installed only where they match, like npm's os/cpu fields. */
  os: z.array(z.string()).optional(),
  cpu: z.array(z.string()).optional(),
});
export type NodeLockEntry = z.infer<typeof NodeLockEntry>;

export const CatalogRuntime = z.object({
  python: z
    .object({
      version: z.string().regex(/^3\.\d+$/),
      /** Exact pins only; extras allowed (markitdown[all]==0.1.3). */
      packages: z.array(z.string().regex(/^[A-Za-z0-9._-]+(\[[A-Za-z0-9._,-]+\])?==[A-Za-z0-9._+!-]+$/)),
    })
    .optional(),
  node: z
    .object({
      lock: z.array(NodeLockEntry),
      /** Curation only (catalog:pin): a package-lock.json inside the skill, or `npm:<spec> …` resolved by npm at pin time. */
      lock_from: z.string().optional(),
    })
    .optional(),
  /**
   * What else Desk provisions, a closed set: `playwright-chromium` downloads Playwright's Chromium; `browser` uses the
   * installed Chrome, Edge or Chromium (`DESK_BROWSER`) and downloads Chromium only when there is none.
   */
  extras: z.array(z.enum(['playwright-chromium', 'browser'])).optional(),
}).superRefine((rt, ctx) => {
  // Both extras drive Playwright from the skill's Python, so an entry without it could only fail at install time.
  const playwright = rt.python?.packages.some((p) => /^playwright(\[[^\]]*\])?==/i.test(p));
  for (const extra of rt.extras ?? []) {
    if (!playwright) ctx.addIssue({ code: 'custom', path: ['extras'], message: `${extra} needs runtime.python with a playwright pin` });
  }
});
export type CatalogRuntime = z.infer<typeof CatalogRuntime>;

export const CatalogEntry = z.object({
  id: SkillName,
  title: z.string().min(1).max(80),
  category: CatalogCategory,
  summary: z.string().min(1).max(200),
  license: z.string().min(1),
  homepage: z.string().url(),
  source: CatalogSource,
  digest: z.string().regex(/^sha256:[0-9a-f]{64}$/),
  files: z.number().int().min(1),
  bytes: z.number().int().min(1),
  /** How many of the files are scripts (shown on the card; the review lists them). */
  scripts: z.number().int().min(0).default(0),
  runtime: CatalogRuntime.default({}),
  /** Command run by `catalog:check` inside the sandbox, from the skill directory. */
  smoke: z.array(z.string()).optional(),
  caveats: z.array(z.string()).default([]),
});
export type CatalogEntry = z.infer<typeof CatalogEntry>;

export const CatalogFile = z.object({
  version: z.literal(1),
  /** Curation date; also the `--exclude-newer` bound for Python packages. */
  updated: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  entries: z.array(CatalogEntry),
});
export type CatalogFile = z.infer<typeof CatalogFile>;

export const ReviewWarningKind = z.enum(['exec-block', 'pipe-to-shell', 'base64-blob', 'invisible-unicode', 'paste-site', 'memory-write']);
export type ReviewWarningKind = z.infer<typeof ReviewWarningKind>;

export const ReviewWarning = z.object({ file: z.string(), line: z.number().int().min(1), kind: ReviewWarningKind, excerpt: z.string() });
export type ReviewWarning = z.infer<typeof ReviewWarning>;

export const CatalogReview = z.object({
  entry: CatalogEntry,
  /** Browsable URL of the pinned source (the commit's tree), or null for builtin skills. */
  source_url: z.string().nullable(),
  files: z.array(z.object({ path: z.string(), size: z.number().int(), script: z.boolean() })),
  skill_md: z.string(),
  license_text: z.string().nullable(),
  warnings: z.array(ReviewWarning),
});
export type CatalogReview = z.infer<typeof CatalogReview>;

export const RuntimeState = z.enum(['none', 'preparing', 'ready', 'failed']);
export type RuntimeState = z.infer<typeof RuntimeState>;

export const CatalogInstallState = z.enum(['not_installed', 'installed', 'update_available', 'modified', 'name_taken']);
export type CatalogInstallState = z.infer<typeof CatalogInstallState>;

export const CatalogInstall = z.object({
  scope: SkillScope,
  project_id: z.string().nullable(),
  state: CatalogInstallState,
  /** The catalog commit of the installed version (null when not from the catalog). */
  sha: z.string().nullable(),
  runtime: RuntimeState,
  runtime_reason: z.string().nullable(),
});
export type CatalogInstall = z.infer<typeof CatalogInstall>;

/** A catalog entry with where it is installed; scopes with nothing of that name are omitted. */
export const CatalogItem = CatalogEntry.extend({ installs: z.array(CatalogInstall) });
export type CatalogItem = z.infer<typeof CatalogItem>;

export const CatalogInstallRequest = z.object({
  scope: SkillScope.default('global'),
  project_id: z.string().min(1).optional(),
  /** Required to replace a catalog skill that was edited locally. */
  replace_modified: z.boolean().default(false),
});
export type CatalogInstallRequest = z.input<typeof CatalogInstallRequest>;

export const CatalogInstallResult = z.object({
  skill: z.object({ name: SkillName, scope: SkillScope, version: z.number().int(), project_id: z.string().nullable() }),
  state: CatalogInstallState,
  runtime: RuntimeState,
});
export type CatalogInstallResult = z.infer<typeof CatalogInstallResult>;

export const RuntimesReport = z.object({
  bytes: z.number().int(),
  envs: z.array(
    z.object({ scope: SkillScope, project_id: z.string().nullable(), name: z.string(), bytes: z.number().int(), orphan: z.boolean() }),
  ),
});
export type RuntimesReport = z.infer<typeof RuntimesReport>;
