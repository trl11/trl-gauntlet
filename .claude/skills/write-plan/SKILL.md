---
name: write-plan
description: "Write an implementation plan in docs/plans/ that another agent can execute end to end. Use when the user asks to plan, design, or spec a feature or change before building it, asks for a design doc, or asks to turn an investigation into a work plan. Produces the repo's house plan format: goals, non-goals, current contract, user model, implementation, work packages, test plan, acceptance criteria, risks, delivery order."
---

# Write an implementation plan

Plans live in `docs/plans/<kebab-case-feature>.md`, one file per feature, and are read by an agent
who was not part of the conversation that produced them. That reader has the codebase and nothing
else — no chat history, no ticket, no author to ask. Everything needed to implement, verify, and
know when to stop is in the file.

`references/template.md` is the authority on shape: section order, headings, and the kind of
sentence each section holds. Start from it every time.

## Ask, never guess

A plan states decisions. It contains no "TBD", no options list, no hedged alternative. Anything
unresolved is resolved before the file is written, by one of three routes:

| Kind of unknown | Route |
|---|---|
| Ambiguous — two readings lead to materially different work | Ask the user before writing. Do not pick and proceed. |
| Unknowable without measurement — instrument behaviour, real timings, what a bench actually reports | Make it work package 1, and state the artifact it produces (e.g. "a recorded register map read from the part on the bench"). |
| Out of scope | Write it as a non-goal. |

Ask about anything you cannot verify from the code: intended operator behaviour, acceptable latency
or failure modes, which surfaces the feature appears on, defaults, whether an existing behaviour may
change. Batch the questions and ask them in one pass rather than drip-feeding. If the user has
already decided something, do not re-open it.

## Before writing

1. **Read the code the plan touches.** A plan that names `CapabilityRegistry.claim_for_run` and
   `SuiteManifest.requires` is executable; one that says "the instrument layer" is not. Every type,
   file, field, and UI component named in the plan must exist, or be explicitly introduced by the
   plan. Read the root `CLAUDE.md` first, then the `docs/` page for the area:
   `contract.md` before anything crossing the suite boundary, `frontend.md` before `frontend/`,
   `instruments.md` before `capabilities/` or `instruments/`, `campaigns.md` before `campaigns/`,
   `deploying.md` before `rig/` or `tools/deploy/deploy-bench.sh`. Check any `graphify-out/` graphs
   that exist.
2. **Establish what exists today** so the plan can say what changes. This becomes the current-contract
   section — often the highest-value part of the file.
3. **Walk the boundary checklist** below and decide, for each row, whether the change crosses it.
4. **Ask the questions** the code cannot answer, per the triage above.

### Boundary checklist

The usual way an agent-executed plan comes up short is a missed layer, not bad logic. Walk every
row; the plan says which it crosses, and names the ones it deliberately does not.

| Boundary | Trigger |
|---|---|
| `gauntlet_sdk.contract` | Any change to what a suite declares or writes. Additive optional fields keep `apiVersion: 1`; anything that invalidates a conforming suite increments it |
| API router ↔ `frontend/src/api/types.ts` | Any change to a response shape. The captured fixtures in `frontend/src/test/fixtures.ts` are typed, so a body that drifts fails `tsc` |
| Exact-shape assertions | Tests that assert a whole key set, such as `test_system_api.py` for `/api/system/data`. A new field fails them until they are updated |
| `capabilities/` vs `instruments/` | A protocol or the registry goes in the first, a device in the second. `api/capabilities.py` is what a suite drives, `api/instruments.py` what the operator sees |
| `storage/` | Any persisted column. `RunsIndex.import_tree` rebuilds the index from disk, so a value that cannot be derived from a run directory will not survive a reimport |
| Suite and campaign discovery | New manifest fields are read generically. A suite key or campaign key in `packages/gauntlet/` or `frontend/src/` outside a fixture is a defect |
| `frontend/src/` | Forms and views are built from `overrides`, `produces`, `requires` and the providers' declared `state()`, `commands()` and `readouts()`. An instrument name in the frontend is a defect |
| `frontend/src/api/client.ts` | New requests. It is the only module that calls `fetch`, every path is prefixed with `VITE_API_BASE`, and nothing may assume the API's origin |
| Theme and styling | Colours, fonts and spacing come from `@trl11/styles/theme.scss`. One `X.scss` beside each `X.tsx`, no hex literals. Never edit the `extras/trl-ui-kit` submodule |
| Host telemetry | `gauntlet.api.host_stats` stays standard-library only |
| The release pipeline | A new file that ships to a bench crosses all of `common.mk`, `app/Makefile` host-setup, `ci-validate-dist` in `ci/ci.mk`, the `sent` list in `tools/deploy/deploy-bench.sh`, and `install-service.sh` or `setup-host.sh`. Missing one either fails CI or silently does not reach the bench |
| `docs/` | Always for behaviour a reader would otherwise have to infer. The root `CLAUDE.md` layout block and command table when either changes |

## Sections

Use `references/template.md` as the skeleton. Section order is fixed. Scale the plan to the change:

| Section | When |
|---|---|
| Title + `Status:` line | Always |
| Lede paragraph | Always |
| Goals | Always |
| Non-Goals | Always |
| Current *X* Contract | When behaviour already exists in this area |
| User Model / User-Facing Behaviour | When an operator can see or control the result |
| Architecture / Implementation | Always |
| Design Decisions | When a choice needs its rationale recorded (contracts, ranges, ownership) |
| Work Packages | When the change lands as more than one commit |
| Test Plan | Always |
| Acceptance Criteria | Always |
| Risks and Mitigations | When the change touches instruments, timing, a live bench, or run history |
| Delivery Order | When ordering between layers or the submodules matters |

A small, single-surface change ends at Acceptance Criteria — around 100 lines. Do not pad a small
plan into a large one. Keep any plan under about 500 lines; past that, the change is two plans, so
split it and say how they relate.

## Rules

**Status line.** Second line of the file, always present: `Status: proposed`, `Status: in progress`,
`Status: implemented`, or `Status: implemented and bench-validated`. New plans start at `proposed`.

**Voice.** Present tense, declarative, third person. "The supervisor claims every capability the
manifest requires before the suite process starts" — not "we will claim", "the supervisor should
claim", or "this PR adds". The plan describes the system as it will be, and reads the same before
and after implementation.

**Language.** The plan's reader is an agent that cannot ask what a sentence meant, so ambiguity in
the file becomes wrong code. Run the text through the `asd-ste100` skill before writing the file:

| Part of the plan | Mode |
|---|---|
| Work packages, gates, test plan, acceptance criteria, invariants | Strict — these are instructions, and a misreading changes what gets built |
| Lede, current contract, design decisions, risks | STE-flavored — structural rules in full, lexical rules advisory |

Two of its rules matter more here than anywhere else. Keep every hedge at its original strength: a
plan that promotes "the instrument may reject the command" to "the instrument rejects the command"
has invented a contract. And add no fact the source did not state — a rewrite that reads better
because it supplies a cause, a threshold, or a mechanism has stopped being a rewrite and become a
decision.

**No history, no provenance.** No dates, authors, ticket ids, finding ids, PR numbers, "previously/
now" comparisons, or references to the conversation. Linking to `CLAUDE.md` or a `docs/` page is
fine and useful.

**Name real things.** Symbols, files, fields, endpoints, UI components, make targets. Backtick them.
`CapabilityRegistry.claim_for_run`, `RunsIndex.import_tree`, `GET /api/system/data`,
`rig/homepage/serve-homepage.py`.

**Lead with what is reused.** The lede names the existing path the change extends — discovery, the
supervisor, the capability registry, the runs index, the release pipeline. If nothing is reused, the
plan says why a new path is needed.

**Goals are observable outcomes; non-goals are fences.** Non-goals exist to stop an executing agent
from expanding scope. Write the ones someone would plausibly assume: "Writing to a serializer
register", "A second telemetry implementation outside `host_stats`", "Changing what an existing
profile does". Every non-goal should be a thing a reasonable implementer might otherwise do.

**Invariants over prose.** State the guarantees numerically and absolutely: exactly one run per
`POST /api/runs`, at most one owner of a capability at a time, a run id unique within a second,
values `0` and `1` only. These are what the tests assert.

**Persistence and reimport.** Any plan that adds a persisted field states its default when the
field is absent from an existing run or profile, and whether it survives `RunsIndex.import_tree`
rebuilding the index from disk. A value that cannot be derived from the run directory does not
survive, and storing one anyway is the bug this rule exists to prevent. State the same for anything
written to the data directory, which outlives a redeploy while the unpacked bundle does not.

**Failure semantics.** Every external interaction — instrument command, SSH to a unit under test,
device response, suite process exit — states its failure mode. Bias to failing loud and leaving no
partial artifact: a run without `verdict.json` is recorded as `error`, not `failed`, and an
incomplete download fails the request without writing a partial file.

**Untrusted input.** When the change accepts a URL, path, upload, or device address, the plan states
the validation rule and what it prevents: "a datasheet name is resolved and checked to be a direct
child of the datasheet directory, so neither a traversal nor a symlink out of it is served."

**Tables for matrices.** Enumerable value/label/behaviour sets, instrument mode grids, and risk
pairs go in tables, not bullets. Keep list order meaningful (process flow) or alphabetical.

**Almost no code.** A short fenced `text` block for a loop or sequence is fine. No implementation
snippets — the plan says what holds, the agent writes the code.

**Concrete numbers.** Sample periods, durations, timeouts, channel counts, byte budgets. Vague
magnitudes are unverifiable.

### Write this, not that

| Not this | This |
|---|---|
| test the run lifecycle | assert a run with no `verdict.json` is recorded as `error`, not `failed` |
| the instrument layer handles ownership | `CapabilityRegistry.claim_for_run` owns every requirement not already owned, and disowns only what it owned |
| we should probably validate the path | a datasheet name is resolved and checked to be a direct child of the datasheet directory |
| add appropriate error handling | a refused connection to Gauntlet answers 502 and the page still renders |
| the field should be optional | the field is optional on the response model, so a payload captured before it existed still typechecks |
| update the UI | the panel builds the row from the provider's declared `readouts()`; no component names the instrument |

## Work packages

The part that makes a plan agent-executable. Each numbered package:

- is independently landable and independently verifiable;
- names the files or components it changes;
- lists its steps in the order they must happen, numbered when order is load-bearing;
- ends with an exit gate stated as a runnable command.

Order packages so the lower layer lands before its consumer (contract → storage → supervisor or
instrument → API → frontend → docs). Say explicitly what keeps intermediate commits safe: which
field stays optional, which profile stays unreferenced, what remains invisible to the operator until
the final package passes.

### Gates

Write gates as commands the executing agent can run, not as intentions:

| Gate | Command |
|---|---|
| Everything CI runs | `make check` |
| Python only | `make gauntlet-test`, `make suite-test` |
| Frontend | `make frontend-check`, or `make frontend-test` for the tests alone |
| Frontend renders in a browser | `npm run screenshots` — the only gate that catches a page that passes jsdom and paints nothing |
| Electron shell | `make app-check` |
| Suite contract | `make suite-verify-run` |
| Formatting | `make format-check` |
| Onto a bench | `make deploy BENCH=user@host` |

`make build` and `make verify` are the long ones — `verify` is the frontend build, `check`, the
end-to-end test and a real run of every conformance profile. Name them as a gate only when the
package genuinely needs them.

A measurement can also be a gate, as long as the plan says what to record and what value passes.

## Test plan

Group by kind: unit and component, then bench or live-instrument, then contract conformance. Each
entry is an assertion, not an area — "assert a run started with a profile the suite does not declare
is rejected rather than forwarded", not "test overrides". Include the failure and teardown cases:
stop mid-run, unplug the instrument, delete a unit with runs in flight, reimport an index from disk.
Name the harness when one exists; tests build real suite directories on disk, per
`packages/gauntlet/tests/conftest.py`.

A new check in `gauntlet.conformance` needs a test with a suite that fails it.

## Acceptance criteria

Binary, checkable, and traceable to the goals. Each one either holds or does not, without judgment.
The final criterion names the gates: `make check`, any area check the branch touched, and any
live-bench matrix. If an agent cannot decide whether a criterion is met by running something or
reading a value, rewrite it.

## Keeping the plan honest

A stale plan is worse than no plan. Whoever executes it owns keeping it true:

- Update `Status:` as the work moves through `proposed` → `in progress` → `implemented`.
- When the implementation diverges from the plan — a step turns out to be impossible, a contract
  differs from what the code actually does, a package splits — edit the plan to describe the system
  as it now is. Do not leave the old text and do not annotate the change in the file; the plan reads
  as if it had always said this.
- **Tell the user, in chat, every time you change a plan.** Name the file, what changed, and why the
  original no longer holds. Never edit a plan silently — the user is tracking scope against what
  they approved, and a quiet edit hides a scope change.
- If the divergence changes scope, cost, or a decision the user made, stop and ask before editing.

## Finishing

- Write to `docs/plans/<kebab-case-feature>.md`, ending with an empty line.
- Re-read it as the executing agent: is there any step where you would have to guess? Either fix it
  or ask the user.
- Scan the work packages, gates and acceptance criteria against the `asd-ste100` checklist: synonym
  rotation, hedge stacking, nominalization, marketing adjectives, run-on sentences, phrasal verbs.
  One name per thing throughout the file — a package that says "capability" and a gate that says
  "instrument" for the same object reads as two objects.
- Tell the user the path and the work-package count; do not paste the plan into chat.
