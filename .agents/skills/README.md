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

`skills-lock.json` at the repo root records where each skill came from: its
upstream repo, the path within it, and a content hash. It is the authority
here; this section only summarises it, and a summary that disagrees with the
lockfile is wrong.

All 37 skills under this directory come from
[`mattpocock/skills`](https://github.com/mattpocock/skills) (**MIT**), vendored
unmodified. `i-have-adhd` is separate:
[`ayghri/i-have-adhd`](https://github.com/ayghri/i-have-adhd) (**MIT**, Ayoub
G.), and it is the only one whose `SKILL.md` carries a `license:` field of its
own — which is why an earlier version of this file said the rest were
unlicensed. That was wrong: the individual files carry no license field, but
their upstream repository is MIT.

## Adding one

Put the skill under `.agents/skills/<name>/`, then symlink it:

```bash
ln -s ../../.agents/skills/<name> .claude/skills/<name>
```

Relative targets, so a clone resolves them wherever it lands. Windows needs
symlink support enabled (`git config core.symlinks true`) or the links arrive
as plain files holding a path.
