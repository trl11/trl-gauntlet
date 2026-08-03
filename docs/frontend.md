# Frontend

`frontend/` is the operator UI. React 19, TypeScript, Vite, react-router v7 with
`HashRouter`, TanStack Query for server state, recharts for plots, and the
trl-ui-kit component library consumed as source. Styling is SCSS against the
kit's design tokens. Dark theme only.

Vite writes the bundle to `packages/gauntlet/src/gauntlet/web_dist/`, which the
application serves at `/`. See [`architecture.md`](architecture.md) for how the
two halves fit together.

## Layout

```
frontend/
├── package.json          the app declares every dependency, including the kit's
├── vite.config.ts        aliases, dev proxy, build output, vitest config
├── tsconfig.json         the same aliases for the type checker
├── eslint.config.js
├── public/               favicon and the fonts copied from the kit
└── src/
    ├── index.html        Vite entry; `root` is `src/`
    ├── main.tsx          mounts App into #root
    ├── App.tsx           QueryClient, ErrorBoundary, HashRouter, routes
    ├── api/
    │   ├── client.ts     the only module that calls fetch
    │   └── types.ts      the API vocabulary; one interface per response
    ├── components/       shared components, one X.tsx + X.scss per component
    ├── hooks/            useEventStream, useGlobalShortcuts
    ├── pages/            one per route
    ├── styles/           main.scss (fonts, resets), _chart.scss (mixins)
    ├── test/             vitest setup and captured API fixtures
    └── utils/            pure helpers: format, overrides, run_history, …
```

## Aliases

Declared in both `vite.config.ts` and `tsconfig.json`, and they must agree.

| Alias | Resolves to |
|---|---|
| `@api` | `frontend/src/api` |
| `@assets` | `frontend/src/assets` |
| `@components` | `frontend/src/components` |
| `@hooks` | `frontend/src/hooks` |
| `@pages` | `frontend/src/pages` |
| `@styles` | `frontend/src/styles` |
| `@trl11` | `extras/trl-ui-kit` |

## The ui-kit submodule

`extras/trl-ui-kit` is a git submodule. It is consumed as **source**: its own
`package.json` is never installed, so `frontend/package.json` declares every
dependency the kit's files import — react, react-dom, clsx and the
`@fortawesome/*` packages. `vite.config.ts` lists those in `resolve.dedupe` and
`tsconfig.json` maps their bare specifiers into `frontend/node_modules`, so the
kit's imports resolve to this app's single copy.

```bash
git submodule update --init            # first checkout
git submodule update --remote          # move to the tip of the tracked branch
git submodule update --init --force    # back to the commit the parent records
```

`--remote` leaves `extras/trl-ui-kit` modified in the parent repository: the new
commit has to be committed there for anyone else to get it. Run
`make frontend-check` before doing so.

Import only from `@trl11/components/ui` and `@trl11/hooks`.
`@trl11/components/vip` and `@trl11/components/media` pull three.js, satellite.js
and a `file:` dependency that does not resolve here.

Each component's props are in its own `.tsx`, with usage in the `.stories.tsx`
beside it. Read those rather than guessing.

## Styling

One `X.scss` beside each `X.tsx`, imported with `import "./X.scss"`. Every
file that uses a token starts with:

```scss
@use "@trl11/styles/theme.scss" as *;
```

Colours, fonts and spacing come from that file — `$trl-background`, `$trl-dark`,
`$trl-gray`, `$trl-blue`, `$trl-red`, `$trl-green`, `$trl-amber`,
`$font-primary`, `$font-mono`, `$navbar-height` and the `$indicator-*` set. Do
not write hex values.

Fonts are copied from `extras/trl-ui-kit/fonts` into `frontend/public/font` and
declared with `@font-face` in `src/styles/main.scss`.

## Running against a live API

```bash
make run           # terminal 1: builds the bundle and serves the API on :7100
make frontend-dev  # terminal 2: Vite on :7101, proxying /api to :7100
```

`make frontend-dev` runs `npm run dev`. The proxy is in `vite.config.ts` and
forwards `/api`, including the SSE stream, to `http://127.0.0.1:7100`. That
target is fixed in the config; for an API on another host or port, set
`VITE_API_BASE` instead of using the proxy.

`VITE_API_BASE` is read in `src/api/client.ts` and prefixes every request, the
event-stream URL and every artifact link. Empty, the default, means same origin.

```bash
cd frontend && VITE_API_BASE=http://bench-01:7100 npm run dev
```

Hash routing and that configurable base are both there so the same bundle can be
loaded from `file://` inside an Electron shell that talks to a Gauntlet process
elsewhere. Nothing in `frontend/` may assume it is served from the API's
origin, and no route may depend on the server resolving a path.

## The UI is generic over the manifest

The frontend renders whatever a suite declares. It must never branch on a suite
key, nor on an instrument name.

| Declared in | Rendered as |
|---|---|
| `overrides[]` | The run form. Each entry's `type`, `label`, `unit`, `choices`, `minimum` and `maximum` build and bound one control. `frontend/src/utils/overrides.ts` reads only those fields. |
| `produces[]` | Which result views a suite offers. |
| `requires[]` | The capabilities checked against `GET /api/instruments` before the run button is enabled. |
| `profile-schema` | The profile editor form, built by `SchemaForm` from the JSON Schema at `GET /api/suites/{key}/profile-schema`. |
| A provider's `state()` | The rows of its instrument panel. |
| A provider's `commands()` | One form per command, its inputs built from that command's `fields[]`. |

`InstrumentPanel` is the only instrument component, and it is rendered for every
instrument. A new capability provider gets a working panel by declaring one.

A `grep` for a suite key or an instrument name under `frontend/src/` should
return nothing but test fixtures.

## Adding a page

1. Write `src/pages/ThingPage.tsx` and `src/pages/ThingPage.scss`. Open with
   `<PageHeader title=… subtitle=… />`.
2. Add the endpoint function to `src/api/client.ts` and its response interface
   to `src/api/types.ts`. `client.ts` is the only module that calls `fetch`.
3. Read it with `useQuery`; write with `useMutation` and invalidate the query
   key you read.
4. Register the route in `src/App.tsx`.
5. Add an entry to `NAV` in `src/components/Layout.tsx` if it is a top-level
   page, and to `DESTINATIONS` and `SHORTCUTS` in
   `src/hooks/useGlobalShortcuts.ts` for its `g`-prefixed shortcut.
6. Write `src/pages/ThingPage.test.tsx`. Tests use vitest, jsdom and
   Testing Library, and mock `@api/client`; the captured responses in
   `src/test/fixtures.ts` are typed against `api/types.ts`, so a fixture that
   drifts from the API fails `tsc`.

## Checks

```bash
make frontend-test   # vitest
make frontend-check  # prettier --check, eslint, tsc --noEmit, vitest
make frontend        # production build into gauntlet/web_dist
```

`make check` runs `make frontend-check`. Run it directly before committing
anything under `frontend/`.
