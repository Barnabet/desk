import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { macKeychain } from '@desk/core';
import { startDaemon, DEFAULT_PORT } from './daemon';
import { createLogger } from './logger';
import { macNotify } from './notifier';
import { daemonPaths, defaultDataDir } from './paths';

const { values } = parseArgs({ options: { port: { type: 'string' }, 'data-dir': { type: 'string' } } });
const dataDir = values['data-dir'] ?? defaultDataDir();
const log = createLogger(daemonPaths(dataDir).logFile);
/** The packaged app ships uv next to deskd for Python skill runtimes; DESK_UV still wins. */
const bundledUv = process.env.DESK_BUNDLED === '1' && !process.env.DESK_UV ? fileURLToPath(new URL('./bin/uv', import.meta.url)) : null;

/** The bundle's content hash, written by scripts/bundle.mjs; the app restarts a daemon whose build differs from its own. */
const buildFile = fileURLToPath(new URL('./build-id', import.meta.url));
const build = process.env.DESK_BUNDLED === '1' && existsSync(buildFile) ? readFileSync(buildFile, 'utf8').trim() : null;

process.on('uncaughtException', (err) => log.error('uncaught exception', err));
process.on('unhandledRejection', (err) => log.error('unhandled rejection', err));

try {
  const daemon = await startDaemon({
    dataDir,
    port: values.port ? Number(values.port) : DEFAULT_PORT,
    log,
    build,
    ...(process.env.DESK_BUNDLED === '1'
      ? { migrationsDir: fileURLToPath(new URL('./drizzle', import.meta.url)), catalog: { builtinRoot: fileURLToPath(new URL('./catalog/skills', import.meta.url)) } }
      : {}),
    ...(bundledUv && existsSync(bundledUv) ? { runtimes: { uv: bundledUv } } : {}),
    ...(process.platform === 'darwin' ? { keychain: macKeychain(), notify: macNotify } : {}),
  });
  let stopping = false;
  const stop = async (signal: string) => {
    if (stopping) return;
    stopping = true;
    log.info(`received ${signal}, shutting down`);
    await daemon.stop();
    process.exit(0);
  };
  process.on('SIGTERM', () => void stop('SIGTERM'));
  process.on('SIGINT', () => void stop('SIGINT'));
} catch (err) {
  log.error('failed to start', err);
  process.exit(1);
}
