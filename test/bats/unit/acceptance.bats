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
# ── 未涵蓋不是通過 ────────────────────────────────────────────────────────

@test "一條檢查點對不到任何規格時，判定為未涵蓋" {
  start_map
  checkpoint 1 '沒有人在驗的檢查點' ""

  report

  [[ "${output}" == *"檢查點 1  未涵蓋"* ]]
}

@test "未涵蓋的檢查點讓整份報表非零結束——未涵蓋不是通過" {
  # 這一則是整份報表存在的理由。少了它，一個沒有人在驗的檢查點與一個驗過的
  # 檢查點，在輸出與結束碼上完全一樣。
  start_map
  checkpoint 1 '沒有人在驗的檢查點' ""

  report

  [ "${status}" -ne 0 ]
}

@test "未涵蓋與未通過在輸出上分得開——兩者要做的事不同" {
  start_map
  checkpoint 1 '沒人驗的' ""
  checkpoint 2 '會紅的' "'test/pytest/unit/test_red.py'"

  report

  [[ "${output}" == *"檢查點 1  未涵蓋"* ]]
  [[ "${output}" == *"檢查點 2  未通過"* ]]
}

@test "未涵蓋的理由被印出來，但判定仍然是未涵蓋" {
  # 理由是給讀者看的，不是給判定看的。寫得出理由不代表那個洞被補起來了。
  start_map
  cat >>"${MAP}" <<'TOML'

[[milestone.checkpoint]]
number = 1
text = '沒人驗的'
specs = []
uncovered = 'dump 尚不支援改動既有條目'
TOML

  report

  [[ "${output}" == *"dump 尚不支援改動既有條目"* ]]
  [[ "${output}" == *"檢查點 1  未涵蓋"* ]]
}

# ── 指到不存在的東西是失敗，不是安靜跳過 ──────────────────────────────────

@test "對照表指到不存在的規格檔時大聲失敗" {
  start_map
  checkpoint 1 '指錯了' "'test/pytest/unit/test_不存在.py'"

  report

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"test_不存在.py"* ]]
  # 解析階段擋下的，不是規格自己跑紅的——兩者都非零結束（突變檢查抓到的）。
  [[ "${output}" == *"指不到東西"* ]]
}

@test "對照表指到存在的檔案、但不存在的測試名時同樣失敗" {
  # 這一則才是真的缺口：檔案在，所以「檔案存在嗎」那種檢查會放它過去，而
  # pytest 對收不到的 node id 的處置不會自己變成一條紅色的檢查點。
  start_map
  checkpoint 1 '指錯了' "'test/pytest/unit/test_green.py::test_這條不存在'"

  report

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"test_這條不存在"* ]]
  # 解析階段擋下的，不是規格自己跑紅的——兩者都非零結束（突變檢查抓到的）。
  [[ "${output}" == *"指不到東西"* ]]
}

@test "bats 規格檔裡不存在的測試名也失敗，不被 bats 的過濾器安靜吃掉" {
  # bats --filter 對比不到任何測試的樣式回 0，跑了零條測試——那正是靜默通過。
  start_map
  checkpoint 1 '指錯了' "'test/bats/unit/green.bats::這條不存在'"

  report

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"這條不存在"* ]]
  # 解析階段擋下的，不是規格自己跑紅的——兩者都非零結束（突變檢查抓到的）。
  [[ "${output}" == *"指不到東西"* ]]
}

@test "對照表指到不存在的規格時，一條判定都不印" {
  # 一份對照表已經對不上的報表，它印出來的「通過」沒有意義。
  start_map
  checkpoint 1 '會過的' "'test/pytest/unit/test_green.py'"
  checkpoint 2 '指錯了' "'test/pytest/unit/test_不存在.py'"

  report

  [[ "${output}" != *"檢查點 1"* ]]
}

@test "bats 規格檔也能被指名並執行" {
  start_map
  checkpoint 1 '由 bats 驗的' "'test/bats/unit/green.bats'"

  report

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"檢查點 1  通過"* ]]
}

@test "bats 規格檔裡的單一測試也能被指名" {
  start_map
  checkpoint 1 '由 bats 驗的' "'test/bats/unit/green.bats::一條會過的 bats 規格'"

  report

  [ "${status}" -eq 0 ]
}

# ── 缺工具不得靜默通過 ────────────────────────────────────────────────────

@test "跑得動規格的工具不在時大聲失敗，不回報通過" {
  # 與 test.sh 的「缺工具不得靜默通過」同一條（不變式 2）：一支因為跑不動規格
  # 而回 0 的報表，比沒有報表更糟——它看起來與全綠一模一樣。
  # 拿掉工具只能靠重建 PATH：腳本以 command -v 判定，覆寫不掉。
  start_map
  checkpoint 1 '會過的' "'test/pytest/unit/test_green.py'"

  local bin="${WORK}/bin"
  mkdir -p "${bin}"
  local tool
  for tool in bash dirname python3; do
    ln -sf "$(command -v "${tool}")" "${bin}/${tool}"
  done

  run env -i PATH="${bin}" HOME="${WORK}" \
    CM_IN_TEST_IMAGE=1 CM_ACCEPTANCE_MAP="${MAP}" CM_ACCEPTANCE_ROOT="${ROOT}" \
    "${SCRIPT}" v0.1.0

  [ "${status}" -ne 0 ]
  # 「輸出裡有 pytest 這個字」連 traceback 都滿足。要斷言它說的是那支工具不在
  # PATH 上（突變檢查抓到的）。
  [[ "${output}" == *"pytest 不在 PATH 上"* ]]
}
# ── 摘要，以及對照表本身壞掉時 ──────────────────────────────────────────

@test "摘要印出通過、未通過、未涵蓋各幾條" {
  start_map
  checkpoint 1 '會過的' "'test/pytest/unit/test_green.py'"
  checkpoint 2 '會紅的' "'test/pytest/unit/test_red.py'"
  checkpoint 3 '沒人驗的' ""

  report

  [[ "${output}" == *"1 通過／1 未通過／1 未涵蓋"* ]]
}

@test "對照表裡沒有這個 milestone 時大聲失敗，不回報零條全部通過" {
  # 打錯一個版號而得到一份綠色的空報表，是這支腳本能出的最糟的錯。
  start_map
  checkpoint 1 '會過的' "'test/pytest/unit/test_green.py'"

  report v9.9.9

  [ "${status}" -ne 0 ]
  [[ "${output}" != *"檢查點 1"* ]]
}

@test "找不到 milestone 時列出對照表裡有哪些" {
  start_map
  checkpoint 1 '會過的' "'test/pytest/unit/test_green.py'"

  report v9.9.9

  [[ "${output}" == *"v0.1.0"* ]]
}

@test "對照表不存在時大聲失敗，訊息說得出下一步" {
  # 只斷言結束碼非零分不出「大聲失敗」與「一個 Python traceback」——兩者都非零，
  # 而後者說不出下一步。突變檢查抓到的：把存在性檢查拿掉，這一則原本仍是綠的。
  rm -f "${MAP}"

  report

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"下一步："* ]]
}

@test "對照表解析不了時大聲失敗，並指名那份檔案" {
  printf 'this is not toml =\n' >"${MAP}"

  report

  [ "${status}" -ne 0 ]
  # 指名那份檔案還不夠：找不到 milestone 的訊息裡也有同一個路徑。要斷言它說的是
  # 「解析不了」，這一則才分得出那兩種失敗（突變檢查抓到的）。
  [[ "${output}" == *"解析不了"* ]]
  [[ "${output}" == *"${MAP}"* ]]
}

@test "沒有給 milestone 參數時以用法結束，不預設跑某一版" {
  CM_ACCEPTANCE_MAP="${MAP}" CM_ACCEPTANCE_ROOT="${ROOT}" run "${SCRIPT}"

  [ "${status}" -ne 0 ]
}

# ── 突變：只有轉紅的那一條被報成未通過 ────────────────────────────────────

@test "某一條檢查點的規格轉紅時，被報成未通過的只有那一條" {
  # 一份把整版一起報紅的報表，說不出是哪一條壞了——說不出來的報表，修的時候
  # 只能靠猜。與 coverage_gate 的「被指名的只有 core」同一個保證。
  start_map
  checkpoint 1 '第一條' "'test/pytest/unit/test_green.py'"
  checkpoint 2 '第二條' "'test/pytest/unit/test_red.py'"
  checkpoint 3 '第三條' "'test/pytest/unit/test_green.py'"

  report

  [[ "${output}" == *"檢查點 2  未通過"* ]]
  [[ "${output}" != *"檢查點 1  未通過"* ]]
  [[ "${output}" != *"檢查點 3  未通過"* ]]
}

@test "有一條檢查點未通過時，整份報表非零結束" {
  start_map
  checkpoint 1 '會紅的' "'test/pytest/unit/test_red.py'"

  report

  [ "${status}" -ne 0 ]
}

@test "未通過的檢查點把規格自己的輸出帶出來，不是只說一句未通過" {
  start_map
  checkpoint 1 '會紅的' "'test/pytest/unit/test_red.py'"

  report

  [[ "${output}" == *"test_一條會紅的規格"* ]]
}


# ── 人工檢查點與簽核（#355，doc/ACCEPTANCE-SIGNOFF.md） ─────────────────────
#
# 人工檢查點是「只能由真人驗收」的那一種。它不能因為機器跑完了就變綠，也不能讓 main
# 上的 job 一直是紅的：所以報表分三段，閘門分兩種。簽核只核對格式與網址的形狀——它
# 擋得住「沒寫」「寫錯地方」「寫錯格式」，擋不住「網址的內容不是這一條的證據」。

report_with() {
  CM_ACCEPTANCE_MAP="${MAP}" CM_ACCEPTANCE_ROOT="${ROOT}" run "${SCRIPT}" "$@"
}

# 一條人工檢查點。第三個參數是簽核那一段的內容（TOML 的鍵值），空的就沒有簽核。
manual_checkpoint() {
  local number="$1" text="$2" signoff="${3:-}"
  cat >>"${MAP}" <<TOML

[[milestone.checkpoint]]
number = ${number}
text = '${text}'
manual = true
TOML
  if [[ -n "${signoff}" ]]; then
    signoff_block "${signoff}"
  fi
}

# 把簽核接在對照表最後一條檢查點底下——不論那一條是不是人工的。
signoff_block() {
  printf '\n[milestone.checkpoint.signoff]\n%s\n' "$1" >>"${MAP}"
}

GOOD_SIGNOFF="by = '王小明 <ming@example.com>'
date = 2026-12-01
evidence = 'https://github.com/ycpss91255-research/config_manager/issues/123#issuecomment-456'
summary = '未參與開發的同事照步驟操作，五項任務都完成'"

@test "人工檢查點沒有簽核時，列為人工待驗" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'

  report

  [[ "${output}" == *"檢查點 2  人工待驗"* ]]
}

@test "人工待驗時，預設的閘門判整體未通過，以 1 結束" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'

  report

  [ "${status}" -eq 1 ]
}

@test "--gate overall 與不帶 --gate 相同：人工待驗時以 1 結束" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'

  report_with --gate overall v0.1.0

  [ "${status}" -eq 1 ]
}

@test "--gate automated 時，人工待驗不讓結束碼變紅" {
  # main 上的 acceptance job 用這個閘門：它抓的是自動檢查退步與對照表壞掉，
  # 人工項目要等真人，不應讓 main 一直是紅的。
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'

  report_with --gate automated v0.1.0

  [ "${status}" -eq 0 ]
}

@test "--gate automated 時，人工待驗仍印在報表上" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'

  report_with --gate automated v0.1.0

  [[ "${output}" == *"檢查點 2  人工待驗"* ]]
}

@test "--gate automated 時，自動檢查全過、仍有人工待驗，整體那一段仍判未通過" {
  # 閘門只決定結束碼，不改整體的判定：main 上的 job 是綠的，人工待驗也不能被讀成通過。
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'

  report_with --gate automated v0.1.0

  [[ "${output}" == *"自動檢查通過，人工待驗 1 條——整體未通過"* ]]
}

@test "--gate automated 時，自動檢查未通過仍以 1 結束" {
  start_map
  checkpoint 1 '會紅的' "'test/pytest/unit/test_red.py'"

  report_with --gate automated v0.1.0

  [ "${status}" -eq 1 ]
}

@test "--gate automated 時，未涵蓋仍以 1 結束——未涵蓋不是人工待驗" {
  start_map
  checkpoint 1 '沒人驗的' ""

  report_with --gate automated v0.1.0

  [ "${status}" -eq 1 ]
}

@test "不認得的 --gate 以 2 結束" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"

  report_with --gate lenient v0.1.0

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"--gate 只認 overall 與 automated"* ]]
}

@test "人工檢查點有合格的簽核時，列為已簽核並印出簽核的證據網址" {
  start_map
  manual_checkpoint 1 '只能由真人驗收的' "${GOOD_SIGNOFF}"

  report

  [[ "${output}" == *"檢查點 1  已簽核"*"https://github.com/ycpss91255-research/config_manager/issues/123#issuecomment-456"* ]]
}

@test "自動檢查全部通過、人工檢查點都已簽核時，整體通過，以 0 結束" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的' "${GOOD_SIGNOFF}"

  report

  [ "${status}" -eq 0 ]
}

@test "簽核寫在自動檢查點上時以 2 結束——簽核不能把規格變成通過" {
  start_map
  checkpoint 1 '會紅的' "'test/pytest/unit/test_red.py'"
  signoff_block "${GOOD_SIGNOFF}"

  report

  [ "${status}" -eq 2 ]
}

@test "簽核寫在未涵蓋的檢查點上時以 2 結束——簽核不能取代沒做的驗收" {
  start_map
  checkpoint 1 '沒人驗的' ""
  signoff_block "${GOOD_SIGNOFF}"

  report

  [ "${status}" -eq 2 ]
}

@test "簽核缺 summary 時以 2 結束，並指名那一欄" {
  start_map
  manual_checkpoint 1 '只能由真人驗收的' "by = '王小明 <ming@example.com>'
date = 2026-12-01
evidence = 'https://github.com/ycpss91255-research/config_manager/issues/123'"

  report

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"summary："* ]]
}

@test "簽核的 by 是空的時以 2 結束，並指名那一欄" {
  start_map
  manual_checkpoint 1 '只能由真人驗收的' "by = ''
date = 2026-12-01
evidence = 'https://github.com/ycpss91255-research/config_manager/issues/123'
summary = '做了什麼'"

  report

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"by："* ]]
}

@test "簽核的 date 是字串而不是日期時以 2 結束，並指名那一欄" {
  start_map
  manual_checkpoint 1 '只能由真人驗收的' "by = '王小明 <ming@example.com>'
date = '2026-12-01'
evidence = 'https://github.com/ycpss91255-research/config_manager/issues/123'
summary = '做了什麼'"

  report

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"date："* ]]
}

@test "簽核的 evidence 不是這個 repo 的 issue 或 PR 時以 2 結束，並指名那一欄" {
  start_map
  manual_checkpoint 1 '只能由真人驗收的' "by = '王小明 <ming@example.com>'
date = 2026-12-01
evidence = 'https://example.com/screenshot.png'
summary = '做了什麼'"

  report

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"evidence："* ]]
}

@test "簽核有問題時，指名是哪一條檢查點" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 7 '只能由真人驗收的' "by = ''
date = 2026-12-01
evidence = 'https://github.com/ycpss91255-research/config_manager/issues/123'
summary = '做了什麼'"

  report

  [[ "${output}" == *"檢查點 7 的簽核"* ]]
}

@test "同一條檢查點同時有 specs 與 manual = true 時以 2 結束" {
  start_map
  checkpoint 1 '說不清楚的' "'test/pytest/unit/test_green.py'"
  printf 'manual = true\n' >>"${MAP}"

  report

  [ "${status}" -eq 2 ]
}

@test "沒有人工檢查點時，報表照樣印出自動檢查、人工待驗清單、整體三段" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"

  report

  [[ "${output}" == *"【自動檢查】"*"【人工待驗清單】"*"【整體】"* ]]
}

# ── 目標版本（--target） ──────────────────────────────────────────────────

# 對照表頂層的 target 必須寫在第一個 [[milestone]] 之前（TOML 的頂層鍵）。
start_map_with_target() {
  printf "target = '%s'\n\n" "$1" >"${MAP}"
  cat >>"${MAP}" <<'TOML'
[[milestone]]
id = 'v0.1.0'
title = '替身'
TOML
}

milestone() {
  printf "\n[[milestone]]\nid = '%s'\ntitle = '替身'\n" "$1" >>"${MAP}"
}

@test "--target 跑對照表頂層 target 指的那一版" {
  start_map_with_target v0.2.0
  checkpoint 1 'v0.1.0 的' "'test/pytest/unit/test_red.py'"
  milestone v0.2.0
  checkpoint 1 'v0.2.0 的' "'test/pytest/unit/test_green.py'"

  report_with --target

  [ "${status}" -eq 0 ]
}

@test "對照表沒有寫 target 時，--target 以 2 結束" {
  start_map
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"

  report_with --target

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"沒有寫頂層的 target"* ]]
}

@test "target 寫的版本不在對照表裡時，--target 以 2 結束" {
  start_map_with_target v9.9.9
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"

  report_with --target

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"沒有 milestone v9.9.9"* ]]
}

@test "--target 的目標已整體通過、之後還有版本時以 2 結束，並指名下一版" {
  # 留在已完成的版本上，這個 job 會永遠報綠——與原本寫死 v0.1.0 是同一個問題。
  start_map_with_target v0.1.0
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  milestone v0.2.0
  checkpoint 1 'v0.2.0 的' "'test/pytest/unit/test_red.py'"

  report_with --target

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"target 改成 v0.2.0"* ]]
}

@test "--target 的目標已整體通過、之後只有不是版本的 milestone 時照常以 0 結束" {
  start_map_with_target v0.1.0
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  milestone core-flow
  checkpoint 1 'core-flow 的' "'test/pytest/unit/test_red.py'"

  report_with --target

  [ "${status}" -eq 0 ]
}

@test "--target 的目標還沒整體通過時不觸發防呆，照常以 1 結束" {
  start_map_with_target v0.1.0
  checkpoint 1 '自動的' "'test/pytest/unit/test_green.py'"
  manual_checkpoint 2 '只能由真人驗收的'
  milestone v0.2.0
  checkpoint 1 'v0.2.0 的' "'test/pytest/unit/test_green.py'"

  report_with --target

  [ "${status}" -eq 1 ]
}
