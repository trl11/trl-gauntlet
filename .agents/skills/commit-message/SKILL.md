---
name: commit-message
description: "Write a commit message in this repository's house format. Use when about to create a commit, when asked to write or rewrite a commit message, or when squashing or amending. Covers the prose body this repository uses, what a paragraph has to earn, and what never appears in a message. For opening a pull request, use the pull-request skill instead."
---

# Commit messages

A commit body is written for someone reading the diff. It says what the diff cannot: why the change was necessary, and which of its effects are worth knowing about.

Never commit or push unless asked. Branch first if on the default branch, which is `main`.

## Structure

**A summary line, then prose.** Of the last 38 commits with a body, 32 are prose paragraphs and 6 use a bullet list. Bullets are the exception here, not the shape.

- **Summary line** — imperative, naming the outcome rather than the activity. History runs 27 to 75 characters, median 54.
- **Blank line.**
- **Body** — paragraphs. Open with the problem or the background a reader cannot infer from the diff, then what the change does about it. Each paragraph carries one idea.

### When a bullet list earns its place

Only when the content is genuinely an enumeration: a set of parallel items where the reader wants to count them or find one. Three undocumented setup steps, a list of artifacts, a set of profiles. If the items are parallel and countable, list them. If they are reasoning, write the reasoning.

A bullet list is not a way to avoid writing sentences. A body that could be paragraphs and is bullets instead reads as a changelog, and this repository does not write changelogs.

### What a paragraph has to earn

- The reason the change was necessary, which the diff never shows.
- A behaviour that changed, a constraint the change imposes, a trap it avoids.
- The reasoning behind a choice that looks arbitrary in the diff.

### What does not belong

- Anything the diff already says: file counts, renames, mechanics, restating what a function does.
- Attribution trailers, ticket ids, tool or agent mentions, emoji. Older history carries `Co-Authored-By` trailers; they were dropped and do not come back.

## Example

`b47d3a3`, in full, as the shape to follow:

```
Catch the CI checks up with what the tree already ships

`make ci-test` failed at two points, both of them an expectation that had
been left behind by a commit already on main.

`test_commands_declare_their_fields` compared a command field against an
exact dict written before `command_field()` grew `choices_from`, `dial`
and `format`. The field the API serves has carried those three keys since
the CP2112 toolbar redesign, so the assertion was describing a shape that
no longer exists.

`ci-validate-dist` listed eight artifacts, but `make -C app build` has
installed `setup-bench.sh` into `dist/` since the bench setup script
landed. Every named artifact was present, so the `test -s` loop passed and
only the count check failed — the release contract has to name the file
for that count to mean anything.
```

The summary names the outcome. The first paragraph says what was wrong in one sentence. Each paragraph after it takes one failure and explains why it was wrong rather than what line changed. Symbols and targets are backticked and named exactly.

`d71fbe6` is the case for a list: it enumerates the three undocumented steps of standing up a bench, then returns to prose to say what the new script does about them. The list is there because a reader wants to count the steps.

## Wording

The reader is a person, so ordinary prose is right. Say what happened plainly, keep sentences short, and prefer the concrete noun to the abstract one — `serve-gauntlet.sh`, `/dev/video0`, `RunsIndex.import_tree`, not "the serving layer".

## Pull requests

A pull request description has a different reader and its own format — use the `pull-request` skill.
