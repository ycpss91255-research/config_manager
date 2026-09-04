#!/usr/bin/env bats
#
# script/check_file.sh — 編輯之後立刻跑得動的那幾項單檔檢查。
#
# 這支不是閘門（CI 才是），但它的失敗形狀跟閘門一樣危險：一個接錯線的 hook 什麼都
# 不檢查，而它每次都回 0，看起來跟「檢查過了、乾淨」一模一樣。這個 repo 已經在
# hadolint 上付過一次那個代價（ADR-00000027 第二列）。所以派工規則要有規格。
#
# 被觀察的是命令列（T19）：一個路徑進去，或一份 hook 酬載進 stdin，看結束碼與訊息。
# 規格跑在檢查映像裡，CM_IN_TEST_IMAGE=1 已由映像設定，所以是就地執行、不轉容器。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  SCRIPT="${REPO_ROOT}/script/check_file.sh"
}

@test "src 底下的 Python 檔違反 ruff 規則時被擋下" {
  cat >"${REPO_ROOT}/src/config_manager/core/_spec_tmp.py" <<'PY'
def f() -> int:
    try:
        return int("x")
    except:
        return 0
PY
  run "${SCRIPT}" src/config_manager/core/_spec_tmp.py
  rm -f "${REPO_ROOT}/src/config_manager/core/_spec_tmp.py"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"E722"* ]]
}

@test "src 底下的 Python 檔缺三要素的訊息也被擋下" {
  cat >"${REPO_ROOT}/src/config_manager/core/_spec_tmp.py" <<'PY'
class DuplicateUid(Exception):
    """兩筆條目共用同一個 uid。"""


def f(ref: str) -> None:
    raise DuplicateUid(f"uid 重複：{ref}")
PY
  run "${SCRIPT}" src/config_manager/core/_spec_tmp.py
  rm -f "${REPO_ROOT}/src/config_manager/core/_spec_tmp.py"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"下一步"* ]]
}

@test "script 底下的 shell 腳本走 shellcheck" {
  cat >"${REPO_ROOT}/script/_spec_tmp.sh" <<'SH'
#!/usr/bin/env bash
echo $undefined_and_unquoted
SH
  run "${SCRIPT}" script/_spec_tmp.sh
  rm -f "${REPO_ROOT}/script/_spec_tmp.sh"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"SC2"* ]]
}

@test "乾淨的檔案通過" {
  run "${SCRIPT}" src/config_manager/core/config_list.py
  [ "${status}" -eq 0 ]
}

@test "不在派工表上的路徑不跑任何檢查，也不報錯" {
  run "${SCRIPT}" doc/TEST-PLAN.md
  [ "${status}" -eq 0 ]
}

@test "repo 之外的路徑不跑任何檢查，也不報錯" {
  run "${SCRIPT}" /etc/hostname
  [ "${status}" -eq 0 ]
}

@test "不存在的檔案不報錯" {
  run "${SCRIPT}" src/config_manager/core/no_such_file.py
  [ "${status}" -eq 0 ]
}

@test "沒有參數時從 stdin 的 hook 酬載讀出 file_path" {
  cat >"${REPO_ROOT}/src/config_manager/core/_spec_tmp.py" <<'PY'
def f() -> int:
    try:
        return int("x")
    except:
        return 0
PY
  run bash -c "printf '%s' '{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"${REPO_ROOT}/src/config_manager/core/_spec_tmp.py\"}}' | '${SCRIPT}'"
  rm -f "${REPO_ROOT}/src/config_manager/core/_spec_tmp.py"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"E722"* ]]
}

@test "stdin 不是 JSON 時不報錯，也不假裝檢查過" {
  run bash -c "printf 'not json' | '${SCRIPT}'"
  [ "${status}" -eq 0 ]
}
