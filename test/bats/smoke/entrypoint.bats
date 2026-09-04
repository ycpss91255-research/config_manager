#!/usr/bin/env bats
#
# 建置期 smoke：「它到底起不起得來？」跑在 runtime-test 階段裡，對著真正會被部署的
# 那個產物跑。

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
