#!/usr/bin/env bats
#
# script/acceptance.sh — 驗收檢查點的逐條判定（T19、#148）。
#
# 被觀察的不是任何一條驗收檢查點，而是**這份報表會不會說謊**。一份報表只有三種
# 說謊的方式，三種都在這裡：
#
#   1. 把「沒有人在驗」報成通過（未涵蓋）
#   2. 把「指到的規格根本不存在」安靜跳過（同 test.sh 的「缺工具不得靜默通過」）
#   3. 一條規格轉紅時，把不相干的檢查點一起拖下水，或反過來，一條都不報紅
#
# **餵的是替身對照表，不是真的那一份。** CM_ACCEPTANCE_MAP 與 CM_ACCEPTANCE_ROOT
# 兩個覆寫點，與 coverage_gate.bats 的 CM_COVERAGE_JSON 同一個先例：真的檢查點結果
# 若參與這些規格，它們會在別人修好 dump 的那天無故轉紅——而一組結果取決於別的規格
# 有沒有跑的規格，測的不是這支腳本。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  SCRIPT="${REPO_ROOT}/script/acceptance.sh"
  WORK="$(mktemp -d)"
  ROOT="${WORK}/root"
  MAP="${WORK}/checkpoints.toml"
  mkdir -p "${ROOT}/test/pytest/unit" "${ROOT}/test/bats/unit"

  cat >"${ROOT}/test/pytest/unit/test_green.py" <<'PY'
def test_一條會過的規格():
    assert True


def test_另一條會過的規格():
    assert True
PY

  cat >"${ROOT}/test/pytest/unit/test_red.py" <<'PY'
def test_一條會紅的規格():
    assert False
PY

  # 替身的 bats 規格不能用 heredoc 原樣寫出來：bats 的前處理器掃的是**這個檔案的
  # 每一行**，`@test "…" {` 出現在 heredoc 裡照樣被當成一則測試，於是這個檔案會多出
  # 一則跑不起來的幽靈測試（實測：24 則裡的第 1 則，bats 回 unknown test name）。
  # 把關鍵字經一個變數組出來，那一行就不再長成前處理器認得的樣子。
  local keyword='@test'
  printf '%s "一條會過的 bats 規格" {\n  true\n}\n' "${keyword}" \
    >"${ROOT}/test/bats/unit/green.bats"
}

teardown() {
  rm -rf "${WORK}"
}

# 一條檢查點。編號、文字、規格參照（以逗號分隔的 TOML 陣列內容）。
checkpoint() {
  local number="$1" text="$2" specs="$3"
  cat >>"${MAP}" <<TOML

[[milestone.checkpoint]]
number = ${number}
text = '${text}'
specs = [${specs}]
TOML
}

start_map() {
  cat >"${MAP}" <<'TOML'
[[milestone]]
id = 'v0.1.0'
title = '替身'
TOML
}

report() {
  CM_ACCEPTANCE_MAP="${MAP}" CM_ACCEPTANCE_ROOT="${ROOT}" run "${SCRIPT}" "${1:-v0.1.0}"
}

# ── 通過與判定的印出 ──────────────────────────────────────────────────────

@test "對照表指到的規格全數通過時，結束碼為 0" {
  start_map
  checkpoint 1 '一條有人驗的檢查點' "'test/pytest/unit/test_green.py'"

  report

  [ "${status}" -eq 0 ]
}

@test "每一條檢查點都被逐條印出判定，不是只印一個總結" {
  start_map
  checkpoint 1 '第一條' "'test/pytest/unit/test_green.py::test_一條會過的規格'"
  checkpoint 2 '第二條' "'test/pytest/unit/test_green.py::test_另一條會過的規格'"

  report

  [[ "${output}" == *"檢查點 1  通過"* ]]
  [[ "${output}" == *"檢查點 2  通過"* ]]
}

@test "檢查點的文字被印出來，讀報表的人不必自己去翻 PDF" {
  start_map
  checkpoint 1 '寫出中斷不產生半殘檔案' "'test/pytest/unit/test_green.py'"

  report

  [[ "${output}" == *"寫出中斷不產生半殘檔案"* ]]
}
