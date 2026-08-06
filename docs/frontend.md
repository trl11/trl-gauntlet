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

A file that exports a component is `PascalCase.tsx`. Everything else is
`snake_case`, wherever it sits — which is why `components/run_columns.tsx`,
column specs and cell renderers belonging to `RunTable`, is spelled the way
`utils/run_history.ts` is. Hooks keep React's own `useThing.ts`.

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

## The kit's design-system skill

The submodule carries a coding-agent skill at
`extras/trl-ui-kit/.claude/skills/trl11-frontend`, describing the design system
and the component API. Tooling scopes a skill to the directory it is found in,
which would be the one directory the rules never apply to, so
`.claude/skills/trl11-frontend` symlinks it into this repository and work under
`frontend/` picks it up.

Its `references/styling.md` and `references/components.md` are authoritative
here: the colour tokens, the typography scale, the spacing constants, the
`filter: brightness()` hover and disabled states, kebab-case with BEM
modifiers, and the props of every component the kit exports.

The skill was written against a different application, and six of its rules
describe that one rather than this one.

| The skill says | Gauntlet |
|---|---|
| `@trl11/ui-kit/...` imports | `@trl11/...`, per the alias table above |
| `@utils/backend`, `@utils/toast` | `@api/client`, the only module that calls `fetch` |
| `components/media` players | unresolvable here, along with `components/vip` |
| MQTT subscriptions invalidate queries | polling; there is no MQTT dependency |
| `@tanstack/react-router`, `routeTree.gen.ts` | `react-router`, `HashRouter`, no generated tree |
| A 46px icon sidebar, and pages offset for it | a fixed top tab bar, and no sidebar |

Correct a divergence here rather than in the submodule, which this repository
only ever consumes.

### Sharp corners

The skill states the rule as never rounding a corner. The kit reads it more
precisely, and the distinction is what the shape is standing for.

Anything that is a surface has square corners: the panels a page is built
from, the cards inside them, buttons, text fields and dropdowns, tabs, badges,
table cells, dialogs and the bar across the top. Nothing is softened, and
softening one is the easiest way to make a screen stop matching the rest of
the application. The look is milled — a face cut from a sheet, edges left as
they came off the tool — rather than the rounded cards of a web dashboard.

Roundness is reserved for depicting something that is round on real
equipment: the status light beside an instrument's name, the lamp on a power
key, the cap of a rotary knob, the dot marking a run's state, the ring around
a unit of measurement. These read as fittings mounted on the panel, and their
roundness is what separates them from it. A circle in the interface should
always be a thing you could point at on a bench; if it is a container for
content, it is a rectangle.

## Running against a live API

```bash
make run           # terminal 1: builds the bundle and serves the API on :7100
make frontend-dev  # terminal 2: Vite on :7101, proxying /api to :7100
```

`make frontend-dev` runs `npm run dev`. The proxy is in `vite.config.ts` and
forwards `/api`, including the SSE stream, to `http://127.0.0.1:7100`. That
target is fixed in the config; for an API on another host or port, set
`VITE_API_BASE` instead of using the proxy.

`API_BASE` is resolved once in `src/api/client.ts` and prefixes every request,
the event-stream URL and every artifact link. It takes the first of:

| Source | Set by | Known at |
|---|---|---|
| `window.gauntlet.apiBase` | the Electron preload script | runtime |
| `VITE_API_BASE` | the build | build time |
| `""`, meaning same origin | the default | — |

```bash
cd frontend && VITE_API_BASE=http://bench-01:7100 npm run dev
```

The runtime source exists because the Electron app spawns its own backend on a
port chosen when it starts, so no build-time value could name it. In a browser
`window.gauntlet` is undefined and the build-time value decides.

The desktop shell loads the backend's own URL, so there the base is the origin
the page came from and the choice costs nothing. It matters wherever the bundle
is served by something other than the API: a dev server proxying elsewhere, or
a shell pointed at a Gauntlet process on another machine. Hash routing is there
for the same reason. Nothing in `frontend/` may assume it is served from the
API's origin, and no route may depend on the server resolving a path.

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
| A provider's `readouts()` | What the display lights, in the groups and to the precision it asked for. |
| A provider's `commands()` | One form per command, its inputs built from that command's `fields[]`. |

`InstrumentPanel` is the only instrument component, and it is rendered for every
instrument. A new instrument gets a working panel by being registered.

The panel is drawn as a bench instrument's front face. Declared readouts light a
`SevenSegment` display, cycling green, red and amber in the order the provider
declared them, and a reading seven bars cannot spell falls back to plain text. A
command field that declares both a minimum and a maximum gets a `Knob` beside
its entry, turned by dragging, by clicking a point on it, or by the arrow keys;
`utils/dial.ts` is the angle arithmetic behind it. None of this is chosen per
instrument: position and declared range are all either component reads.

A primary command that settles one boolean, and otherwise only picks what to
settle it for, becomes a latching key: pressing it sends the opposite of what
it last sent. A lock beside it has to be released first, and stays where the
operator leaves it. `GET /api/instruments` reports `in_use_by`, the run whose
suite declared it `requires` that capability, and while a run holds an
instrument its lock is shut and cannot be released.

A `grep` for a suite key or an instrument name under `frontend/src/` should
return nothing but test fixtures.

## Two vocabularies

The UI speaks to the operator, so it does not always use the contract's word.
A **test** on screen is a suite in the manifest, and an **instrument** is a
capability provider. Both are deliberate. The contract's words stay in
`api/types.ts` and in every payload, so `suite`, `requires` and `capability`
are what the code handles even where the label says otherwise.

Read a page in `pages/` and a component in `components/` by its route, not by
its heading: `TestsPage` serves `/tests` and renders `GET /api/suites`.

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
