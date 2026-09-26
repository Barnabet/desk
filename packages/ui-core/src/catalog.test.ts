import { describe, expect, it } from 'vitest';
import { CatalogCategory, type CatalogInstall } from '@desk/protocol';
import { actionFor, CATALOG_BAYS, catalogIndex, installRef, runtimePackages, runtimeWords, sourceLabel } from './catalog';
import { whoLabel } from './skill-origin';

const install = (over: Partial<CatalogInstall> = {}): CatalogInstall =>
  ({ scope: 'global', project_id: null, state: 'installed', sha: 'a'.repeat(40), runtime: 'ready', ...over }) as CatalogInstall;

describe('the catalog helpers', () => {
  it('knows its five bays, and none for files', () => {
    expect(CATALOG_BAYS.map((b) => b.category)).toEqual(['research', 'documents', 'writing', 'planning', 'code']);
    expect(CatalogCategory.options.filter((c) => !CATALOG_BAYS.some((b) => b.category === c))).toEqual(['files']);
    expect(CATALOG_BAYS.find((b) => b.category === 'documents')?.blurb).toBe('Conversion to Markdown, data analysis, Excel automation, HTML slides.');
  });

  it('names what Desk sets up, the packages, the source and the action a card offers', () => {
    const py = { version: '3.12', packages: ['python-docx==1.2.0'] };
    expect(runtimeWords({ runtime: { python: py, extras: ['browser'] } })).toBe('Python 3.12 + browser · set up by Desk');
    expect(runtimeWords({ runtime: {} })).toBe('Nothing to set up');
    expect(runtimePackages({ runtime: { python: py } })).toEqual(['python-docx==1.2.0']);
    expect(sourceLabel({ source: { type: 'builtin', path: 'word-documents' } })).toBe('Desk');
    expect(actionFor(undefined)).toEqual({ label: 'Install', kind: 'install' });
    expect(actionFor(install({ state: 'update_available' }))).toEqual({ label: 'Update', kind: 'update' });
  });

  it('indexes the installs that came from the catalog by skill key', () => {
    const item = (id: string, installs: CatalogInstall[]) => ({ id, installs }) as unknown as Parameters<typeof catalogIndex>[0][number];
    const index = catalogIndex([item('word-documents', [install(), install({ scope: 'project', project_id: 'p1', state: 'name_taken' })]), item('pre-mortem', [install({ scope: 'project', project_id: 'p2' })])]);
    expect([...index.keys()]).toEqual(['global:word-documents', 'project:p2:pre-mortem']);
    expect(installRef('pre-mortem', { scope: 'project', project_id: 'p2' })).toEqual({ scope: 'project', projectId: 'p2', name: 'pre-mortem' });
  });
});

describe('whoLabel', () => {
  it('says who made a version: you, a thread, Desk, a built-in skill, or the catalog with its commit', () => {
    const titles = new Map([['t1', 'Welcome emails']]);
    expect(whoLabel(null, titles)).toBe('Unknown');
    expect(whoLabel('user', titles)).toBe('You');
    expect(whoLabel('agent:t1', titles)).toBe('Welcome emails');
    expect(whoLabel('agent:d9', titles)).toBe('Desk');
    expect(whoLabel('builtin:word-documents', titles)).toBe('Built into Desk');
    expect(whoLabel(`catalog:word-documents@${'a'.repeat(40)}`, titles)).toBe('Catalog · aaaaaaa');
    expect(whoLabel('catalog:word-documents@builtin-0123456789ab', titles)).toBe('Catalog');
    expect(whoLabel('import', titles)).toBe('import');
  });
});
