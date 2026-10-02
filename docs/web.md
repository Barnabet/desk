# Desk in the browser (`desk web`)

`desk web` serves Desk's second frontend, an Angular app (`apps/web-ui`), to a browser on this computer. It sits alongside the desktop app. Both are clients of the same `deskd`, they show the same projects, threads and screens, and closing the tab never stops the work.

- **Spec:** [`superpowers/specs/2026-09-25-angular-web-ui-design.md`](superpowers/specs/2026-09-25-angular-web-ui-design.md)
- **Plan:** 17 in [`superpowers/plans/`](superpowers/plans/)
- **The desktop app it mirrors:** [`desktop.md`](desktop.md)
- **API behind both:** [`api.md`](api.md). desk web calls the same routes as the desktop app's main process.

## Start

Build the app first with `pnpm --filter @desk/web-ui build`, or use `pnpm web`.

```sh
bin/desk web                 # http://127.0.0.1:7434; opens the browser with a one-time login link
bin/desk web --port 7500     # another port, remembered in <data>/web-settings.json
bin/desk web --no-open       # print the link without opening it
```

- The link is `http://127.0.0.1:<port>/login?code=…`. It works once, for 2 minutes. It stores a session secret in this browser for this address, then opens the app. Press Enter in the terminal where `desk web` runs for a new link, for example after clearing the browser's data.
- The link desk web opens and the link it prints carry different codes, so using the printed one as well is fine. A link used a second time signs out the browser it signed in, and the terminal prints a warning.
- Only this computer can open it. desk web listens on `127.0.0.1` only, and answers only `http://127.0.0.1:<port>`, not `localhost`.
- One `desk web` runs per data dir (`<data>/web.json`). A second one says so and exits.
- If the port is taken, desk web exits and names `--port`. It never moves to another port, because what the browser keeps (the session, onboarding, drafts, the last project) belongs to the address.
- Ctrl-C stops it. Sessions live only as long as it runs: after a restart, sign in again with a new link.
- deskd: desk web finds it through `daemon.json`, as the CLI does. When deskd is not running, the page shows the offline overlay with **Start Desk**.
  - If the Desk app's LaunchAgent is installed (macOS) and runs deskd for desk web's data dir, Start, Restart and Stop in System go through `launchctl`. Start also restarts that deskd when it stopped answering. desk web never writes or removes the LaunchAgent.
  - Otherwise, for example for desk web on another data dir through `DESK_DATA_DIR`, desk web runs this repository's deskd and stops it by its pid.
  - Repairing the LaunchAgent stays in the desktop app.

## From your phone

A paired phone opens the same web UI over a private network: message any project's Desk, answer its questions and approvals, read reports, threads and the Library. desk web still listens on `127.0.0.1` only. [Tailscale Serve](https://tailscale.com/kb/1312/serve) gives the Mac a private HTTPS address that only your own Tailscale devices can reach, and forwards it to desk web. Never use Tailscale Funnel, which would put it on the public internet.

### Set up

On the Mac, once:

```sh
desk web --remote-url https://<mac>.<tailnet>.ts.net   # remembered in web-settings.json ('off' removes it)
desk web install                                       # a login item (LaunchAgent dev.desk.web) runs desk web --service
tailscale serve --bg 7434                              # HTTPS on the tailnet → http://127.0.0.1:7434
desk web pair --name "iPhone"                          # prints a QR code: scan it with the phone's camera
```

- The address is the Mac's MagicDNS name. Tailscale Serve needs MagicDNS and HTTPS certificates turned on for the tailnet (Tailscale's admin console, DNS page). The Mac App Store app's CLI is `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
- On the phone: install Tailscale, sign in with the same account, then scan the code. The link works once, for 10 minutes. The phone stays signed in: its secret is in the phone browser's storage for that address, and desk web keeps only a SHA-256 of it in `web-devices.json`.
- For a full-screen app, add the address to the Home Screen from Safari. On an iPhone a Home Screen app keeps its own storage, so it does not share Safari's pairing: open it, and on its **Pair this phone** page type the short code `desk web pair` also prints (`ABCD-EFGH`, any case; the QR code and the short code are one pairing, so use one of them). desk web refuses more than 10 wrong short codes in 10 minutes, from anyone.
- A project's whole history goes to the page when it opens. A phone gets it without the threads' transcripts (their messages, tool calls and results, and usage): it fetches a thread's history with `threads.transcript` when it first shows it, and catches up after a reconnect. Desk's own conversation, the threads' states, questions, reports and approvals, and every live event still come whole. For a phone, desk web also cuts tool calls (also those inside assistant messages) and tool results to their first 500 characters, and compaction summaries to 2,000, with a note to open them on the Mac. What agents say and what approvals ask stay whole (`phone-slim.ts`), and `/push` is compressed for everyone. History goes out in frames of at most 256 KB, and a page whose socket drops while a project opens resumes after the last event it received. desk web gzips the app's files and `/rpc` answers (never `/login` or `/pair`, which hold secrets), and the files named by their content are cached for good, so a phone downloads the app once per build.
- `desk web phones` lists paired phones, and `desk web unpair <id>` signs one out at once (open sockets close within 15 seconds). A phone unused for 30 days is signed out.
- With desk web running as a login item there is no terminal, so it prints no login link (the log file would keep it). `desk web login` signs a browser on this Mac in instead. `desk web uninstall` removes the login item.
- The phone reaches Desk while the Mac is awake and on the network. Browser notifications need an open tab, so a phone gets none while Desk is closed.

### What a phone may do

A paired phone may do what the web UI does from the couch, and nothing that changes what agents may do. `phone-policy.ts` holds the allow list, so an operation added later is refused on phones (403 `not_on_phone`) until it is listed.

| A phone may | Only the Mac may |
|---|---|
| Read projects, the conversation, threads, their transcripts, diffs and files, service logs, the Library, memory, skills and the catalog, automations and their runs | Create, change or archive projects; change policy, settings or sources (and their write access) |
| Message Desk and threads, stop or archive a thread, stop a service | Start or restart services |
| Answer approvals one at a time (never *remember*) and questions, dismiss attention items | Change the model endpoint, models or config; turn built-in skills on or off; install, import, save or restore skills |
| Upload to the Library; add, correct or remove memory | Create, edit, turn on or grant automations |
| Run automations, answer their steps, cancel runs, stop steps | Start, restart or stop deskd; browse the Mac's folders; change desk web's settings; pair phones |

Approving from the phone runs the action on the Mac, as approving on the Mac does, so treat a paired phone like the Mac's keyboard: pair only your own, and unpair a lost one.

## What differs from the desktop app

The screens, their text and their look are the same. Both UIs load `@desk/ui-styles` and share their logic through `@desk/ui-core` (see the desktop app's [screens](desktop.md#screens)). What a browser does differently:

| | Desktop app | Browser |
|---|---|---|
| Choosing a folder | The system dialog | Desk's folder browser: folders under your home folder or a project's sources, hidden ones on request (on by default for a skill import from `~/.claude/skills`), or a typed path, which deskd checks |
| Choosing a file (an automation's file input) | The system dialog | The same folder browser, listing files as well: click one or type a full path |
| Design's graph (Automations) | React Flow | Desk's own canvas with the same look and keys: drag a step, drag from a handle to connect, drag the background to pan, wheel or the buttons to zoom, Delete or Backspace removes the selection |
| Links in agent text | The system browser, after a confirmation | A new tab, after the same confirmation (`http`, `https` and `mailto` only) |
| Agent files | Previews from the daemon's bytes | The same, except SVG, which shows as text. Images come from raster formats only. Save a copy downloads the file |
| Notifications | macOS notifications from the app | Browser notifications (below) |
| Appearance | System, Light or Dark (System → Appearance) | The browser follows the system's light or dark setting. System → Appearance says so, and its choices are disabled |
| System → deskd | Start, Restart, Stop, Repair LaunchAgent | Start, Restart, Stop. No Repair and no Install LaunchAgent: desk web answers `daemon.repair` with `not_offered` |
| The tray (menu bar popover), native menus, launch at login | Yes | No |
| Shortcuts | ⌘K, ⌘P, ⌘1 to ⌘4, ⌘N, and Attention's keys | ⌘K / Ctrl-K (palette), ⌘P / Ctrl-P (project switcher), and Attention's J/K, ⌘⏎, ⌘⌫ and E. ⌘1 to ⌘4 and ⌘N stay with the browser, which uses them for tabs and windows |

## Notifications

- **System → Notifications → From the app** is desk web's switch, saved in `<data>/web-settings.json`. Turning it on asks the browser for permission. The switch saves without waiting for the answer. If the browser has not allowed Desk's notifications yet, System says so and offers **Allow notifications**. If the browser blocks them, System says to allow them in the browser's site settings for `http://127.0.0.1:<port>`.
- A tab shows new attention items (up to three at a time, as in the desktop app) while it is in the background: approvals, questions, hand-offs, and stalled, failed or paused work. A tab with focus stays quiet, as a focused desktop window does. Each notification is tagged with its item, so several open tabs show it once. Clicking it opens the item in Attention.
- **From deskd when the app is closed** is deskd's own switch. deskd stays quiet only while desk web can notify you: the switch above is on and a connected tab has the browser's permission. Otherwise deskd posts its own notifications, so you get each one once.

## Files desk web keeps

All in the Desk data dir, which agents cannot write outside their workspace.

| File | What it holds |
|---|---|
| `web.json` | The running desk web's pid and port (mode 0600, no secrets). A second `desk web` reads it and exits, and deskd's sandbox guard reads the port from it. desk web removes it when it stops. |
| `web-settings.json` | The port chosen with `--port`, the "From the app" notifications switch, and the phones' address (`--remote-url`). |
| `web-login-<random>.html` | A page that redirects to a one-time login link (mode 0600). desk web opens this file instead of the link, so the code never appears on a command line. It is deleted once the code is used or expires. Agents cannot read these files. |
| `web-devices.json` | Paired phones: an id, a name, when each was paired and last used, and a SHA-256 of its secret (mode 0600). Agents cannot read it. |
| `web-code-<random>.json` | A one-time code `desk web pair` or `desk web login` issued, as a SHA-256 (mode 0600). Deleted once used or found expired. Agents cannot read these files. |
| `logs/web.log` | desk web's log. |

Sessions and login codes live in desk web's memory only, except paired phones and the CLI's one-time codes above. The browser keeps its session secret in `localStorage` for `http://127.0.0.1:<port>`, a phone for the remote address.

## Architecture

```
browser (Angular app)  ── http://127.0.0.1:<port> ──►  desk web (Node)  ── bearer token ──►  deskd (127.0.0.1)
   DeskBridge.call(op)   POST /rpc/<op>   (x-desk-session)          @desk/bff handlers           /v1/* REST
   DeskBridge.onPush(ch) WebSocket /push  (secret in 1st frame)     @desk/bff broker             /v1/stream WS
```

- **`packages/bff` (`@desk/bff`)**: the backend-for-frontend both hosts run. `@desk/bff/contract` (operation schemas, push channels, `GlobalState`) is the only part of `@desk/bff` that browser code imports. `@desk/bff/server` holds the broker, the handlers and `DaemonManager`.
- **`packages/ui-core` and `packages/ui-styles`**: the UI logic and the CSS both UIs share.
- **`apps/web-server` (`@desk/web-server`)**: the `desk web` host. Hono on `127.0.0.1` serves:
  - the Angular build (`apps/web-ui/dist/browser`);
  - `/login`;
  - `POST /rpc/:op`: each input is validated by its schema, and `Uint8Array`s travel as `{ "$bytes": base64 }`;
  - the `/push` WebSocket: one broker sender per socket, and a project watch is acknowledged after its backfill;
  - `/healthz`.

  It also supplies the web `HandlerContext` (`web-context.ts`).
- **`apps/web-ui` (`@desk/web-ui`)**: Angular 22, zoneless, with standalone components and signals.
  - There is no Angular Router: it uses the desktop's hash routes (`#/map`, `#/p/<id>/conversation`, …).
  - There is one component per React component, with the same DOM and class names.
  - `DeskBridge` is its only way out. Operations go to `/rpc`, and pushes come from `/push`. Opening a link, saving a file and picking a folder happen in the browser.

### Security rules desk web keeps

- The deskd token never leaves desk web. The browser gets the same operation results the desktop renderer gets, which hold no token.
- There is no cookie. The session secret lives in this origin's `localStorage`. It travels as the `x-desk-session` header on `/rpc` and as `/push`'s first frame, and desk web compares it in constant time. A missing or wrong secret gets 401 on `/rpc` and closes `/push` with 4401. The page then shows "Open Desk from your terminal".
- Host check: every request, the WebSocket upgrade included, must carry `Host: 127.0.0.1:<port>`, or the remote address's host when one is set. Anything else gets 421, which blocks DNS rebinding.
- Origin check: `/rpc` and the `/push` upgrade also need `Origin: http://127.0.0.1:<port>` (or the remote address). Anything else gets 403.
- `POST /pair` takes only a short code (Origin checked like `/rpc`), at most 10 wrong ones per 10 minutes across all callers.
- A paired phone's secret is checked against `web-devices.json` on every request and every 15 seconds on `/push`, and its operations against the phone allow list. Sandboxed agents can reach the remote address through Tailscale, like any network address, but hold no secret for it and cannot read the files that would give one. `/rpc` also takes only `application/json` (415 otherwise), up to 40 MB (413 above).
- Every HTTP response carries strict headers: a CSP with `script-src 'self'` and `frame-ancestors 'none'`, `nosniff`, `no-referrer`, and same-origin opener and resource policies. The built app has no inline script.
- Agent text never becomes HTML. `SafeMarkdown` renders `marked`'s tokens with Angular templates, and `apps/web-ui` has no `innerHTML` (`security.spec.ts` fails on it).
- Sandboxed agents cannot read desk web's one-time login files, and cannot connect to its port. deskd reads the port from `web.json` each time it builds a sandbox profile, so a command or service started before desk web took its port can reach it until it restarts. desk web still asks it for a session.

## Development

```sh
pnpm web                          # ng build --watch plus desk web --dev (reloads the page after each rebuild); extra args go to desk web
pnpm --filter @desk/web-ui build  # apps/web-ui/dist/browser, which desk web serves
pnpm --filter @desk/web-ui test   # the web UI's specs (Vitest through the Angular CLI, jsdom); pnpm test runs them after the root suite
pnpm typecheck                    # includes ngc for apps/web-ui and tsc for apps/web-ui/e2e
pnpm test:web-e2e                 # builds the UI, checks its index.html, then Playwright/Chromium against an in-process deskd and desk web
```

`apps/web-ui` needs Node `^22.22.3 || ^24.15.0 || >=26` and brings its own TypeScript 6. The rest of the repository stays on its Node and TypeScript 7. Every Angular command goes through `scripts/ng.mjs`, which picks a Node that fits from nvm when the current one is older (`node scripts/ng.mjs --which` prints it). To run one spec: `(cd apps/web-ui && node ../../scripts/ng.mjs test --watch=false --include <spec>)`.

### The parity guard

A UI feature ships in both UIs. `apps/web-ui/src/app/parity.spec.ts` reads the operation names both UIs pass to `call(…)`, leaving comments and tests out. It fails when:
- the React renderer calls an operation the web UI does not (only `app.openMain` and `daemon.repair` are desktop-only);
- either UI passes an operation name it cannot read, such as a name built at run time, a variable, or type arguments nested more than one level;
- a desk-web-only operation (`webChannels`) goes unused;
- a route other than the tray has no web screen.

So call every operation by its literal name in both UIs: `call('daemon.stop', {})`, never a template string.

### End-to-end tests

The suite lives in `apps/web-ui/e2e`, on `startWebE2E` in `harness.ts`. Each file starts a real deskd on the fake model, inside the test process, and desk web in front of it. Both get a temporary home and data dir, so the real ones are never touched.

| File | Covers |
|---|---|
| `smoke.e2e.test.ts` | Signing in with a one-time link, deskd, the endpoint test, onboarding with the folder browser, the map; a link used twice signs the browser out |
| `flows.e2e.test.ts` | Brief → threads fork → Desk's question → approval in Attention → report; messages between threads on their lanes and in the pair sheet; the Threads screen, a thread on a git source (route, diff, files) |
| `knowledge.e2e.test.ts` | Library upload and preview, memory, a source from the folder browser, settings and the policy, archive |
| `catalog.e2e.test.ts` | The shipped catalog with two local stand-ins: review, install with its runtime, the skill on the map, a project install; refine and restore a skill; import from `~/.claude/skills` through the folder browser; turn a built-in skill off and duplicate one |
| `system.e2e.test.ts` | The model registry editor (a duplicate refused, a new model saved and reloaded, a draft discarded); deskd without Repair; the notification switches; ⌘K and ⌘P |
| `automations.e2e.test.ts` | A Blank automation built on the canvas (an input, an agent step, an Ask me step), saved, run with an input, answered in the run view, its file in the Library; a file input chosen in the folder browser |

`pnpm test:web-e2e` also runs `apps/web-server/src/built-ui.e2e.test.ts`, which checks that the built `index.html` has no inline code. The files run one at a time. To run one file against the current build:

```sh
pnpm --filter @desk/web-ui build
pnpm exec vitest run --config vitest.web-e2e.config.ts apps/web-ui/e2e/system.e2e.test.ts
```

The pages run in Playwright's Chromium. By default that is its headless shell, the lighter one. `system.e2e.test.ts` opts into the full Chromium in headless mode (`startWebE2E({ chromium: 'full' })`), because the shell reports every notification permission as denied, even a granted one. Both come from Playwright's own install.

Set `DESK_E2E_SHOTS=<dir>` to save screenshots (`web-<name>.png`).

## Troubleshooting

- **"Open Desk from your terminal":** this browser has no valid session. It may be a new browser, its data was cleared, desk web restarted, or a login link was used twice. Press Enter where `desk web` runs for a new link, or start it again with `desk web`.
- **"Reconnecting to desk web":** the page lost desk web. It retries by itself. If desk web was stopped, run it again and sign in with the new link.
- **"Desk isn’t running" overlay:** use Start Desk, or run `desk up`. deskd's logs are in `<data dir>/logs/` (System → Reveal logs opens the folder), next to desk web's `web.log`.
- **The port is taken:** `desk web --port <n>`. The browser keeps a separate session, onboarding and drafts per port.
- **No notifications:** check System → Notifications, the browser's permission for `http://127.0.0.1:<port>`, and that the Desk tab is in the background.
