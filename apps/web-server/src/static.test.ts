import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Hono } from 'hono';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DEV_RELOAD_TAG, findInlineCode, serveUi } from './static';

const BUILT_INDEX = fileURLToPath(new URL('../../web-ui/dist/browser/index.html', import.meta.url));
const INDEX = '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Desk</title><link rel="stylesheet" href="styles-AB12.css"></head><body><desk-root></desk-root><script src="main-CD34.js" type="module"></script></body></html>';

let root: string;
let uiDir: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'desk-web-static-'));
  uiDir = join(root, 'dist', 'browser');
  mkdirSync(join(uiDir, 'media'), { recursive: true });
  writeFileSync(join(uiDir, 'index.html'), INDEX);
  writeFileSync(join(uiDir, 'main-CD34.js'), 'console.log(1);');
  writeFileSync(join(uiDir, 'styles-AB12.css'), 'body{}');
  writeFileSync(join(uiDir, 'media', 'geist.woff2'), Buffer.from([1, 2, 3]));
  writeFileSync(join(root, 'dist', 'secret.txt'), 'outside the build');
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

const app = (dev = false, dir = uiDir) => {
  const a = new Hono();
  a.get('*', serveUi(dir, { dev }));
  return a;
};

describe('serveUi', () => {
  it('serves index.html at / (never cached) and the assets with their types', async () => {
    for (const path of ['/', '/index.html']) {
      const res = await app().request(path);
      expect(res.status, path).toBe(200);
      expect(res.headers.get('content-type')).toBe('text/html; charset=utf-8');
      expect(res.headers.get('cache-control')).toBe('no-store');
      expect(await res.text()).toBe(INDEX);
    }
    const js = await app().request('/main-CD34.js');
    expect(js.headers.get('content-type')).toBe('text/javascript; charset=utf-8');
    expect(await js.text()).toBe('console.log(1);');
    expect((await app().request('/styles-AB12.css')).headers.get('content-type')).toBe('text/css; charset=utf-8');
    const font = await app().request('/media/geist.woff2');
    expect(font.headers.get('content-type')).toBe('font/woff2');
    expect(new Uint8Array(await font.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
  });

  it('never serves a file outside the build, and 404s what is not there', async () => {
    for (const path of ['/..%2fsecret.txt', '/%2e%2e/secret.txt', '/media/..%2f..%2fsecret.txt', '/missing.js', '/media', '/%E0%A4%A']) {
      expect((await app().request(path)).status, path).toBe(404);
    }
  });

  it('says how to build the app when it is not built', async () => {
    const res = await app(false, join(root, 'nothing'))
      .request('/');
    expect(res.status).toBe(503);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.text()).toContain('pnpm --filter @desk/web-ui build');
  });

  it('adds the external reload script under --dev, keeping index.html free of inline code', async () => {
    const html = await (await app(true).request('/')).text();
    expect(html).toContain(`${DEV_RELOAD_TAG}</body>`);
    expect(findInlineCode(html)).toEqual([]);
    expect(await (await app(false).request('/')).text()).not.toContain('__dev');
  });
});

describe('findInlineCode', () => {
  it('flags inline scripts and event-handler attributes, and passes external scripts', () => {
    expect(findInlineCode(INDEX)).toEqual([]);
    expect(findInlineCode('<script>alert(1)</script>')).toHaveLength(1);
    expect(findInlineCode('<script type="module"></script>')).toHaveLength(1);
    expect(findInlineCode('<link rel="stylesheet" href="styles.css" media="print" onload="this.media=\'all\'">')).toEqual(['event handler attribute: onload']);
    expect(findInlineCode('<img src=x onerror=alert(1)>')).toEqual(['event handler attribute: onerror']);
    expect(findInlineCode('<div data-onboard="x" title="turn on=off"></div>')).toEqual([]);
  });

  it.skipIf(!existsSync(BUILT_INDEX))('finds none in the built web UI (apps/web-ui/dist/browser/index.html)', () => {
    expect(findInlineCode(readFileSync(BUILT_INDEX, 'utf8'))).toEqual([]);
    expect(dirname(BUILT_INDEX)).toMatch(/web-ui[\\/]dist[\\/]browser$/);
  });
});
