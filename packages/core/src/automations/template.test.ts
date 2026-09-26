import { describe, expect, it } from 'vitest';
import { PATH_PATTERN, TemplateError, renderArgs, renderText, templatePaths, type TemplateScope } from './template';

const scope: TemplateScope = {
  inputs: { name: 'Ada', count: 3, dry: false, urls: ['https://a.example', 'https://b.example'], none: null, nothing: [] },
  steps: {
    fetch: { outputs: { count: 5, files: ['a.md', 'b.md'], ok: true }, summary: 'Fetched 5 pages', route: 'changed', dir: '/runs/r1/steps/fetch' },
    idle: { outputs: {}, summary: null, route: null, dir: '/runs/r1/steps/idle' },
  },
  run: { id: 'r1', dir: '/runs/r1', date: '2026-09-28', trigger: 'schedule', test: false },
  previous: { steps: { fetch: { outputs: { count: 4 }, dir: '/runs/r0/steps/fetch' } } },
};

function errorOf(src: string): TemplateError {
  try {
    templatePaths(src);
  } catch (err) {
    if (err instanceof TemplateError) return err;
    throw err;
  }
  throw new Error(`parsed without an error: ${src}`);
}

describe('templates', () => {
  it('renders every kind of path', () => {
    expect(
      renderText(
        'Hi {{inputs.name}}: {{ steps.fetch.summary }} ({{steps.fetch.route}}) in {{steps.fetch.dir}}; run {{run.id}} in {{run.dir}} on {{run.date}} by {{run.trigger}}, test={{run.test}}; last time {{previous.steps.fetch.outputs.count}} in {{previous.steps.fetch.dir}}',
        scope,
      ),
    ).toBe(
      'Hi Ada: Fetched 5 pages (changed) in /runs/r1/steps/fetch; run r1 in /runs/r1 on 2026-09-28 by schedule, test=false; last time 4 in /runs/r0/steps/fetch',
    );
  });

  it('renders numbers and booleans with String, and a list one item per line', () => {
    expect(renderText('{{inputs.count}} {{inputs.dry}} {{steps.fetch.outputs.ok}} {{steps.fetch.outputs.count}}', scope)).toBe('3 false true 5');
    expect(renderText('Files:\n{{steps.fetch.outputs.files}}\nend', scope)).toBe('Files:\na.md\nb.md\nend');
  });

  it('renders missing values, nulls and a missing previous run as empty', () => {
    expect(
      renderText(
        '[{{inputs.missing}}][{{inputs.none}}][{{steps.idle.summary}}][{{steps.idle.route}}][{{steps.nope.dir}}][{{steps.fetch.outputs.nothing}}][{{previous.steps.nope.dir}}]',
        scope,
      ),
    ).toBe('[][][][][][][]');
    expect(renderText('[{{previous.steps.fetch.dir}}][{{previous.steps.fetch.outputs.count}}]', { ...scope, previous: null })).toBe('[][]');
  });

  it('keeps text without templates, lone braces included', () => {
    expect(renderText('a }} b { c {d} }', scope)).toBe('a }} b { c {d} }');
    expect(renderText('{{ inputs.name }}}', scope)).toBe('Ada}');
    expect(renderText('', scope)).toBe('');
  });

  it('refuses an unclosed {{ and malformed paths', () => {
    expect(errorOf('Hi {{inputs.name')).toMatchObject({ position: 3, message: expect.stringMatching(/Unclosed \{\{ at position 3/) });
    expect(errorOf('x {{}}')).toMatchObject({ position: 2, message: expect.stringMatching(/Empty/) });
    expect(errorOf('{{   }}').message).toMatch(/Empty/);
    const bad = [
      '{{inputs}}',
      '{{inputs.Name}}',
      '{{steps.fetch.status}}',
      '{{steps.fetch.outputs}}',
      '{{run.when}}',
      '{{previous.steps.fetch.summary}}',
      '{{inputs.name | upper}}',
      '{{ {{inputs.name}} }}',
      '{{inputs.__proto__}}',
    ];
    for (const src of bad) expect(errorOf(src).message, src).toMatch(/not a valid template path/);
    expect(() => renderText('ok {{nope}}', scope)).toThrow(TemplateError);
    expect(() => renderArgs(['ok', '{{inputs.name'], scope)).toThrow(TemplateError);
  });

  it('lists the paths once each, in first-seen order', () => {
    expect(templatePaths('{{inputs.name}} {{steps.fetch.dir}} {{ inputs.name }} {{previous.steps.fetch.outputs.count}}')).toEqual([
      'inputs.name',
      'steps.fetch.dir',
      'previous.steps.fetch.outputs.count',
    ]);
    expect(templatePaths('no templates here }}')).toEqual([]);
  });

  it('substitutes inside each argv element', () => {
    expect(renderArgs(['--name', '{{inputs.name}}', '--out={{steps.fetch.dir}}/digest.md', 'literal', ''], scope)).toEqual([
      '--name',
      'Ada',
      '--out=/runs/r1/steps/fetch/digest.md',
      'literal',
      '',
    ]);
    expect(renderArgs([], scope)).toEqual([]);
    expect(renderArgs(['{{inputs.count}}', '{{run.test}}'], scope)).toEqual(['3', 'false']);
  });

  it('spreads an element that is exactly one list path', () => {
    expect(renderArgs(['--urls', '{{inputs.urls}}', '--files', '{{ steps.fetch.outputs.files }}'], scope)).toEqual([
      '--urls',
      'https://a.example',
      'https://b.example',
      '--files',
      'a.md',
      'b.md',
    ]);
    expect(renderArgs(['--urls={{inputs.urls}}'], scope)).toEqual(['--urls=https://a.example\nhttps://b.example']);
    expect(renderArgs([' {{inputs.urls}}'], scope)).toEqual([' https://a.example\nhttps://b.example']);
    expect(renderArgs(['a', '{{inputs.nothing}}', 'b'], scope)).toEqual(['a', 'b']);
    expect(renderArgs(['a', '{{inputs.missing}}', 'b'], scope)).toEqual(['a', '', 'b']);
  });

  it('keeps hostile values inert: one element, verbatim, never expanded again', () => {
    const evil = '; rm -rf ~ && echo $(whoami)';
    const s: TemplateScope = { ...scope, inputs: { name: evil, other: 'EXPANDED', sneaky: 'x {{inputs.other}} y', list: [evil, '{{inputs.other}}'] } };
    expect(renderArgs(['--name', '{{inputs.name}}'], s)).toEqual(['--name', evil]);
    expect(renderArgs(['--name={{inputs.name}}'], s)).toEqual([`--name=${evil}`]);
    expect(renderArgs(['{{inputs.sneaky}}'], s)).toEqual(['x {{inputs.other}} y']);
    expect(renderArgs(['{{inputs.list}}'], s)).toEqual([evil, '{{inputs.other}}']);
    expect(renderText('{{inputs.sneaky}} / {{inputs.name}}', s)).toBe(`x {{inputs.other}} y / ${evil}`);
  });

  it('reads own properties only', () => {
    expect(renderText('[{{inputs.constructor}}][{{steps.constructor.dir}}][{{steps.fetch.outputs.constructor}}][{{previous.steps.constructor.dir}}]', scope)).toBe(
      '[][][][]',
    );
    expect(renderArgs(['{{inputs.constructor}}'], scope)).toEqual(['']);
    const inherited: TemplateScope = { ...scope, inputs: Object.create({ leak: 'inherited' }) as Record<string, unknown> };
    expect(renderText('[{{inputs.leak}}]', inherited)).toBe('[]');
    // `__proto__` never gets that far: keys start with a lowercase letter.
    expect(() => renderText('{{inputs.__proto__}}', scope)).toThrow(TemplateError);
  });

  it('exports the path grammar', () => {
    for (const ok of ['inputs.a', 'steps.a-b.outputs.x_1', 'steps.s.summary', 'steps.s.route', 'steps.s.dir', 'run.id', 'run.test', 'previous.steps.s.dir', 'previous.steps.s.outputs.k']) {
      expect(PATH_PATTERN.test(ok), ok).toBe(true);
    }
    for (const bad of ['inputs.a-b', 'steps.s.status', 'run.status', 'previous.steps.s.summary', 'previous.inputs.a', 'inputs.a ', 'x.inputs.a']) {
      expect(PATH_PATTERN.test(bad), bad).toBe(false);
    }
  });
});
