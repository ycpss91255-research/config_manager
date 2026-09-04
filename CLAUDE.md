# CLAUDE.md

Agent guidance for `config_manager`. **Read `CONTEXT.md` first** for the domain
vocabulary — use those terms, don't invent synonyms.

## What this is

A centralized config management system: the config repo is the single source of
truth, written out to targets, validated before change, with drift surfaced.
Python 3.11 + FastAPI backend; so far only the core layer has landed.

Read order: `CONTEXT.md` → `doc/PRD.md` (9 invariants, never violate) →
`doc/adr/` (why it's shaped this way) → `doc/TEST-PLAN.md` (the confirmed test
interfaces).

## Layers

`api → core → io`, one direction (ADR-00000011). Import by top-level module name
(`from config_manager.core.config_list import load`, `python -m config_manager.api.cli`).

- `src/config_manager/core/` — pure logic, **no I/O**: need file content? take it as a parameter.
  Every core test runs with no filesystem, git, or network.
- `src/config_manager/io/` — filesystem/git adapters. Everything lives under one
  top-level package because a top-level `io/` shadows the stdlib module and can stop
  the interpreter from starting (ADR-00000026).
- `src/config_manager/api/` — HTTP endpoints + CLI (the CLI is an HTTP client, ADR-00000009).
- `src/config_manager/web/` — single HTML entry point.

## Workflow

- **Milestone/issue-driven.** Work the GitHub milestones in order
  (`gh issue list --milestone v0.1.0`). Don't jump ahead.
- **Branch → PR → squash.** New work on a feature branch; open a PR; the maintainer
  squash-merges. **Never push to `main` directly, never force-push** — the org
  co-pushes, so `git fetch` + `git rebase origin/main` before pushing.
- **A decision you can't make yourself** (owner-level / architecture) → open a
  GitHub issue and wait, but don't idle: move to another unblocked issue meanwhile.
- **A bug in shipped behaviour** → `fix` + a patch version bump; a milestone's
  features are the minor.

## Commit messages

Follow base's convention, enforced by `script/lint_commit.sh` (ADR-00000025).
Self-check: `./script/test.sh --lint commit`.

- type ∈ `feat fix docs refactor test chore ci perf` (anything else fails)
- the `type(scope): ` prefix must be present and well-formed; no trailing period
- the subject is a lowercase **declarative** sentence saying what is now true
  (`feat(state): the state is missing when the target is gone`), not `add X`
- scope and issue refs are recommended; length is not checked

End commit bodies with a `Co-Authored-By:` trailer; end PR descriptions with the
Claude Code line.

## Quality gates (local == CI)

**Run every check inside the container. Never against this host.**

`./script/test.sh` (or `just test`) builds `dockerfile/Dockerfile.test-tools` and
re-execs itself inside it; CI calls the same script and installs nothing. One
image, two callers (ADR-00000027). Push only when green.

Do not reach for the host interpreter to "just check something quickly". The
host is not evidence about this project, and saying so is not a style
preference -- it has produced four wrong answers here:

| what the host said | what was true |
|---|---|
| pytest could not collect the suite | host pytest 6.2.5 silently ignores `pythonpath`; the project needs 8+ |
| lint clean | host had no hadolint, so CI went red six pushes in a row |
| the interpreter cannot start | on 3.11 the same `io` collision (#56) is a `ModuleNotFoundError` |
| nothing to check in workflows | actionlint existed only in CI, and it reads what no YAML parser sees |

`CM_TEST_LOCAL=1` runs on the host anyway. It surveys the host first and names
**every** checker that is missing, then stops. Adding `CM_LINT_ALLOW_MISSING=1`
runs the rest and repeats what did not run — and **a run with skips is not a
passing run**, so do not report one as green. `CM_APT_MIRROR` overrides the
image's Debian mirror.

- `ruff check src test` · `mypy --strict src/config_manager/core` · `pylint src` (10.00/10) ·
  `pytest test/pytest --cov=src/config_manager/core` (fail_under 85)
- The shell scripts need **bash 4+** (`brew install bash` on macOS; the stock 3.2
  can't run them).

## TDD

Red → green, one slice at a time. Write tests **only at the confirmed interfaces
in `doc/TEST-PLAN.md`** — never at an unconfirmed one. Expected values from an
independent source; one logical assertion per test; test names say what the code
does, in `CONTEXT.md`'s words.
