import { describe, expect, it } from 'vitest';
import { href, parseRoute, type Route } from './router';

describe('router', () => {
  it('parses and builds every route', () => {
    const routes: Route[] = [
      { name: 'onboarding' },
      { name: 'map' },
      { name: 'map', newProject: true },
      { name: 'attention' },
      { name: 'attention', item: 'approval:a/1' },
      { name: 'skills' },
      { name: 'skills', skill: 'brand-voice' },
      { name: 'system' },
      { name: 'tray' },
      { name: 'project', id: 'p1', tab: 'conversation' },
      { name: 'project', id: 'p1', tab: 'threads', threadId: 't9' },
      { name: 'project', id: 'p1', tab: 'settings' },
      { name: 'project', id: 'p1', tab: 'library', file: 'emails/01 welcome.md' },
    ];
    for (const r of routes) expect(parseRoute(href(r))).toEqual(r);
  });

  it('falls back to the map', () => {
    expect(parseRoute('')).toEqual({ name: 'map' });
    expect(parseRoute('#/nowhere')).toEqual({ name: 'map' });
    expect(parseRoute('#/p')).toEqual({ name: 'map' });
    expect(parseRoute('#/p/x/bogus')).toEqual({ name: 'project', id: 'x', tab: 'conversation' });
  });
});
