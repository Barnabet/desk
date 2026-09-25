import type { DeskClient } from '@desk/client';
import type { AppSettings, Channel } from './ipc';
import type { GlobalState } from './state';

/**
 * How the host runs deskd: from this repository through tsx (`dev`), the app's bundled deskd under a LaunchAgent
 * (`packaged`), or for desk web (`web`: the desktop app's LaunchAgent through launchctl when it is installed for the same
 * data dir, else the repository's deskd through tsx).
 */
export type DaemonMode = 'dev' | 'packaged' | 'web';

/** What `daemon.status`, `start`, `restart`, `stop` and `repair` report. */
export type DaemonStatus = {
  running: boolean;
  version: string | null;
  pid: number | null;
  uptime_s: number | null;
  proxy: 'up' | 'down' | 'unknown' | null;
  mode: DaemonMode;
  bundledVersion: string;
  /** The running daemon's build id: null from source, undefined from a daemon older than build ids. */
  build: string | null | undefined;
  /** The bundled deskd's build id (Resources/deskd/build-id); null in dev. */
  bundledBuild: string | null;
  agent: 'installed' | 'missing' | 'unsupported';
};

/** What `app.info` reports about the host. `platform` is Node's name for the host OS (`darwin`, `win32`, `linux`). */
export type AppInfo = { version: string; platform: string; packaged: boolean; dataDir: string };

type Out<F extends (...args: any) => any> = Awaited<ReturnType<F>>;
type Client = DeskClient;
type Ok = { ok: true };

/**
 * What each operation resolves with, declared here so browser code can name it without the server. The server's
 * `handlers` must satisfy it, and `handlers.test.ts` checks that every handler returns exactly this type.
 */
export type ChannelOutputs = {
  health: Out<Client['health']>;
  overview: Out<Client['overview']>;
  usage: Out<Client['usage']>;
  'projects.list': Out<Client['projects']['list']>;
  'projects.create': Out<Client['projects']['create']>;
  'projects.get': Out<Client['projects']['get']>;
  'projects.update': Out<Client['projects']['update']>;
  'projects.archive': Out<Client['projects']['archive']>;
  'projects.addSource': Out<Client['projects']['addSource']>;
  'projects.removeSource': Out<Client['projects']['removeSource']>;
  'projects.setSourceWrite': Out<Client['projects']['setSourceWrite']>;
  'projects.send': Out<Client['projects']['send']>;
  'projects.chat': Out<Client['projects']['chat']>;
  'projects.plan': Out<Client['projects']['plan']>;
  'projects.usage': Out<Client['projects']['usage']>;
  'projects.events': Out<Client['projects']['events']>;
  'threads.list': Out<Client['threads']['list']>;
  'threads.get': Out<Client['threads']['get']>;
  'threads.transcript': Out<Client['threads']['transcript']>;
  'threads.send': Out<Client['threads']['send']>;
  'threads.stop': Out<Client['threads']['stop']>;
  'threads.archive': Out<Client['threads']['archive']>;
  'threads.diff': Out<Client['threads']['diff']>;
  'services.logs': Out<Client['services']['logs']>;
  'services.start': Out<Client['services']['start']>;
  'services.stop': Out<Client['services']['stop']>;
  'services.restart': Out<Client['services']['restart']>;
  'threads.files': Out<Client['threads']['files']>;
  'threads.file': Out<Client['threads']['file']>;
  'approvals.list': Out<Client['approvals']['list']>;
  'approvals.resolve': Out<Client['approvals']['resolve']>;
  'attention.list': Out<Client['attention']['list']>;
  'attention.dismiss': Out<Client['attention']['dismiss']>;
  'memory.list': Out<Client['memory']['list']>;
  'memory.add': Out<Client['memory']['add']>;
  'memory.correct': Out<Client['memory']['correct']>;
  'memory.remove': Out<Client['memory']['remove']>;
  'attachments.get': string;
  'library.list': Out<Client['library']['list']>;
  'library.upload': Out<Client['library']['upload']>;
  'library.file': Out<Client['library']['file']>;
  'skills.list': Out<Client['skills']['list']>;
  'skills.get': Out<Client['skills']['get']>;
  'skills.file': Out<Client['skills']['file']>;
  'skills.save': Out<Client['skills']['save']>;
  'skills.remove': Out<Client['skills']['remove']>;
  'skills.history': Out<Client['skills']['history']>;
  'skills.restore': Out<Client['skills']['restore']>;
  'skills.import': Out<Client['skills']['import']>;
  'skills.version': Out<Client['skills']['version']>;
  'skills.versionFile': Out<Client['skills']['versionFile']>;
  'skills.runtimeRetry': Out<Client['catalog']['retryRuntime']>;
  'catalog.list': Out<Client['catalog']['list']>;
  'catalog.prepare': Out<Client['catalog']['prepare']>;
  'catalog.file': Out<Client['catalog']['file']>;
  'catalog.install': Out<Client['catalog']['install']>;
  'models.list': Out<Client['models']['list']>;
  'models.replace': Out<Client['models']['replace']>;
  'config.get': Out<Client['config']['get']>;
  'config.patch': Out<Client['config']['patch']>;
  'config.endpoint': Out<Client['config']['endpoint']>;
  'config.saveEndpoint': Out<Client['config']['saveEndpoint']>;
  'config.testEndpoint': Out<Client['config']['testEndpoint']>;
  'broker.snapshot': GlobalState;
  'broker.watch': Ok;
  'broker.unwatch': Ok;
  'system.runtimes': Out<Client['catalog']['runtimes']>;
  'system.runtimesCleanup': Out<Client['catalog']['cleanupRuntimes']>;
  'daemon.status': DaemonStatus;
  'daemon.start': DaemonStatus;
  'daemon.restart': DaemonStatus;
  'daemon.stop': DaemonStatus;
  'daemon.repair': DaemonStatus;
  'app.info': AppInfo;
  'app.openExternal': Ok;
  'app.pickFolder': string | null;
  'app.revealLogs': Ok;
  'app.openMain': Ok;
  'app.saveFile': boolean;
  'app.settings': AppSettings;
  'app.updateSettings': AppSettings;
};

/** The value an operation resolves with (`call(op, input)` in either UI). */
export type ChannelOutput<C extends Channel> = ChannelOutputs[C];
