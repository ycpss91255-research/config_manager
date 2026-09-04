# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow the milestone ladder in the design document §8.

## [Unreleased]

### Added

- Repository skeleton: multi-stage `Dockerfile` (`sys` / `devel-base` /
  `devel` / `runtime` / `runtime-test`), hand-written `compose.yaml`,
  `script/` task entry points, and the `just` command model.
- Three-axis test tree under `test/` (static analysis / level / type), with
  reserved non-functional slots kept as documented empty directories.
- Lint thresholds moved from prose into `pyproject.toml`, where a tool can
  check them.
- CI: lint, pytest with coverage, and a `runtime-test` image build.

### Notes

- The shared container template (`ycpss91255-docker/base`) is **not** adopted
  in v0.10.0, by decision (design appendix A). What is adopted now are the
  choices that are expensive to reverse: stage names, `/opt` for baked
  artifacts, `host` networking, the `.local` override suffix, the `just`
  command model, ADR format, and Conventional Commits.

- Milestones v0.1.0–v0.10.0 with the design document's §8.2 acceptance
  checkpoints, and 54 issues split along the §8.1 capability matrix, each
  carrying its own acceptance criteria. §0.7 places acceptance criteria on
  issues rather than in the document, so the document's §8 becomes a pointer
  to GitHub rather than a second copy that can go stale.
- `script/prune.sh` and its `just docker prune` recipe, closing the gap
  against the wrapper set §3.3.2 names.
- `doc/test/TEST.md`: how to run the tests, and what each cell of the three
  axes covers, including the deliberately-empty slots and their reasons.
- `script/hooks/{pre,post}/` for all seven verbs, wired to the five
  self-built wrappers through `script/hooks/dispatch.sh`, so a hook dropped
  in actually runs. The two uses §3.3.4 names move out of the wrappers and
  into `pre/run.sh` and `post/build.sh`. Seams with no caller say so in the
  file (ADR-00000023).
- `script/local/` with the `cfg` command group, registered through the root
  justfile. The CLI it forwards to arrives with the API in v0.1.0; until
  then it fails with a message that says exactly that.
- `.setup.conf` as a placeholder carrying no service values, plus the
  ordered steps for adopting the template without ever having two live
  service definitions (ADR-00000024).
- ADR-00000023 and ADR-00000024.

### Fixed

- `just docker *` and `just test *` were broken in every invocation: a
  module recipe runs with the module's directory as cwd, so the
  `./script/<name>.sh` paths never resolved. They now go through
  `{{justfile_directory()}}`, which is the root justfile's directory
  regardless of which module the recipe lives in.
- `script/lint_commit.sh` and `just test lint commit`: this repo's commit
  messages follow ycpss91255-docker/base, and the rules are derived from a
  200-commit sample of base rather than restated from memory. Fails on an
  unknown type or a malformed prefix, warns on a missing scope or an
  uppercase subject, and deliberately checks neither length nor issue refs
  -- the conventional 50-character cap would reject most of the repo it is
  meant to align with. Scoped to `origin/main..HEAD`, so existing history is
  untouched (ADR-00000025).
