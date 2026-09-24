import { defineConfig } from 'vitest/config';

const live = process.env.DESK_LIVE === '1';
const roots = ['packages/*/src', 'apps/*/src', 'test/*/src'];

export default defineConfig({
  test: {
    include: roots.map((r) => `${r}/**/${live ? '*.live.test.ts' : '*.test.{ts,tsx}'}`),
    exclude: ['**/node_modules/**', '**/*.e2e.test.ts', ...(live ? [] : ['**/*.live.test.ts'])],
    testTimeout: live ? 300_000 : 15_000,
  },
});
