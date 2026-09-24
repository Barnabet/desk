import { createInterface } from 'node:readline/promises';
import { runCli } from './commands';

/** A y/N question on the terminal; absent when stdin is not one (scripts must pass --yes). */
const confirm = process.stdin.isTTY
  ? async (question: string) => {
      const rl = createInterface({ input: process.stdin, output: process.stdout });
      try {
        return /^y(es)?$/i.test((await rl.question(`${question} [y/N] `)).trim());
      } finally {
        rl.close();
      }
    }
  : undefined;

process.exitCode = await runCli(process.argv.slice(2), { out: (s) => process.stdout.write(s), err: (s) => process.stderr.write(s), ...(confirm ? { confirm } : {}) });
