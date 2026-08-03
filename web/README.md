# gauntlet-web

The operator UI. React 19, TypeScript, Vite, react-router with `HashRouter`,
TanStack Query, recharts, and the trl-ui-kit components. Dark theme only.

Conventions, the manifest-driven rule, and how to add a page are in
[`../docs/frontend.md`](../docs/frontend.md).

## Scripts

| Script                 | Does                                                     |
| ---------------------- | -------------------------------------------------------- |
| `npm run dev`          | Vite on 7101, proxying `/api` to `http://127.0.0.1:7100` |
| `npm run build`        | `tsc --noEmit`, then the production bundle               |
| `npm run test`         | vitest, one pass                                         |
| `npm run test:watch`   | vitest, watching                                         |
| `npm run lint`         | eslint, then `tsc --noEmit`                              |
| `npm run format`       | prettier, writing in place                               |
| `npm run format-check` | prettier, reporting rather than writing                  |
| `npm run preview`      | serve the built bundle                                   |

From the repository root, `make web` runs the build, `make web-dev` the dev
server, and `make web-check` the format check, the lint and the tests. Each
installs `node_modules` first if it is missing.

## Where the bundle goes

`npm run build` writes to `../packages/gauntlet/src/gauntlet/web_dist/`, which
is git-ignored package data. The application serves it at `/`; without it, `/`
returns a placeholder linking to `/docs`.

## Aliases

`@api`, `@assets`, `@components`, `@hooks`, `@pages` and `@styles` point into
`src/`. `@trl11` points at `../extras/trl-ui-kit`, a git submodule consumed as
source — its own `package.json` is never installed, so the dependencies its
files import (react, react-dom, clsx, `@fortawesome/*`) are declared here.
Import only from `@trl11/components/ui` and `@trl11/hooks`.

Every alias is declared twice, in `vite.config.ts` and in `tsconfig.json`. They
must agree.

## Pointing at a remote API

`VITE_API_BASE` prefixes every request, the SSE stream and every artifact link.
Empty, the default, means the origin the page was served from.

```bash
VITE_API_BASE=http://bench-01:7100 npm run dev
VITE_API_BASE=http://bench-01:7100 npm run build
```

The dev-server proxy in `vite.config.ts` only covers `127.0.0.1:7100`; use
`VITE_API_BASE` for anything else. It and the hash router are what let the same
bundle run from `file://` inside a future Electron shell.
