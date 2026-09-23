import { defineConfig } from 'vitest/config';

const live = process.env.DESK_LIVE === '1';
const roots = ['packages/*/src', 'apps/*/src', 'test/*/src'];

export default defineConfig({
  test: {
    include: roots.map((r) => `${r}/**/${live ? '*.live.test.ts' : '*.test.ts'}`),
    exclude: ['**/node_modules/**', ...(live ? [] : ['**/*.live.test.ts'])],
    testTimeout: live ? 300_000 : 15_000,
  },
});
