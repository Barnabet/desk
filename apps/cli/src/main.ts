import { runCli } from './commands';

process.exitCode = await runCli(process.argv.slice(2), { out: (s) => process.stdout.write(s), err: (s) => process.stderr.write(s) });
