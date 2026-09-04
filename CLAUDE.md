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
(`from core.config_list import load`, `python -m api.cli`).

- `src/core/` — pure logic, **no I/O**: need file content? take it as a parameter.
  Every core test runs with no filesystem, git, or network.
- `src/io/` — filesystem/git adapters. *(Currently unimportable as `io`: the name
  clashes with Python's stdlib `io`; see issue #56.)*
- `src/api/` — HTTP endpoints + CLI (the CLI is an HTTP client, ADR-00000009).
- `src/web/` — single HTML entry point.

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

`./script/test.sh` (or `just test`) runs the whole set, and a missing tool fails
loudly rather than skipping silently. Push only when green.

- `ruff check src test` · `mypy --strict src/core` · `pylint src` (10.00/10) ·
  `pytest test/pytest --cov=src/core` (fail_under 85)
- The shell scripts need **bash 4+** (`brew install bash` on macOS; the stock 3.2
  can't run them).

## TDD

Red → green, one slice at a time. Write tests **only at the confirmed interfaces
in `doc/TEST-PLAN.md`** — never at an unconfirmed one. Expected values from an
independent source; one logical assertion per test; test names say what the code
does, in `CONTEXT.md`'s words.
