# Task entry point. Self-built (design appendix A.1) -- the shared template's
# justfile is not adopted in v0.10.0 -- but the COMMAND MODEL is adopted now,
# because renaming commands after people have them in their fingers is the
# expensive kind of change:
#
#   zero exceptions -- every action lives in a namespace, nothing at top level
#   from broad to narrow -- `just test` is the widest run, options narrow it
#   `lint` is not a peer of `test`; it is `just test lint`, part of testing
#
# `just` with no arguments lists everything.

mod docker 'script/justfile.docker'
mod test 'script/justfile.test'

# A `cfg` namespace (just cfg import / scan / apply / revert) joins these once
# the CLI exists; it is an API client, so it cannot precede the API.

default:
    @just --list
