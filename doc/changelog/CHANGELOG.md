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
