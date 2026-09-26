#!/usr/bin/env node
// `pnpm web` (spec §9): rebuilds apps/web-ui on every change (ng build --watch, development) and runs `desk web --dev`,
// which serves apps/web-ui/dist/browser and reloads the page after each rebuild. Extra arguments go to desk web
// (`pnpm web --port 7500 --no-open`). Ctrl-C stops both; if either stops on its own, the other is stopped too.
import { spawn, spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isMain } from './ng.mjs';

/**
 * The two processes `pnpm web` runs, as arguments to this Node (no shell, so Windows works too). `tree` marks the one
 * whose own children stop with it (`stopChild`).
 */
export function webDevCommands(root, args = []) {
  return [
    {
      name: 'ng build --watch',
      args: [join(root, 'scripts', 'ng.mjs'), 'build', '--watch', '--configuration', 'development'],
      cwd: join(root, 'apps', 'web-ui'),
      // Enter belongs to desk web (a new login link), not to the build.
      stdio: ['ignore', 'inherit', 'inherit'],
      // ng.mjs runs the Angular CLI as its child.
      tree: true,
    },
    {
      // No tree: a deskd that desk web started (Start Desk, Restart) is its child, and must outlive it.
      name: 'desk web --dev',
      args: ['--import', 'tsx', join(root, 'apps', 'cli', 'src', 'main.ts'), 'web', '--dev', ...args],
      cwd: root,
      stdio: 'inherit',
    },
  ];
}

/**
 * Stops a child with SIGTERM, which ng.mjs passes on to the Angular CLI. On Windows kill() ends only the child, at once,
 * so ng.mjs could not pass anything on and `ng build --watch` would keep running: there a `tree` child is ended with
 * everything it started by `taskkill /T /F` (or kill() when taskkill cannot run). Only the build is a tree: on Windows a
 * deskd that desk web started is still desk web's child (`detached` only gives it its own console), and deskd is
 * stopped only through `daemon.stop`, never with desk web. `run` is spawnSync (a spec passes its own).
 */
export function stopChild(child, tree = false, platform = process.platform, run = spawnSync) {
  if (platform === 'win32' && tree && child.pid !== undefined) {
    const result = run('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    if (!result?.error) return;
  }
  child.kill('SIGTERM');
}

/**
 * Runs the commands with this Node until one exits, then stops the others (`stopChild`, with each command's `tree`;
 * `platform` and `run` go to it, for specs). `done` resolves once all have exited: with the first exit code (1 for a
 * signal or a spawn error), or with 0 after `stop()`.
 */
export function runTogether(commands, o = {}) {
  const log = o.log ?? ((line) => console.error(line));
  let stopped = false;
  let first = null;
  const running = commands.map((c) => ({ c, child: spawn(process.execPath, c.args, { cwd: c.cwd, stdio: c.stdio ?? 'inherit', env: process.env }) }));
  const stopAll = () => {
    for (const { c, child } of running) if (child.exitCode === null && child.signalCode === null) stopChild(child, c.tree, o.platform, o.run);
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

if (isMain(import.meta.url)) main(process.argv.slice(2));
