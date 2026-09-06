#!/usr/bin/env bats
#
# script/lint_coverage_audit.sh — 覆蓋率審計表與 src/、script/ 的現況必須對得起來。
#
# 這張表自己立了一條規則：「新增一個模組或一支腳本時，這裡要一起加一列」。在這支
# lint 之前，那條規則沒有任何工具擋著——依設計 §0.4「無法自動檢查的規範等同不存在」，
# 它先前並不存在，而它也確實兩次沒被遵守（#104 加了兩個模組沒加列，#113 補完之後
# `script/release.sh` 又讓腳本數從 32 漂到 33）。
#
# 觀察位置是命令列：餵一個 repo 根目錄進去，看結束碼與訊息。與 T19 其餘十一支
# 守門腳本同一個測試介面。
#
# 規格造的是**假的 repo 根目錄**，不是真的那一個：真的那一份會隨開發變動，把它當
# 輸入的規格只能斷言「現在剛好通過」，斷言不了「不一致時會被擋下」。
#
# ## 突變檢查（每條規則各拿掉一次，#117）
#
# | 突變 | 轉紅的規格 |
# |---|---|
# | M1a R1：拿掉「找不到那一節」的守衛 | **第一次沒有轉紅**，見下 |
# | M1b R1：讀不出來的列改成安靜略過 | 12 |
# | M2  R2：漏列不再回報 | 2、3 |
# | M3  R3：對不到檔案的列不再回報 | 4 |
# | M4  R5：已落地卻標「未落地」不再回報 | 6 |
# | M5  R4：手抄數量的比對改成永不命中 | 10 |
#
# **M1a 抓到一則假的綠燈。** 規格 13 原本只斷言訊息裡有「模組 → 測試介面」，而漏列
# 那條規則的下一步訊息裡也有那幾個字——整節消失時，它靠另一條規則的訊息照樣通過。
# 補上「找不到」之後才真的轉紅。一則會因為別條規則而通過的規格，測的不是它自己
# 針對的那條規則。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_coverage_audit.sh"
  WORK="$(mktemp -d)"
  mkdir -p "${WORK}/doc" "${WORK}/src/config_manager" "${WORK}/script"
}

teardown() {
  rm -rf "${WORK}"
}

# 造一份只有「模組 → 測試介面」一節的 TEST-PLAN。表格的列（以及要跟在表後面的
# 散文）由 stdin 給。
_plan() {
  {
    printf '## 覆蓋率審計\n\n### 模組 → 測試介面\n\n'
    printf '| 模組 | 測試介面 | 狀態 |\n|---|---|---|\n'
    cat
    printf '\n### 流程 → 測試介面\n\n表外的文字不歸這一節管。\n'
  } >"${WORK}/doc/TEST-PLAN.md"
}

# 造一個模組檔。路徑相對於 src/config_manager，與表格的寫法一致。
_module() {
  mkdir -p "$(dirname "${WORK}/src/config_manager/$1")"
  printf '"""spec fixture."""\n' >"${WORK}/src/config_manager/$1"
}

# 造一支腳本。路徑相對於 script/，與表格的寫法一致。
_script() {
  mkdir -p "$(dirname "${WORK}/script/$1")"
  printf '#!/usr/bin/env bash\n' >"${WORK}/script/$1"
}

@test "每個 .py 與每支 .sh 都有一列時通過" {
  _module __init__.py
  _module core/__init__.py
  _module core/state.py
  _script test.sh
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |
| `**/__init__.py` | 無——見「刻意的空格」 | 已落地 |
| `script/test.sh` | T19 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}

@test "沒有一列的模組被指名，訊息說它既不算被覆蓋也不算刻意留空" {
  _module core/state.py
  _module core/index.py
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"src/config_manager/core/index.py"* ]]
  [[ "${output}" == *"刻意留空"* ]]
}

@test "沒有一列的腳本被指名" {
  _script test.sh
  _script lint_adr.sh
  _plan <<'ROWS'
| `script/test.sh` | T19 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"script/lint_adr.sh"* ]]
}

@test "對不到任何檔案的列，狀態不是「未落地」就被擋下並指名那一列" {
  _module core/state.py
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |
| `core/validate` | T3 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"core/validate"* ]]
}

@test "對不到檔案但狀態寫「未落地」的列是預定的落點，不是失準" {
  _module core/state.py
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |
| `core/validate` | T3 | 未落地（#16） |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}

@test "狀態寫「未落地」但檔案已經在的列被擋下，訊息指名那個檔案" {
  _module core/state.py
  _plan <<'ROWS'
| `core/state` | T2 | 未落地（#58） |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"src/config_manager/core/state.py"* ]]
}

@test "狀態寫「部分落地」的列不被當成宣告未落地" {
  _module api/session.py
  _plan <<'ROWS'
| `api/session` | T13 | 部分落地：身分已落地；階段未落地（#33） |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}

@test "萬用字元的列同時涵蓋頂層與子套件的套件標記" {
  _module __init__.py
  _module core/__init__.py
  _module io/__init__.py
  _plan <<'ROWS'
| `**/__init__.py` | 無——見「刻意的空格」 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}

@test "大括號展開讓一列涵蓋數支同形狀的腳本" {
  _script build.sh
  _script run.sh
  _script stop.sh
  _plan <<'ROWS'
| `script/{build,run,stop}.sh` | 無——見「刻意的空格」 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}

@test "手抄的數量被擋下，訊息指名那一行與不變式 9" {
  _module core/state.py
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |

這張表涵蓋 `find src -name '*.py'` 的 20 個檔案與 `find script -name '*.sh'` 的 33 支腳本。
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"33 支"* ]]
  [[ "${output}" == *"不變式 9"* ]]
}

@test "「一個模組或一支腳本」是不定冠詞，不是被手抄的數量" {
  _module core/state.py
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |

**新增一個模組或一支腳本時，這裡要一起加一列**——沒有一列的檔案，
既不算被覆蓋，也不算刻意留空。
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}

@test "第一欄沒有反引號路徑的列讀不出來，整份停下並指名那一列" {
  _module core/state.py
  _plan <<'ROWS'
| core/state | T2 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"core/state"* ]]
  [[ "${output}" == *"反引號"* ]]
}

@test "找不到「模組 → 測試介面」這一節時整份停下，並指名那個標題" {
  _module core/state.py
  printf '## 覆蓋率審計\n\n### 流程 → 測試介面\n' >"${WORK}/doc/TEST-PLAN.md"

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  # 「找不到」不能省：漏列那條規則的下一步訊息裡也有「模組 → 測試介面」這幾個字，
  # 只斷言標題的話，這一節整個消失時這則規格照樣會綠——突變檢查抓到過一次。
  [[ "${output}" == *"找不到"*"模組 → 測試介面"* ]]
}

@test "TEST-PLAN.md 不在時整份停下，並指名那個路徑" {
  _module core/state.py

  run "${LINT}" "${WORK}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"doc/TEST-PLAN.md"* ]]
}

@test "不以 .py 或 .sh 結尾的列在稽核範圍外，不參與空列檢查" {
  _module core/state.py
  _plan <<'ROWS'
| `core/state` | T2 | 已落地 |
| `web/` | T11 | 已落地 |
ROWS

  run "${LINT}" "${WORK}"
  [ "${status}" -eq 0 ]
}
