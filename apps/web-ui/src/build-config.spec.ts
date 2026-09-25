import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

type Target = { builder: string; options: Record<string, unknown>; configurations?: Record<string, Record<string, unknown>>; defaultConfiguration?: string };
type AngularJson = { projects: { 'web-ui': { architect: { build: Target; test: Target } } } };

/** apps/web-ui: `ng test` runs there. */
const ROOT = existsSync(join(process.cwd(), 'angular.json')) ? process.cwd() : join(process.cwd(), 'apps', 'web-ui');
const architect = (JSON.parse(readFileSync(join(ROOT, 'angular.json'), 'utf8')) as AngularJson).projects['web-ui'].architect;

describe('the Angular build', () => {
  it('ships a production build the CSP allows (spec §4.9, §9)', () => {
    const build = architect.build;
    expect(build.builder).toBe('@angular/build:application');
    expect(build.defaultConfiguration).toBe('production');
    expect(build.configurations?.['production']?.['optimization']).toEqual({ scripts: true, styles: { minify: true, inlineCritical: false }, fonts: false });
    expect(JSON.stringify(build)).not.toContain('autoCsp');
    expect(build.options).toMatchObject({ browser: 'src/main.ts', index: 'src/index.html', outputPath: 'dist', styles: ['@desk/ui-styles/index.css'] });
  });

  it('tests the specs under src with the development build', () => {
    expect(architect.test).toMatchObject({
      builder: '@angular/build:unit-test',
      options: { runner: 'vitest', tsConfig: 'tsconfig.spec.json', buildTarget: 'web-ui:build:development', include: ['src/**/*.spec.ts'] },
    });
  });

  it('has no inline script, event handler or base element in index.html', () => {
    const html = readFileSync(join(ROOT, 'src', 'index.html'), 'utf8');
    expect(html).toContain('<desk-root></desk-root>');
    expect(html).not.toMatch(/<script\b/i);
    expect(html).not.toMatch(/[\s/"']on[a-z]+\s*=/i);
    expect(html).not.toMatch(/<base\b/i);
  });
});
