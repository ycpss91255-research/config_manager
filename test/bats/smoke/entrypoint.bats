#!/usr/bin/env bats
#
# Build-time smoke: "does it even get up?" Runs inside the runtime-test
# stage, against the artifact that would actually be deployed.

@test "entrypoint is installed and executable" {
  [ -x /entrypoint.sh ]
}

@test "python is on PATH" {
  run python --version
  [ "$status" -eq 0 ]
}

@test "git is on PATH -- io/git.py wraps the CLI" {
  run git --version
  [ "$status" -eq 0 ]
}

@test "the application source is baked under /opt, not \$HOME" {
  [ -d /opt/config_manager/src ]
}

@test "the core layer imports without a filesystem or a git repo" {
  run python -c "import config_manager.core.models"
  [ "$status" -eq 0 ]
}
