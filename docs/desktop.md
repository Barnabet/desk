# Desk desktop app

The desktop app (`apps/desktop`) is an Electron + React client for `deskd`. It uses design direction C, "Orbit map, refined". It is a client only: every piece of state lives in the daemon, and closing or quitting the app never stops the work.

- **Spec:** [`superpowers/specs/2026-09-24-desk-desktop-app-design.md`](superpowers/specs/2026-09-24-desk-desktop-app-design.md)
- **Plans:** 7 to 12 in [`superpowers/plans/`](superpowers/plans/)
- **API it consumes:** [`api.md`](api.md)

## Install

1. Build the release with `pnpm package:desktop`. It writes the following to `apps/desktop/release/`:
   - `Desk-<version>-<arch>.dmg`
   - `Desk-<version>-<arch>.zip`
2. Open the `.dmg` and drag Desk to Applications.
3. The build is ad-hoc signed and not notarised, so the first launch needs **right-click → Open**. If you launch it from anywhere else, Desk offers to move itself to Applications. It should live there because its background service runs from the app bundle.
4. Onboarding:
   1. **deskd.** Desk finds a running daemon. If none is running, it installs the bundled one as a LaunchAgent (`dev.desk.deskd`) and starts it.
   2. **Model endpoint.** Desk detects `DESK_OPENAI_*`, `~/.config/cliproxyapi.env` or a stored endpoint. If none is found, you enter a base URL and key. The key goes to the macOS Keychain, then you test the connection.
   3. **First project.**
   4. **The Map.**

The packaged daemon is `Desk.app/Contents/Resources/deskd/deskd.mjs`, run by the app's own binary with `ELECTRON_RUN_AS_NODE=1`. It ships with its migrations and the N-API `better-sqlite3` prebuild, so it needs no separate Node install.
- When a newer app finds an older daemon, it rewrites the LaunchAgent and restarts the daemon.
- **System → Repair LaunchAgent** does the same on demand.

The CLI's `desk up --install` uses the same LaunchAgent label, so there is only ever one deskd.

## Screens

| Place | What it does |
|---|---|
| **Map** (home) | Each project is a territory, sized by activity. Desk nodes have their threads in orbit, needs-you pins sit on the rim and deskd is the sun. The inspector shows the report headline, the plan, the threads and what needs you. There is a List view and a New project sheet with "More options". |
| **Conversation** | The line diagram: the trunk is Desk, threads fork and rejoin, stations link to chat items, and there is a live "now" marker. The chat shows Desk's replies (streamed), the thread feed, report and question cards, and notices. The composer can attach files to the Library or turn a message into a skill. A plan route panel sits alongside. |
| **Threads** | A roster of cards. Each thread has a serpentine route with numbered stops, synced to a Narrative or Every-step transcript. You can steer, stop or archive a thread, or turn it into a skill. Tabs: Result, Diff, Files, Skill drafts, Usage. |
| **Attention** | A flight-strip rack in four bays: clearance, queries, handoffs and holding. The inspector shows the exact arguments, who is asking and where, the policy reason and the thread's last words. Keys: J/K, ⌘⏎ approve, ⌘⌫ deny, E open. |
| **Skills** | A map and a list covering global, project and shadowed skills, with live usage. The detail view has instructions, files, history (compare, restore, change notes), an editor with uploads, import, delete, and Ask Desk. |
| **Library** | A grid with a safe preview (Markdown, text, code and images from the daemon). It shows each file's origin, supports drag-and-drop upload, and has Save a copy. |
| **Memory** | Entries grouped by kind, with search. You can add, correct (with the supersession chain shown) and delete. |
| **Settings** | About, sources, working style (check-ins, autonomy, review rounds, models, slots), the ordered policy editor with reset, and archive. |
| **System** | deskd start/restart/stop/repair and the proxy state; the model endpoint; notifications; the data directory and logs; the model registry editor; usage by model and project; and system notices. |
| **Menu bar** | A count badge and a popover with a mini line diagram of today. The strips let you approve or deny in place. The popover also shows what's running and has Open Desk. |

Global shortcuts: ⌘K (palette: places, projects, threads, skills, library titles, and memory search across projects), ⌘P (project switcher), and ⌘1 to ⌘4 (Map, Attention, Skills, System).

## Architecture

```
renderer (React, sandboxed, no Node)          main process (Node)                        deskd
  window.desk.invoke(channel, payload) ──IPC──▶ zod-validate ─▶ handler ─▶ DeskClient ──HTTP──▶ /v1/*
  window.desk.on('desk:…')            ◀─push── Broker (one WebSocket, all projects) ◀──WS─── /v1/stream
```

- **`src/main`**
  - `index.ts`: app lifecycle and the single-instance lock.
  - `daemon.ts`: `DaemonManager` (start, restart, stop, repair; launchd when packaged, `tsx` in dev).
  - `broker.ts`: the only stream subscriber. It fans out per-window watches, resumes from the last sequence and backs off with jitter.
  - `handlers.ts`: one handler per IPC channel.
  - `tray.ts`, `popover.ts`, `notify.ts`, `menu.ts`, and `windows.ts` (the `desk-app://` scheme, CSP, navigation lock).
- **`src/shared/ipc.ts`**: every channel's zod input schema. `handlers.test.ts` checks that every channel has a handler.
- **`src/preload`**: exposes only `invoke`, `on` and `platform`.
- **`src/renderer`**
  - `state/`:
    - `session.ts`: each project's event log, folded into chat, timeline, transcripts and streams.
    - `global.ts`: connection, health, overview, attention and system.
  - A hash router, and one folder per place.

### Security rules the app keeps

- The daemon token never leaves the main process. The renderer cannot reach deskd directly, and every IPC payload is validated in main.
- Agent content is untrusted. `SafeMarkdown` renders no raw HTML, and remote images become links. Links open in the system browser after a confirmation, and only `http`, `https` and `mailto` are allowed. Images come only from daemon bytes, through blob URLs.
- `app.openMain` accepts only `#/…` routes. The main window cannot navigate away from `desk-app://`.
- Nothing resembling a key is displayed. The endpoint panel shows the source and base URL only, and a new key goes to the Keychain through stdin.

## Development

```sh
pnpm desktop            # Vite HMR + Electron; finds or starts the repo daemon (tsx)
pnpm test               # includes the desktop unit and component tests (jsdom)
pnpm test:e2e           # builds the app, then Playwright-for-Electron against a real deskd + fake model
pnpm package:desktop    # release/Desk-<v>-<arch>.dmg and .zip (then `pnpm test:e2e` also runs the packaged test)
pnpm --filter @desk/desktop icon   # regenerate build/icon.png and icon.icns from build/icon.svg
```

The end-to-end suite (`apps/desktop/e2e`):

| File | Covers |
|---|---|
| `smoke.e2e.test.ts` | Onboarding, connecting, the map, and a live tray count |
| `flows.e2e.test.ts` | Brief → threads fork → question → approval in Attention → revision loop → report → steering → tray popover |
| `knowledge.e2e.test.ts` | Refine and restore a skill, library upload, memory correction, settings and policy, System, ⌘K |
| `packaged.e2e.test.ts` | The packaged `Desk.app` from a clean data dir. deskd runs as the LaunchAgent would, but no real LaunchAgent is installed. Skipped if there is no build. |

Set `DESK_E2E_SHOTS=<dir>` to save screenshots of each screen for visual review.

Environment switches:
- `DESK_DATA_DIR`: the daemon data directory.
- `DESK_USER_DATA`: Electron's `userData` directory.
- `DESK_E2E=1`: test hooks, no single-instance lock, a popover that stays open.
- `DESK_NODE`: the Node binary used to start the dev daemon.

## Packaging notes

`scripts/package.mjs` does the following:
1. Builds main, preload and renderer (all bundled, so there are no runtime `node_modules`) and bundles deskd.
2. Stages the app outside the workspace with an empty npm lockfile, so electron-builder collects no dependencies.
3. Lays out `Desk.app` from the local Electron distribution, which avoids any download.
4. Copies deskd with only the `darwin-*` sqlite prebuilds.
5. Signs the bundle ad hoc, then writes the `.dmg` with `hdiutil` and the `.zip` with `ditto`.

Signing with a Developer ID and notarising need your Apple identity. Set `mac.identity` and add a notarise step when you have one.

`--win` produces an NSIS installer layout. It is untested, because deskd itself is macOS-only for now (it depends on `sandbox-exec` and launchd).

## Troubleshooting

- **"Desk is not running" overlay:** use Start, or System → Restart. Logs are in `<data dir>/logs/` (System → Reveal logs) and the app's own `desktop.log` is in Electron's logs folder.
- **Agents paused:** the model proxy is down. Work resumes by itself, and System → Model endpoint → Test connection checks it.
- **"This deskd is vX; the app bundles vY" in System:** the running deskd is older than the app bundle. The app refreshes it at launch, and System → Repair LaunchAgent does the same on demand.
