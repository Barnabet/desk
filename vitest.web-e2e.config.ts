import { defineConfig } from 'vitest/config';

/**
 * `pnpm test:web-e2e`, after `pnpm --filter @desk/web-ui build`: Playwright with Chromium against an in-process deskd
 * and desk web (apps/web-ui/e2e), and the check of the built index.html (apps/web-server). Globals, so that no test
 * file under apps/web-ui imports `vitest`: that package has its own Vitest (the Angular runner's), and loading a second
 * copy breaks the run.
 */
export default defineConfig({
  test: {
    include: ['apps/web-ui/e2e/**/*.e2e.test.ts', 'apps/web-server/src/**/*.e2e.test.ts'],
    globals: true,
    testTimeout: 120_000,
    hookTimeout: 120_000,
    fileParallelism: false,
  },
});
