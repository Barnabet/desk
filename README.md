# Desk

Local-first project coordinator: a per-project **Desk** agent scopes work, dispatches parallel **threads**, reviews and assembles results.

- Spec: `docs/superpowers/specs/2026-09-23-desk-daemon-design.md`
- Plans: `docs/superpowers/plans/`

## Development

    pnpm install
    pnpm test          # unit + integration (fake model server)
    pnpm test:live     # live smoke tests against the local CLIProxyAPI
    pnpm typecheck
