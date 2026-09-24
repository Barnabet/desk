import { parseArgs } from 'node:util';
import { macKeychain } from '@desk/core';
import { startDaemon, DEFAULT_PORT } from './daemon';
import { createLogger } from './logger';
import { daemonPaths, defaultDataDir } from './paths';

const { values } = parseArgs({ options: { port: { type: 'string' }, 'data-dir': { type: 'string' } } });
const dataDir = values['data-dir'] ?? defaultDataDir();
const log = createLogger(daemonPaths(dataDir).logFile);

process.on('uncaughtException', (err) => log.error('uncaught exception', err));
process.on('unhandledRejection', (err) => log.error('unhandled rejection', err));

try {
  const daemon = await startDaemon({
    dataDir,
    port: values.port ? Number(values.port) : DEFAULT_PORT,
    log,
    ...(process.platform === 'darwin' ? { keychain: macKeychain() } : {}),
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
