import { TestBed } from '@angular/core/testing';
import { afterEach, describe, expect, it } from 'vitest';
import { RouteService } from './route.service';

afterEach(() => history.replaceState(null, '', window.location.pathname));

describe('RouteService', () => {
  it('parses the current hash and follows hashchange', () => {
    history.replaceState(null, '', '#/p/p1/threads/t1?at=7');
    const routes = TestBed.inject(RouteService);
    expect(routes.route()).toEqual({ name: 'project', id: 'p1', tab: 'threads', threadId: 't1', at: 7 });
    history.replaceState(null, '', '#/system');
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(routes.route()).toEqual({ name: 'system' });
  });

  it('navigates with a history entry and replaces without one', () => {
    const routes = TestBed.inject(RouteService);
    routes.navigate({ name: 'attention', item: 'a1' });
    expect(window.location.hash).toBe('#/attention?item=a1');
    expect(routes.route()).toEqual({ name: 'attention', item: 'a1' });
    const depth = history.length;
    routes.replace('#/skills/weekly-report');
    expect(history.length).toBe(depth);
    expect(window.location.hash).toBe('#/skills/weekly-report');
    expect(routes.route()).toEqual({ name: 'skills', skill: 'weekly-report' });
  });
});
