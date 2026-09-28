---
name: update-rig
description: "Check what a running rig (a bench already serving Gauntlet, named in the gitignored .rig file, such as trl@blinky) is on, and deploy this checkout to it when it is behind. Use when asked to deploy, redeploy, update or push a build to a rig or bench, to check whether a rig is up to date or which commit it runs, or \"deploy so I can test\". Not for a rig that has never been deployed to: that is `make deploy-rig RIG_IP=...`."
---

# Updating a running rig

A rig serves Gauntlet from an AppImage installed by `make deploy`. It reports the commit that AppImage was built from at `GET /api/system/info` as `git_sha`, so whether it is current is a question the rig can answer.

## 0. Which rig

The rig this checkout deploys to is named in `.rig` at the repository root, which is gitignored:

```sh
# The rig this checkout deploys to, read by .claude/skills/update-rig.
# Shell syntax. Not committed: each checkout names its own rig.
RIG=trl@blinky
PORT=7100
```

Read it rather than asking. When it is missing and the request names a rig, pass that rig to `rig-status.sh`, which writes `.rig` from it; no question is needed. When it is missing and the request names none, ask for the rig's `user@host` and port (7100 unless its `config.yaml` says otherwise), then pass the answer the same way. When `.rig` exists and the user names a different rig for one deploy, use theirs for that deploy and leave `.rig` alone unless they say the rig has moved; then edit `RIG` and `PORT` in it. Below, `$RIG`, `$HOST` (the part after `@`) and `$PORT` are the values from it.

## 1. Check what the rig is running

```sh
.claude/skills/update-rig/rig-status.sh              # reads .rig; or pass user@host [port]
```

It is read-only. It prints the rig's commit, local `HEAD` and whether the tree is clean, the commit `dist/` was last built from, the commits the rig is missing, any host-setup files changed since the rig's build, and any run in flight. Its last line is a verdict, and its exit status matches it:

| Exit | Verdict | What to do |
|---|---|---|
| 0 | `CURRENT` | Nothing. Say so, with the commit. Deploy anyway only if the user asked for a deploy regardless. |
| 1 | `BEHIND` | Deploy (step 2). List the commits the rig is missing in the report. |
| 1 | `STALE?` | Same commit, but the tree has uncommitted changes. `git_sha` records `HEAD` only, so the rig cannot be proven current. If the user wants those changes on the rig, deploy; mention that the rig will report a commit that does not contain them. |
| 2 | `UNREACHABLE` | Do not deploy blind. Check `ssh $RIG systemctl --user status gauntlet.service` and report what it says. |
| 3 | `BUSY` | Stop and ask. A deploy restarts the service, which kills the run in flight. Name the run and suite, and deploy only when the user says to. |

## 2. Build, then deploy

`make deploy` sends whatever AppImage is already in `dist/` and never builds, so a deploy without a build ships stale code and the user sees old behaviour. Always build first:

```sh
make app-build                      # several minutes; stamps HEAD into the build
make deploy BENCH=$RIG
```

Send the output of both to a log file and read its tail, because both are long. Either failing ends the attempt: report the error, do not retry blindly.

The deploy always ends by saying the host setup was copied but not run. That is normal. It matters only when `rig-status.sh` listed changed files under `targets/service/` or `tools/deploy/` (udev rules, units, sysctl); then tell the user to run `ssh -t $RIG 'sudo gauntlet/setup-host.sh'`, which needs their password.

## 3. Confirm the rig serves the new build

Rerun the status script. It should now say `CURRENT` (or `STALE?` if the tree was dirty). The service takes a few seconds to come back, so an `UNREACHABLE` straight after a deploy is worth one retry after a short wait:

```sh
for i in $(seq 30); do curl -sf -m 3 http://$HOST:$PORT/api/health >/dev/null && break; sleep 2; done
.claude/skills/update-rig/rig-status.sh
```

When the change is frontend-only and the tree was dirty, the commit cannot prove it arrived. Grep the served bundle for a string the change added:

```sh
js=$(curl -s http://$HOST:$PORT/ | grep -o 'assets/index-[^"]*\.js' | head -1)
curl -s "http://$HOST:$PORT/$js" | grep -c 'a string from the change'
```

## 4. Report

Keep it short: the URL (`http://$HOST:$PORT/`), the commit it now serves, what changed for the user to test, and anything they must do themselves (host setup, checking in again after a storage key change). Say plainly whether the deployed code is committed; a deploy is not a commit and nothing here pushes.
