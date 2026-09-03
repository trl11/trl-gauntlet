---
name: pull-request
description: "Open a pull request for this repository, or write or rewrite a pull request description. Use when asked to create a PR, raise a PR, open a pull request, or describe a branch for review. Covers the base branch, the checks to run first, the submodule trap, and the description style a reviewer who does not know the subsystem needs."
---

# Open a pull request

Pushing a branch and opening a pull request are outward-facing actions. **Do both only when the user asks for them in this session.** Approval to commit is not approval to push.

Most work here does not become a pull request. A branch is merged into `main` locally with `--no-ff`, which is what nearly every merge commit in the history is. Open a pull request when the user asks for one, not by default.

## Before opening

1. Confirm the branch is not `main`. If it is, branch first. Branches are named `feat/<subject>` or `fix/<subject>`.
2. Run the checks for what the branch touched, from the table in `CLAUDE.md`:
   - `make check` — always. This is what CI runs.
   - `make frontend-check` — anything under `frontend/`.
   - `make app-check` — anything under `targets/app/`.
   - `make suite-verify-run` — the launcher, the contract models, or the conformance checker.
   - `make verify` is all of it including a real run of every conformance profile. It takes a long time; don't run it unless asked.
3. `git fetch origin`, then `git log origin/main..HEAD --oneline` to read your own commits. The description is written from the branch as a whole, not from the last commit.
4. `git diff origin/main...HEAD --stat` — confirm nothing unintended is in the branch.

**Check the submodule pointers.** `extras/trl-ui-kit` and `extras/trl-engineering-keys` are submodules, and `git add -A` clobbers a merged pointer. That surfaces later as an unresolved import from `@trl11/...` far from the cause. A submodule line in the diffstat that the branch did not deliberately move is a mistake, not a change.

## Opening it

`main` is the base.

```bash
git push -u origin <branch>
gh pr create --base main --title "<title>" --body-file <path>
```

Give a descriptive title, not the branch name. There is no pull request template in this repository, so the body is yours to structure — use the headings below.

## Writing the description

A commit body is written for someone reading the diff. A pull request description is not — write it for a reader who does not know the subsystem.

**Give the ideas, not an inventory of the changes.** The reviewer reads the diff for the changes. They cannot read it for the two or three ideas that hold the branch together. Name those ideas. Say what each one is for. Cut any sentence that only restates what the diff shows. A large branch does not earn a long description.

- Open with what was wrong, then what the change does about it.
- Plain language and short sentences, in short sections under headings. Never one long block.
- A bullet list wherever the content is a list. Write one bullet per idea, not one per change.
- A target name or a path is fine. An unexplained concept is not. Name a symbol only where it carries an idea, because a list of every renamed symbol belongs in the diff.
- Each branch merged in alongside the change gets its own heading: what broke, what it does now.
- End with what the reader has to watch out for — a new manual step, an artifact that goes stale, a behaviour that changed underneath them.
- No emoji. No attribution trailers, no tool or agent mentions.

### How to test

Give a step-by-step procedure with expected results, and say what it was run against. That distinction matters here more than the steps do:

- **Mock**, meaning the conformance profiles and anything whose provider reports `describe()["driver"] == "mock"`. This is what `make check` and CI cover, and it proves the wiring rather than the hardware.
- **A real bench**, meaning instruments that answer. Name the parts, because a suite that passes against a mock camera and a suite that passes against the IMX728 are different claims.
- **A rig**, meaning deployed with `make deploy BENCH=user@host` and driven over the API or the landing page. Say so, because the deploy path has failure modes a checkout never sees — group membership, port binding, the udev rules.

An untested area is said plainly rather than left out.

## After opening

Report the URL. Do not merge, and do not add reviewers, labels or milestones unless asked.

Related: the `commit-message` skill for the commits inside the branch.
