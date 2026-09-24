import type { CatalogInstall, CatalogItem, CatalogReview } from '@desk/protocol';

const SHA = 'a'.repeat(40);
const DIGEST = `sha256:${'b'.repeat(64)}`;

/** Catalog items for component tests: a Python research skill, a builtin document skill and an instructions-only one. */
export function catalogItems(installs: Partial<Record<string, CatalogInstall[]>> = {}): CatalogItem[] {
  return [
    {
      id: 'paper-lookup',
      title: 'Paper lookup',
      category: 'research',
      summary: 'Search free scholarly APIs for papers.',
      license: 'MIT',
      homepage: 'https://github.com/K-Dense-AI/scientific-agent-skills',
      source: { type: 'github', repo: 'K-Dense-AI/scientific-agent-skills', path: 'skills/paper-lookup', sha: SHA },
      digest: DIGEST,
      files: 3,
      bytes: 4096,
      scripts: 2,
      runtime: { python: { version: '3.12', packages: [] } },
      smoke: ['python3', 'scripts/lookup.py', '--help'],
      caveats: ['Some sources rate-limit anonymous requests.'],
      installs: installs['paper-lookup'] ?? [],
    },
    {
      id: 'word-documents',
      title: 'Word documents',
      category: 'documents',
      summary: 'Create, read and edit .docx files.',
      license: 'MIT',
      homepage: 'https://github.com/Barnabet/desk',
      source: { type: 'builtin', path: 'word-documents' },
      digest: DIGEST,
      files: 5,
      bytes: 9000,
      scripts: 3,
      runtime: { python: { version: '3.12', packages: ['python-docx==1.2.0'] } },
      caveats: [],
      installs: installs['word-documents'] ?? [],
    },
    {
      id: 'pre-mortem',
      title: 'Pre-mortem',
      category: 'planning',
      summary: 'Imagine a plan failed, then sort its risks.',
      license: 'MIT',
      homepage: 'https://github.com/phuryn/pm-skills',
      source: { type: 'github', repo: 'phuryn/pm-skills', path: 'pm-execution/skills/pre-mortem', sha: SHA },
      digest: DIGEST,
      files: 1,
      bytes: 4000,
      scripts: 0,
      runtime: {},
      caveats: [],
      installs: installs['pre-mortem'] ?? [],
    },
  ];
}

export const install = (o: Partial<CatalogInstall> = {}): CatalogInstall => ({ scope: 'global', project_id: null, state: 'installed', sha: SHA, runtime: 'ready', runtime_reason: null, ...o });

export function reviewOf(item: CatalogItem, o: Partial<CatalogReview> = {}): CatalogReview {
  const { installs: _installs, ...entry } = item;
  return {
    entry,
    source_url: entry.source.type === 'github' ? `https://github.com/${entry.source.repo}/tree/${SHA}/${entry.source.path}` : null,
    files: [
      { path: 'SKILL.md', size: 120, script: false },
      { path: 'scripts/lookup.py', size: 300, script: true },
    ],
    skill_md: `---\nname: ${item.id}\ndescription: ${item.summary}\n---\n\nUse scripts/lookup.py.\n`,
    license_text: 'MIT License\n\nPermission is hereby granted…',
    warnings: [],
    ...o,
  };
}
