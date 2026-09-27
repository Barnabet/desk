import { describe, expect, it } from 'vitest';
import { digestDef } from './testing/automations';
import { activeToken, conditionSuggestions, insertSuggestion, isKnownPath, pathsIn, templateSuggestions } from './automation-templates';

describe('template suggestions', () => {
  it('offers inputs, upstream results, the run and previous runs', () => {
    const paths = templateSuggestions(digestDef(), 'ok').map((s) => s.path);
    expect(paths).toEqual(expect.arrayContaining(['inputs.topic', 'steps.fetch.summary', 'steps.fetch.dir', 'steps.fetch.route', 'steps.fetch.outputs.', 'steps.sum.outputs.headline', 'run.date', 'previous.steps.fetch.dir', 'previous.steps.ok.dir']));
    expect(paths).not.toContain('steps.ok.summary');
    expect(templateSuggestions(digestDef(), 'fetch').map((s) => s.path).filter((p) => p.startsWith('steps.'))).toEqual([]);
  });

  it('offers condition paths from the edge’s source and what is upstream of it', () => {
    const paths = conditionSuggestions(digestDef(), 'sum').map((s) => s.path);
    expect(paths).toEqual(expect.arrayContaining(['inputs.topic', 'steps.sum.status', 'steps.sum.outputs.headline', 'steps.fetch.route', 'steps.fetch.status']));
    expect(paths).not.toContain('steps.ok.status');
  });

  it('finds the partial path being typed and inserts a suggestion', () => {
    expect(activeToken('Hi {{inp', 8)).toEqual({ start: 3, query: 'inp' });
    expect(activeToken('Hi {{inputs.a}} x', 17)).toBeNull();
    expect(activeToken('Hi {{ in put', 12)).toBeNull();
    expect(insertSuggestion('Hi {{inp', 8, { path: 'inputs.topic', label: 'Topic' })).toEqual({ text: 'Hi {{inputs.topic}}', caret: 19 });
    expect(insertSuggestion('Hi {{inp}} there', 8, { path: 'inputs.topic', label: 'Topic' })).toEqual({ text: 'Hi {{inputs.topic}} there', caret: 19 });
    expect(insertSuggestion('{{steps.f', 9, { path: 'steps.fetch.outputs.', label: 'x', open: true })).toEqual({ text: '{{steps.fetch.outputs.', caret: 22 });
  });

  it('lists the paths a text uses, and knows which are available here', () => {
    const s = templateSuggestions(digestDef(), 'ok');
    expect(pathsIn('a {{inputs.topic}} b {{ steps.fetch.outputs.count }} {{inputs.topic}}')).toEqual(['inputs.topic', 'steps.fetch.outputs.count']);
    expect(isKnownPath('steps.fetch.outputs.count', s)).toBe(true);
    expect(isKnownPath('steps.sum.outputs.nope', s)).toBe(false);
    expect(isKnownPath('previous.steps.sum.outputs.headline', s)).toBe(true);
    expect(isKnownPath('previous.steps.ghost.dir', s)).toBe(false);
  });
});
