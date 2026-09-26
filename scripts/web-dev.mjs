#!/usr/bin/env node
// `pnpm web` (spec §9): rebuilds apps/web-ui on every change (ng build --watch, development) and runs `desk web --dev`,
// which serves apps/web-ui/dist/browser and reloads the page after each rebuild. Extra arguments go to desk web
// (`pnpm web --port 7500 --no-open`). Ctrl-C stops both; if either stops on its own, the other is stopped too.
import { spawn } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** The two processes `pnpm web` runs, as arguments to this Node (no shell, so Windows works too). */
export function webDevCommands(root, args = []) {
  return [
    {
      name: 'ng build --watch',
      args: [join(root, 'scripts', 'ng.mjs'), 'build', '--watch', '--configuration', 'development'],
      cwd: join(root, 'apps', 'web-ui'),
      // Enter belongs to desk web (a new login link), not to the build.
      stdio: ['ignore', 'inherit', 'inherit'],
    },
    {
      name: 'desk web --dev',
      args: ['--import', 'tsx', join(root, 'apps', 'cli', 'src', 'main.ts'), 'web', '--dev', ...args],
      cwd: root,
      stdio: 'inherit',
    },
  ];
}

/**
 * Runs the commands with this Node until one exits, then stops the others with SIGTERM. `done` resolves once all have
 * exited: with the first exit code (1 for a signal or a spawn error), or with 0 after `stop()`.
 */
export function runTogether(commands, o = {}) {
  const log = o.log ?? ((line) => console.error(line));
  let stopped = false;
  let first = null;
  const running = commands.map((c) => ({ c, child: spawn(process.execPath, c.args, { cwd: c.cwd, stdio: c.stdio ?? 'inherit', env: process.env }) }));
  const stopAll = () => {
    for (const { child } of running) if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  };
  const exits = running.map(
    ({ c, child }) =>
      new Promise((settle) => {
        const finish = (code, why) => {
          if (first === null) {
            first = code;
            if (!stopped) log(`${c.name} stopped (${why}); stopping the rest.`);
            stopAll();
          }
          settle();
        };
        child.once('exit', (code, signal) => finish(code ?? 1, signal ?? `exit ${code}`));
        child.once('error', (err) => finish(1, err.message));
      }),
  );
  return {
    done: Promise.all(exits).then(() => (stopped ? 0 : (first ?? 0))),
    stop() {
      stopped = true;
      stopAll();
    },
  };
}

function main(args) {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..');
  const run = runTogether(webDevCommands(root, args));
  for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => run.stop());
  run.done.then((code) => {
    process.exitCode = code;
  });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) main(process.argv.slice(2));
