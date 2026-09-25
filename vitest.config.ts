import { defineConfig } from 'vitest/config';

const live = process.env.DESK_LIVE === '1';
const roots = ['packages/*/src', 'apps/*/src', 'test/*/src', 'scripts'];

export default defineConfig({
  test: {
    include: roots.map((r) => `${r}/**/${live ? '*.live.test.ts' : '*.test.{ts,tsx}'}`),
    // apps/web-ui is tested by the Angular CLI (`pnpm --filter @desk/web-ui test`), never by root Vitest.
    exclude: ['**/node_modules/**', '**/*.e2e.test.ts', 'apps/web-ui/**', ...(live ? [] : ['**/*.live.test.ts'])],
    testTimeout: live ? 300_000 : 15_000,
  },
});
