# Scaffolding a suite

`gauntlet new-suite` generates a suite directory from a template bundled with
the package. `make suite-new` wraps it.

```bash
gauntlet new-suite thermal_cycle
gauntlet new-suite link_check --template shell

make suite-new NAME=thermal_cycle              # same, into ./suites
make suite-new NAME=link_check TEMPLATE=shell
```

## Templates

| Template | Produces |
|---|---|
| `python` (default) | A `SuiteSpec` suite: profile model, `iterate()`, `evaluate()`, and a CLI built with `make_suite_cli`. |
| `shell` | A bash script implementing the contract directly, with no Gauntlet dependency. |

`gauntlet templates` prints the available names.

Every template renders a suite that passes `gauntlet verify --run` unmodified.

## Substitutions

Each file is copied with three placeholders replaced. A path component named
`__SUITE_KEY__` is renamed, though no current template uses one — the code
package is always `suite/`.

| Placeholder | For `my_probe` | Appears in |
|---|---|---|
| `__SUITE_KEY__` | `my_probe` | manifest key, `SuiteSpec.name` |
| `__SUITE_TITLE__` | `My Probe` | manifest title, docstrings |
| `__SUITE_CLASS__` | `MyProbe` | profile class name |

`__SUITE_TITLE__` contains spaces, so `__SUITE_CLASS__` is a separate
placeholder for positions requiring a Python identifier.

Executable bits are preserved. Files with a binary suffix are copied verbatim.

## Adding a template

Create `templates/<name>/` containing a `suite.yaml`, a `profiles/` directory
with a `quick.yaml`, and whatever the suite runs. Use the placeholders above.
Put any code under `suite/`, so every suite has the same layout.

`packages/gauntlet/tests/test_scaffold.py` parametrizes over `available_templates()`, so a new
template is rendered, loaded, and run through `gauntlet verify --run`
automatically.

## Layout

```
packages/gauntlet/src/gauntlet/scaffold/
├── generator.py       rendering
└── templates/
    ├── python/
    └── shell/
```

Templates ship as package data, so scaffolding works from an installed
Gauntlet with no source checkout.
