import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { OUTPUT_LIST_MAX, OUTPUT_STRING_MAX, OUTPUTS_MAX_BYTES, RESERVED_ROUTES, SUMMARY_MAX } from '@desk/protocol';
import { describe, expect, it } from 'vitest';
import { parseStepOutput } from './script';

/** The automation-scripts built-in skill (spec §6.5): its helper must accept exactly what a script step accepts. */
const SCRIPTS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills', 'automation-scripts', 'scripts');
const python = spawnSync('python3', ['--version']).status === 0;

/** Runs Python code with desk_step importable and a fresh DESK_OUTPUT; returns the exit status, stderr and the file written. */
function py(code: string): { status: number | null; stderr: string; raw: string | null } {
  const dir = mkdtempSync(join(tmpdir(), 'desk-step-'));
  const file = join(dir, '.desk', 'output.json');
  const prelude = `import sys\nsys.path.insert(0, ${JSON.stringify(SCRIPTS)})\nfrom desk_step import output\n`;
  const r = spawnSync('python3', ['-B', '-c', prelude + code], {
    cwd: dir,
    encoding: 'utf8',
    env: { PATH: process.env.PATH, HOME: process.env.HOME, DESK_OUTPUT: file, DESK_STEP_DIR: dir, DESK_RUN_DIR: dir },
  });
  let raw: string | null = null;
  try {
    raw = readFileSync(file, 'utf8');
  } catch {}
  return { status: r.status, stderr: r.stderr, raw };
}

describe('the automation-scripts built-in skill', () => {
  it("states Desk's output limits", () => {
    const src = readFileSync(join(SCRIPTS, 'desk_step.py'), 'utf8');
    const constant = (name: string) => Number(new RegExp(`^${name} = (\\d+)$`, 'm').exec(src)?.[1]);
    expect(constant('OUTPUTS_MAX_BYTES')).toBe(OUTPUTS_MAX_BYTES);
    expect(constant('OUTPUT_STRING_MAX')).toBe(OUTPUT_STRING_MAX);
    expect(constant('OUTPUT_LIST_MAX')).toBe(OUTPUT_LIST_MAX);
    expect(constant('SUMMARY_MAX')).toBe(SUMMARY_MAX);
    expect(src).toContain(`RESERVED_ROUTES = (${RESERVED_ROUTES.map((r) => `"${r}"`).join(', ')})`);
  });

  it.skipIf(!python)('writes results a script step accepts, up to the limits', () => {
    const r = py('output(route="changed", summary="Café \\U0001F600", count=2, ratio=0.5, ok=True, nothing=None, items=["a", 1, False], text="x" * 4000, many=list(range(200)))');
    expect(r.status, r.stderr).toBe(0);
    expect(parseStepOutput(r.raw!, ['changed', 'unchanged'])).toEqual({
      ok: true,
      route: 'changed',
      summary: 'Café 😀',
      outputs: { count: 2, ratio: 0.5, ok: true, nothing: null, items: ['a', 1, false], text: 'x'.repeat(4000), many: [...Array(200).keys()] },
    });
    // Both count characters as code points: 4000 emoji fit (16 000 bytes).
    const emoji = py('output(e="\\U0001F600" * 4000)');
    expect(emoji.status, emoji.stderr).toBe(0);
    expect(parseStepOutput(emoji.raw!, [])).toEqual({ ok: true, route: null, summary: null, outputs: { e: '😀'.repeat(4000) } });
  });

  it.skipIf(!python)('refuses what a script step refuses', () => {
    const cases: Array<[string, Record<string, unknown>]> = [
      ['output(s="x" * 4001)', { outputs: { s: 'x'.repeat(4001) } }],
      ['output(s="\\U0001F600" * 4001)', { outputs: { s: '😀'.repeat(4001) } }],
      ['output(items=list(range(201)))', { outputs: { items: [...Array(201).keys()] } }],
      ['output(meta={"a": 1})', { outputs: { meta: { a: 1 } } }],
      ['output(items=["a", None])', { outputs: { items: ['a', null] } }],
      ['output(**{"Bad": 1})', { outputs: { Bad: 1 } }],
      ['output(**{"k" + str(i): "x" * 4000 for i in range(5)})', { outputs: Object.fromEntries([0, 1, 2, 3, 4].map((i) => [`k${i}`, 'x'.repeat(4000)])) }],
      ['output(summary="s" * 2001)', { summary: 's'.repeat(2001) }],
      ['output(route="error")', { route: 'error' }],
    ];
    for (const [code, doc] of cases) {
      const r = py(`try:\n    ${code}\nexcept ValueError as e:\n    print(e)\n    raise SystemExit(9)\n`);
      expect(r.status, `${code}: ${r.stderr}`).toBe(9);
      expect(r.raw, code).toBeNull();
      expect(parseStepOutput(JSON.stringify(doc), ['changed']).ok, code).toBe(false);
    }
  });

  it.skipIf(!python)('passes its selftest', () => {
    const r = spawnSync('python3', ['-B', join(SCRIPTS, 'selftest.py')], {
      cwd: mkdtempSync(join(tmpdir(), 'desk-step-')),
      encoding: 'utf8',
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' },
      timeout: 60_000,
    });
    expect(r.status, r.stdout + r.stderr).toBe(0);
    expect(r.stdout).toMatch(/^ok: \d+ checks/m);
  });
});
