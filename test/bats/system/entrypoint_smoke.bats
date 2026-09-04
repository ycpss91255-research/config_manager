#!/usr/bin/env bats
#
# 建置期 smoke：「它到底起不起得來？」跑在 runtime-test 階段裡，對著真正會被部署的
# 那個產物跑。
#
# 目錄是 system，不是 smoke。PDF §3.6.1 的三個正交軸裡，smoke 是**軸 3 的型別**
# （目的），unit／integration／system／acceptance 才是**軸 2 的層級**（範圍）。
# 型別寫在檔名裡，目錄留給層級——參照專案 ycpss91255-docker/base 就是這樣分的，
# 它的 test/bats/ 底下只有那四個層級（#116）。
#
# 目錄決定「它屬於哪個範圍」，型別決定「誰執行它」。§3.6.1 的軸 3 明文寫著 smoke
# 在 runtime-test stage 的建置過程中執行，所以這兩件事本來就不必互相決定。

setup() {
  # 這一檔斷言的是**建好的 runtime 映像**：裝進去的 entrypoint、烤在 /opt 的原始碼、
  # 映像自己的 PATH 與 PYTHONPATH。在別的地方跑，它觀察到的是別的東西。
  #
  # **整檔跳過，不是只跳過會紅的那三條。** 「python 在 PATH 上」與「git 在 PATH 上」
  # 在檢查映像裡也是綠的——但那是檢查映像的 PATH，不是被測產物的。一條綠在錯的
  # 對象上的斷言，比一條紅燈更難發現。
  #
  # **守衛不看 /entrypoint.sh 在不在**：那正是底下第一條斷言的東西，拿它當守衛會讓
  # 那條斷言恆真——把 runtime 的 COPY 那一行拿掉，這一檔會安靜地整個跳過，而不是
  # 轉紅。所以用一個與每一條斷言都無關的旗標，由執行者明說「我就是那個映像」。
  #
  # 旗標被漏掉的風險由 Dockerfile 那一端擋住：那裡是唯一該執行它們的地方，而它在
  # 有任何一條跳過時讓建置失敗。少了那道守衛，這個 skip 就會變成另一種靜默通過。
  if [ "${CM_SYSTEM_IMAGE:-}" != "1" ]; then
    skip "只在建好的 runtime 映像裡有意義，由 docker build --target runtime-test 執行"
  fi
}

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
