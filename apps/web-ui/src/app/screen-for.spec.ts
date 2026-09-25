import { describe, expect, it } from 'vitest';
import { screenFor } from './screen-for';
import { NotYet } from './screens/not-yet';

describe('screenFor', () => {
  it('names a screen for every route but the tray', () => {
    expect(screenFor({ name: 'tray' })).toBeNull();
    expect(screenFor({ name: 'map', newProject: true })).toEqual({ component: NotYet, inputs: { label: 'The map' } });
    expect(screenFor({ name: 'catalog', review: 'pdf-toolkit' })?.inputs).toEqual({ label: 'The skill catalog' });
    expect(screenFor({ name: 'project', id: 'p', tab: 'memory', q: 'auth' })?.inputs).toEqual({ label: 'Memory' });
    for (const tab of ['conversation', 'threads', 'library', 'memory', 'settings'] as const) expect(screenFor({ name: 'project', id: 'p', tab })).not.toBeNull();
  });
});
