#!/usr/bin/env bats
#
# 建置期 smoke：「它到底起不起得來？」跑在 runtime-test 階段裡，對著真正會被部署的
# 那個產物跑。

@test "entrypoint 裝進去了且可執行" {
  [ -x /entrypoint.sh ]
}

@test "python 在 PATH 上" {
  run python --version
  [ "$status" -eq 0 ]
}

@test "git 在 PATH 上——io/git.py 是包在 CLI 外面的" {
  run git --version
  [ "$status" -eq 0 ]
}

@test "應用程式的原始碼烘在 /opt 底下，不在 \$HOME" {
  [ -d /opt/config_manager/src ]
}

@test "核心層在沒有檔案系統也沒有 git repo 的情況下 import 得起來" {
  run python -c "import config_manager.core.models"
  [ "$status" -eq 0 ]
}
