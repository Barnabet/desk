import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { CatalogService, SkillRuntimes, treeDigest } from '@desk/core';
import { createHarness, makeTarball, newRuntime, type Harness } from '@desk/core/testing';
import type { CatalogEntry } from '@desk/protocol';
import { createApp } from './app';

let h: Harness;
let server: Server | undefined;
afterEach(async () => {
  await new Promise<void>((r) => (server ? server.close(() => r()) : r()));
  server = undefined;
  await h?.cleanup();
});

const SHA = 'c'.repeat(40);
const files = [
  { path: 'SKILL.md', content: Buffer.from('---\nname: fact-checker\ndescription: Verify claims in a document.\n---\n\nCheck each claim.\n') },
  { path: 'references/method.md', content: Buffer.from('# Method\n') },
];

/** A stand-in for codeload.github.com: answers 429 once, then serves the archive. */
async function archiveHost(): Promise<{ base: string; hits: string[] }> {
  const hits: string[] = [];
  const tar = makeTarball('claude-code-skills-x', files.map((f) => ({ name: `fact-checker/${f.path}`, content: f.content })));
  server = createServer((req, res) => {
    hits.push(req.url ?? '');
    if (hits.length === 1) return void res.writeHead(429).end();
    if (req.url === `/daymade/claude-code-skills/tar.gz/${SHA}`) return void res.writeHead(200, { 'content-type': 'application/x-gzip' }).end(tar);
    res.writeHead(404).end();
  });
  await new Promise<void>((r) => server!.listen(0, '127.0.0.1', r));
  return { base: `http://127.0.0.1:${(server!.address() as AddressInfo).port}`, hits };
}

const entry = (digest = treeDigest(files)): CatalogEntry => ({
  id: 'fact-checker',
  title: 'Fact checker',
  category: 'research',
  summary: 'Verify claims in a document and propose corrections.',
  license: 'MIT',
  homepage: 'https://github.com/daymade/claude-code-skills',
  source: { type: 'github', repo: 'daymade/claude-code-skills', path: 'fact-checker', sha: SHA },
  digest,
  files: 2,
  bytes: 120,
  runtime: {},
  caveats: [],
});

async function setup(entries: CatalogEntry[]) {
  h = await createHarness();
  const runtime = newRuntime(h);
  const host = await archiveHost();
  const catalog = new CatalogService({
    runtime,
    store: h.store,
    dataDir: h.dir,
    builtinRoot: join(h.dir, 'builtin'),
    archiveBase: host.base,
    catalog: { version: 1, updated: '2026-09-24', entries },
  });
  const app = createApp({ runtime, store: h.store, models: h.models, catalog, token: 't', version: '1.0.0' });
  const api = async (method: string, path: string, body?: unknown) => {
    const res = await app.request(`/v1${path}`, {
      method,
      headers: { authorization: 'Bearer t', ...(body !== undefined ? { 'content-type': 'application/json' } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
    return { status: res.status, body: (await res.json()) as any };
  };
  return { api, runtime, host };
}

describe('catalog API', () => {
  it('lists, prepares (retrying a 429), installs and reports the state', async () => {
    const { api, host } = await setup([entry()]);
    expect((await api('GET', '/catalog')).body).toMatchObject([{ id: 'fact-checker', installs: [] }]);

    const review = await api('POST', '/catalog/fact-checker/prepare');
    expect(review.status).toBe(200);
    expect(review.body.files.map((f: { path: string }) => f.path)).toEqual(['SKILL.md', 'references/method.md']);
    expect(host.hits).toEqual([`/daymade/claude-code-skills/tar.gz/${SHA}`, `/daymade/claude-code-skills/tar.gz/${SHA}`]);

    const installed = await api('POST', '/catalog/fact-checker/install', {});
    expect(installed).toMatchObject({ status: 201, body: { skill: { name: 'fact-checker', scope: 'global', version: 1 }, state: 'installed' } });
    expect((await api('GET', '/catalog')).body[0].installs).toEqual([
      { scope: 'global', project_id: null, state: 'installed', sha: SHA, runtime: 'none', runtime_reason: null },
    ]);
    expect((await api('GET', '/skills/fact-checker/history')).body[0]).toMatchObject({ origin: `catalog:fact-checker@${SHA}` });
  });

  it('maps refusals to 400, 404 and 409', async () => {
    const { api, runtime } = await setup([entry(`sha256:${'0'.repeat(64)}`)]);
    expect((await api('POST', '/catalog/nope/prepare')).status).toBe(404);
    const mismatch = await api('POST', '/catalog/fact-checker/install', {});
    expect(mismatch).toMatchObject({ status: 400, body: { error: { code: 'invalid', message: expect.stringMatching(/does not match the catalog/) } } });
    runtime.saveSkill({ scope: 'global', name: 'fact-checker', description: 'Mine', instructions: 'Mine.' });
    const taken = await api('POST', '/catalog/fact-checker/install', {});
    expect(taken).toMatchObject({ status: 409, body: { error: { code: 'conflict' } } });
    expect((await api('POST', '/catalog/fact-checker/install', { scope: 'nowhere' })).status).toBe(400);
  });
});

describe('skill runtime routes', () => {
  it('reports sizes, cleans up, and refuses a retry for skills not from the catalog', async () => {
    h = await createHarness();
    const runtime = newRuntime(h);
    const skillRuntimes = new SkillRuntimes({ dataDir: h.dir, store: h.store, uv: null, nodeExec: process.execPath, exists: () => false });
    const catalog = new CatalogService({ runtime, store: h.store, dataDir: h.dir, builtinRoot: join(h.dir, 'b'), runtimes: skillRuntimes, catalog: { version: 1, updated: '2026-09-24', entries: [] } });
    const app = createApp({ runtime, store: h.store, models: h.models, catalog, skillRuntimes, token: 't', version: '1.0.0' });
    const call = async (method: string, path: string) => {
      const res = await app.request(`/v1${path}`, { method, headers: { authorization: 'Bearer t' } });
      return { status: res.status, body: (await res.json()) as any };
    };
    expect(await call('GET', '/system/runtimes')).toEqual({ status: 200, body: { bytes: 0, envs: [] } });
    expect(await call('POST', '/system/runtimes/cleanup')).toEqual({ status: 200, body: { removed: 0, bytes: 0 } });
    expect((await call('POST', '/skills/nope/runtime/retry')).status).toBe(404);
  });
});
