import { describe, expect, it } from 'vitest';
import { EXPR_MAX_DEPTH, ExprError, evalExpr, exprPaths, parseExpr, type ExprScope } from './expr';

const scope: ExprScope = {
  inputs: { url: 'https://example.com/a', count: 3, dry: false, empty: '', zero: 0, tags: ['news', 'tech'], none: null },
  steps: {
    fetch: { status: 'succeeded', route: 'changed', outputs: { count: 5, title: 'Hello world', names: ['ada', 'bob'], ok: true, blank: [] } },
    review: { status: 'rejected', route: 'rejected', outputs: {} },
  },
};
const empty: ExprScope = { inputs: {}, steps: {} };
const ev = (src: string, s: ExprScope = scope) => evalExpr(parseExpr(src), s);

function errorOf(src: string): ExprError {
  try {
    parseExpr(src);
  } catch (err) {
    if (err instanceof ExprError) return err;
    throw err;
  }
  throw new Error(`parsed without an error: ${src}`);
}

describe('when expressions', () => {
  it('compares numbers, strings, booleans and null', () => {
    expect(ev('steps.fetch.outputs.count == 5')).toBe(true);
    expect(ev('steps.fetch.outputs.count != 5')).toBe(false);
    expect(ev('steps.fetch.outputs.count > 4')).toBe(true);
    expect(ev('steps.fetch.outputs.count >= 5')).toBe(true);
    expect(ev('steps.fetch.outputs.count < 5')).toBe(false);
    expect(ev('steps.fetch.outputs.count <= 5')).toBe(true);
    expect(ev('-1.5 < 0 and 2.5 > 2')).toBe(true);
    expect(ev("inputs.url == 'https://example.com/a'")).toBe(true);
    expect(ev("'apple' < 'banana' and 'b' >= 'a'")).toBe(true);
    expect(ev('inputs.dry == false')).toBe(true);
    expect(ev('inputs.none == null and null == null')).toBe(true);
    expect(ev('steps.fetch.route == "changed"')).toBe(true);
    expect(ev('steps.review.status == "rejected" and steps.review.route == "rejected"')).toBe(true);
  });

  it('never compares values of different types, except that != is true', () => {
    expect(ev("5 == '5'")).toBe(false);
    expect(ev("5 != '5'")).toBe(true);
    expect(ev("5 < '6'")).toBe(false);
    expect(ev("'6' > 5")).toBe(false);
    expect(ev('null < 1')).toBe(false);
    expect(ev('inputs.count >= null')).toBe(false);
    expect(ev('inputs.none != 0')).toBe(true);
    expect(ev("inputs.none == ''")).toBe(false);
    expect(ev('true > false')).toBe(false); // booleans are not ordered
    expect(ev('inputs.tags > inputs.tags')).toBe(false); // nor are lists
  });

  it('reads a missing path as null', () => {
    expect(ev('inputs.missing == null')).toBe(true);
    expect(ev('steps.nope.status == null')).toBe(true);
    expect(ev('steps.nope.route != "changed"')).toBe(true);
    expect(ev('steps.fetch.outputs.nothing > 0')).toBe(false);
    expect(ev('steps.nope.outputs.count')).toBe(false);
    expect(ev('steps.fetch.outputs.count > 0', empty)).toBe(false);
  });

  it('tests existence: neither missing nor null', () => {
    expect(ev('exists(inputs.url)')).toBe(true);
    expect(ev('exists( inputs.zero )')).toBe(true);
    expect(ev('exists(steps.fetch.outputs.blank)')).toBe(true);
    expect(ev('exists(steps.review.route)')).toBe(true);
    expect(ev('exists(inputs.none)')).toBe(false);
    expect(ev('exists(inputs.missing)')).toBe(false);
    expect(ev('not exists(steps.nope.route)')).toBe(true);
  });

  it('checks contains on strings and lists', () => {
    expect(ev("steps.fetch.outputs.title contains 'world'")).toBe(true);
    expect(ev("steps.fetch.outputs.title contains 'World'")).toBe(false);
    expect(ev("inputs.url contains ''")).toBe(true);
    expect(ev("inputs.tags contains 'tech'")).toBe(true);
    expect(ev("inputs.tags contains 'te'")).toBe(false); // an element, not a substring
    expect(ev('steps.fetch.outputs.names contains 1')).toBe(false);
    expect(ev('steps.fetch.outputs.count contains 5')).toBe(false);
    expect(ev("inputs.missing contains 'x'")).toBe(false);
  });

  it('truth-tests an operand without an operator', () => {
    expect(ev('steps.fetch.outputs.ok')).toBe(true);
    expect(ev('inputs.tags')).toBe(true);
    expect(ev('inputs.count')).toBe(true);
    expect(ev("'x'")).toBe(true);
    for (const falsy of ['inputs.dry', 'inputs.empty', 'inputs.zero', 'steps.fetch.outputs.blank', 'inputs.none', 'inputs.missing', '0', 'null', "''"]) {
      expect(ev(falsy), falsy).toBe(false);
    }
  });

  it('binds not before and before or, and honours parentheses', () => {
    expect(ev('true or false and false')).toBe(true);
    expect(ev('(true or false) and false')).toBe(false);
    expect(ev('not false and false')).toBe(false);
    expect(ev('not (false and false)')).toBe(true);
    expect(ev('false or not false')).toBe(true);
    expect(ev('not not true')).toBe(true);
    expect(ev('inputs.count > 1 and inputs.count < 5 or inputs.dry')).toBe(true);
    expect(parseExpr('inputs.a or inputs.b and not inputs.c')).toEqual({
      kind: 'or',
      items: [
        { kind: 'path', path: 'inputs.a' },
        { kind: 'and', items: [{ kind: 'path', path: 'inputs.b' }, { kind: 'not', expr: { kind: 'path', path: 'inputs.c' } }] },
      ],
    });
  });

  it('short-circuits and and or', () => {
    const inputs: Record<string, unknown> = {};
    Object.defineProperty(inputs, 'boom', {
      enumerable: true,
      get() {
        throw new Error('read boom');
      },
    });
    const s: ExprScope = { inputs, steps: {} };
    expect(ev('true or inputs.boom', s)).toBe(true);
    expect(ev('false and inputs.boom', s)).toBe(false);
    expect(() => ev('inputs.boom or true', s)).toThrow('read boom');
  });

  it('lists the paths it reads, once each, in first-seen order', () => {
    const e = parseExpr("steps.fetch.route == 'changed' and (exists(inputs.url) or steps.fetch.outputs.count > inputs.count) and inputs.url != ''");
    expect(exprPaths(e)).toEqual(['steps.fetch.route', 'inputs.url', 'steps.fetch.outputs.count', 'inputs.count']);
    expect(exprPaths(parseExpr('true'))).toEqual([]);
  });

  it('reads strings in either quote with backslash escapes', () => {
    expect(ev(String.raw`'it\'s' == "it's"`)).toBe(true);
    expect(ev(String.raw`"say \"hi\"" contains '"hi"'`)).toBe(true);
    expect(ev(String.raw`'back\\slash' contains "\\"`)).toBe(true);
    expect(ev(String.raw`'a\nb' contains '\n'`)).toBe(true);
    expect(ev("'and or not' == \"and or not\"")).toBe(true); // keywords inside strings are text
  });

  it('refuses bad tokens with their position', () => {
    expect(errorOf('inputs.a = 1')).toMatchObject({ position: 9, message: expect.stringMatching(/Unexpected character "=" at position 9/) });
    expect(errorOf('inputs.a == 1 && inputs.b').position).toBe(14);
    expect(errorOf('inputs.a == `x`').position).toBe(12);
    expect(errorOf("inputs.a == 'open")).toMatchObject({ position: 12, message: expect.stringMatching(/Unterminated string/) });
    expect(errorOf('inputs.a == yes')).toMatchObject({ position: 12, message: expect.stringMatching(/Unknown name 'yes'/) });
    expect(errorOf('inputs.a == 12abc')).toMatchObject({ position: 12, message: expect.stringMatching(/Malformed number/) });
    expect(errorOf('inputs.a ==')).toMatchObject({ position: 11, message: expect.stringMatching(/ends at position 11/) });
    expect(errorOf('(inputs.a == 1')).toMatchObject({ position: 14, message: expect.stringMatching(/Expected '\)'/) });
    expect(errorOf('inputs.a == 1 inputs.b')).toMatchObject({ position: 14, message: expect.stringMatching(/Unexpected 'inputs.b'/) });
    expect(errorOf('inputs.a == 1 == 2').position).toBe(14);
    expect(errorOf('and inputs.a').position).toBe(0);
    expect(errorOf('exists(1)')).toMatchObject({ position: 7, message: expect.stringMatching(/exists needs a path/) });
    expect(errorOf('exists inputs.a').position).toBe(7);
    expect(errorOf('   ')).toMatchObject({ position: 0, message: 'The condition is empty' });
  });

  it('refuses paths outside the grammar, naming them', () => {
    const bad = [
      'inputs.A',
      'inputs.a.b',
      'inputs.',
      'inputs.a-b',
      'steps.fetch',
      'steps.fetch.outputs',
      'steps.fetch.outputs.x.y',
      'steps.fetch.summary',
      'steps.fetch.dir',
      'steps.Fetch.route',
      'run.id',
      'previous.steps.fetch.dir',
    ];
    for (const path of bad) {
      const err = errorOf(`${path} == 1`);
      expect(err.position, path).toBe(0);
      expect(err.message, path).toContain(`'${path}'`);
    }
    expect(errorOf("inputs.a == 1 and steps.x.bad == 'y'").position).toBe(18);
  });

  it('limits the length and the nesting depth', () => {
    const deep = (n: number) => '('.repeat(n) + 'true' + ')'.repeat(n);
    expect(ev(deep(EXPR_MAX_DEPTH))).toBe(true);
    expect(errorOf(deep(EXPR_MAX_DEPTH + 1))).toMatchObject({ position: EXPR_MAX_DEPTH, message: expect.stringMatching(/Nested too deeply/) });
    expect(ev('not '.repeat(EXPR_MAX_DEPTH) + 'true')).toBe(true);
    expect(errorOf('not '.repeat(EXPR_MAX_DEPTH + 1) + 'true').position).toBe(EXPR_MAX_DEPTH * 4);
    const longest = `'${'a'.repeat(498)}'`;
    expect(longest).toHaveLength(500);
    expect(ev(longest)).toBe(true);
    expect(errorOf(`'${'a'.repeat(499)}'`).message).toMatch(/at most 500 characters/);
  });

  it('keeps hostile input away from code and prototypes', () => {
    expect(ev('exists(inputs.constructor)', empty)).toBe(false);
    expect(ev('inputs.constructor == null', empty)).toBe(true);
    expect(ev('exists(steps.constructor.status)', empty)).toBe(false);
    expect(ev('steps.constructor.route == null', empty)).toBe(true);
    expect(ev('exists(steps.fetch.outputs.constructor)')).toBe(false);
    expect(ev('exists(inputs.inherited)', { inputs: Object.create({ inherited: 'x' }) as Record<string, unknown>, steps: {} })).toBe(false);
    expect(errorOf('inputs.__proto__ == 1').position).toBe(0);
    expect(errorOf('__proto__').message).toMatch(/Unknown name '__proto__'/);
    expect(errorOf('constructor').message).toMatch(/Unknown name 'constructor'/);
    expect(errorOf('inputs.url == process.exit(1)')).toMatchObject({ position: 14, message: expect.stringMatching(/Unknown name 'process.exit'/) });
    expect(errorOf("inputs.url == 'x'; require('fs')").position).toBe(17);
  });
});
