# <Feature Name>

Status: proposed

<One paragraph: what this design provides, and what existing machinery it reuses rather than
replaces. Name the reused path explicitly — discovery, the supervisor, the capability registry, the
runs index, the release pipeline.>

## Goals

- <Observable outcome, imperative, one line each.>
- <Include the guarantees that make the change correct, not only the features it adds.>
- <Include "preserve the existing behavior of X" where behavior must not regress.>

## Non-Goals

- <A thing a reasonable implementer might otherwise build. Fence it off.>
- <Scope that belongs to a later plan.>
- <Behavior explicitly rejected, e.g. "silently changing a requested value when the hardware rejects it".>

## Current <Area> Contract

<Only when behavior already exists here. What the system does today: the command, the wait, the
response shape, the persistence, the validation. Name the endpoints, fields, and units. This is the
baseline the rest of the plan diffs against.>

## User Model

<What the operator sees and controls. Name the exact UI location and control labels.>

- <Control>
- <Control>

<State what deliberately does not appear in the UI and why the boundary sits there.>

| Value | Label | Behavior |
|---|---|---|
| `0` | <label> | <behavior> |
| `1` | <label> | <behavior> |

## Implementation

<How it works, in the order it happens. Name real types and files.>

A <request/operation> performs this sequence:

1. <step>
2. <step>
3. <step>

<Cross-boundary statement: which of `gauntlet_sdk.contract`, the API router, `storage/`, the
capability registry, `frontend/src/api/types.ts` and its captured fixtures the field or type
crosses, and what each layer does with it.>

<Concurrency, buffering, and fan-out: queue depth, drop policy, what is shared vs copied, what is
serialized by which lock.>

<Failure semantics: what each external interaction does on timeout, rejection, or partial result.
State that no partial artifact is written.>

<Untrusted input: the validation rule for any URL, path, upload, or device address, and what it
prevents.>

<Persistence and reimport: the default when the field is absent from an existing run or profile,
and whether it survives `RunsIndex.import_tree` rebuilding the index from disk. Say the same for
anything written to the data directory, which outlives a redeploy while the unpacked bundle does
not.>

## Design Decisions

### <Decision>

<The rule, then why it holds. Ranges, defaults, and what happens outside the range.>

### <Contract, e.g. Timestamp Contract>

- <Invariant.>
- <Invariant, with units and timebase.>

### <Ownership, e.g. Buffer Ownership>

<Who owns what until when, and which outcomes the API must distinguish. State why a simpler return
type is insufficient.>

## Work Packages

### 1. <Lowest layer / characterization>

<What changes, in which files. Numbered steps when order is load-bearing.>

1. <step>
2. <step>

<Exit gate: `make check`, `make gauntlet-test`, `make frontend-check`, `make suite-verify-run`, or
a measurement with the value that passes. If this package resolves an unknown, name the artifact it
produces for later packages.>

### 2. <Next layer>

<As above. Each package is independently landable and independently verifiable.>

### 3. API and storage

- <validation rule per layer; an override a manifest does not declare is rejected, never forwarded>
- <default preserved on reload, and after `RunsIndex.import_tree`>
- <specific error for unsupported combinations>
- <round trip: create, read, update, rescan>

### 4. Frontend

<Component to change and how state threads through it. The view is built from the manifest and the
provider's declared `state()`, `commands()` and `readouts()`; no component names a suite or an
instrument.>

The UI:

- defaults to <value>;
- <availability rule from `requires` and the provider's `available()`>;
- <what it explains to the operator>;
- preserves the value when the view is reopened.

### 5. Observability

<Runtime information sufficient to diagnose the change without per-item logging.>

## Test Plan

### Unit and Component Tests

- <Assertion, not an area.>
- <Failure case: timeout, rejection, partial input.>
- <Teardown case: stop mid-flight, remove the source, disable the service.>

### <Bench Matrix>

<Which instruments to run against and, for each run, what to save, measure, and confirm. Say which
entries run against a mock provider and which need hardware that answers.>

### <Integration / Egress Validation>

<Downstream consumers to exercise and what must hold at each.>

### Runtime Reconfiguration

1. <start state>
2. <change>
3. <what must be observed>

## Acceptance Criteria

- <Binary and checkable.>
- <Traceable to a goal.>
- <Existing behavior unchanged within stated tolerance.>
- <Cleanup: no leaked resources, no late writes, no orphaned workers.>
- `make check` passes, along with any area check the change touched and the <bench matrix>.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| <Concrete failure mode> | <The design element that prevents it> |

## Delivery Order

1. <Submodule or lower-repo change first.>
2. <Pointer update.>
3. <Parent implementation.>
4. <API and persistence.>
5. <UI.>
6. <Deploy and run the validation matrix.>
7. <Record measured results in `docs/`.>

<What keeps intermediate commits safe: which value stays enforced, and when the setting becomes
user-visible.>
