# Tools

Scripts this project is worked on with, none of which ship. What does ship is
under `targets/`.

| Directory | Contents |
|---|---|
| `bench/` | Reading a bench directly: whether the udev rules reached the instruments, and what one I2C address is saying. |
| `deploy/` | Putting a release on a bench, and standing a new rig up from nothing. |
| `release/` | Cutting a release: holding every manifest's version equal to `VERSION`. |

Each is reached through a Make target rather than run by hand:

| Task | Command |
|---|---|
| `bench/udev_check.py` | `make udev-check` |
| `deploy/deploy-bench.sh` | `make deploy BENCH=user@host` |
| `deploy/deploy-rig.sh` | `make deploy-rig RIG_IP=x.x.x.x` |
| `release/version.py` | `make version-check` / `make version-sync` |

`bench/mevo_temp_monitor.py` is the exception, because it is an investigation
rather than a step: it drives the `i2c` capability's own transfer logic against
one address and prints what comes back. Run it directly.
