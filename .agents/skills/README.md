# Vendored agent skills

Skills the team shares, checked in so everyone gets the same ones rather than
each person installing their own set. `.claude/skills/` holds a symlink per
skill pointing here, so one copy serves any tool that reads `.agents/`.

`/tdd` is the one this project actually requires: features and bug fixes are
developed test-first, and that skill is the reference for what a test worth
keeping looks like. Read it together with `doc/TEST-PLAN.md` — the skill says
how to test, that document says what this project has agreed to test and at
which interface.

## Provenance

These are third-party skills vendored as-is. Only `i-have-adhd` states a
license upstream (MIT, Ayoub G., <https://github.com/ayghri/i-have-adhd>); the
rest carried no license file or `license:` field at the version vendored here,
so they are included unmodified and without a claim of ownership. Anyone
wanting to reuse one outside this repo should find its upstream first.

## Adding one

Put the skill under `.agents/skills/<name>/`, then symlink it:

```bash
ln -s ../../.agents/skills/<name> .claude/skills/<name>
```

Relative targets, so a clone resolves them wherever it lands. Windows needs
symlink support enabled (`git config core.symlinks true`) or the links arrive
as plain files holding a path.
