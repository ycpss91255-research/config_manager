#!/usr/bin/env bats
#
# 建置期 smoke：「它到底起不起得來？」跑在 runtime-test 階段裡，對著真正會被部署的
# 那個產物跑。
#
# 目錄是 system，不是 smoke。PDF §3.6.1 的三個正交軸裡，smoke 是**軸 3 的型別**
# （目的），unit／integration／system／acceptance 才是**軸 2 的層級**（範圍）。
# 型別寫在檔名裡，目錄留給層級——參照專案 ycpss91255-docker/base 就是這樣分的，
# 它的 test/bats/ 底下只有那四個層級（#116）。

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
