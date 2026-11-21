#!/usr/bin/env bats
#
# script/release.sh — 哪一種 tag 發得出去（T19、#150）。
#
# 被觀察的不是驗收報表的內容——那是 acceptance.bats 的事——而是**這支腳本怎麼處置
# 那份報表的結束碼**：
#
#   rc tag      不論綠紅都建立 release。擋下紅色的 rc 等於讓 N 無法遞增，
#               而 rc 的用途就是記錄「現在走到哪」。
#   正式 tag    只有報表全數通過才建得起來。這一條要由工具擋，不是靠記得
#               ——否則它就是又一條「寫在文件裡等於不存在」的規範（§0.4）。
#   報表壞掉    連 rc 都不建。那不是「未通過」，是根本沒有報表。
#
# #407 之後再加三件事：tag 不能刪（tag ruleset 擋刪除與 force push，定案 14），所以每一則
# 下一步都指向推導出來的下一個名字——rc 推下一個 rc，正式 tag 改發下一個 patch 版；編號只驗
# 「比它小的都在」、推導取最小的缺號；patch 版用 vX.Y.0 的檢查點與 badge。
#
# **餵的是替身報表，不是真的跑一次驗收**（CM_RELEASE_ACCEPTANCE），與
# acceptance.bats 的 CM_ACCEPTANCE_MAP 同一個先例：真的報表結果若參與這些規格，
# 它們會在任何一條檢查點轉紅的那天跟著轉紅——而一組結果取決於別的規格有沒有跑的
# 規格，測的不是這支腳本。
#
# **gh 以 PATH 上的假指令替換**，與 lint_checkpoints.bats 同一個手法。這裡尤其
# 必要：這支腳本真的跑起來會在 GitHub 上留下一個對外可見的 release。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  SCRIPT="${REPO_ROOT}/script/release.sh"
  WORK="$(mktemp -d)"
  STUB="${WORK}/bin"
  mkdir -p "${STUB}"
  PATH="${STUB}:${PATH}"
  export PATH

  # 假 gh 把每一個參數各記一行。斷言的是「發出了什麼呼叫」，不是它的輸出。
  GH_CALLS="${WORK}/gh-calls"
  export GH_CALLS
  cat >"${STUB}/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"${GH_CALLS}"
STUB
  chmod +x "${STUB}/gh"

  ACCEPTANCE="${WORK}/acceptance"
  export CM_RELEASE_ACCEPTANCE="${ACCEPTANCE}"

  # tag 由一個真的 repo 供應：rc 編號的推導讀的是 git tag，餵假的就測不到那件事。
  mkdir -p "${WORK}/repo"
  cd "${WORK}/repo" || return 1
  git init -q .
  git config user.name spec
  git config user.email spec@example.invalid
  git commit -q --allow-empty -m "chore(base): 起點"

  # 覆蓋率的重新量測（#354）以替身代替：預設量到的數字，與 repo 裡那份 badge 畫的數字相同，
  # 所以預設是「一致」——其餘規格不受這一項影響。
  COVERAGE="${WORK}/coverage"
  export CM_RELEASE_COVERAGE="${COVERAGE}"
  stub_coverage "${SAME[@]}"
  commit_badge v0.1.0 "${SAME[@]}"
}

# 四層各 90/100 的一份量測結果（tsv 的一行一層，以 | 代替 tab）。
SAME=('core|90|100|85|pass' 'io|90|100|85|pass' 'api|90|100|85|pass' 'web|90|100|85|pass')

# 替身量測：把給定的幾行寫成 tsv，交到 release.sh 指定的位置。
stub_coverage() {
  printf '%s\n' "$@" | tr '|' '\t' >"${WORK}/measured.tsv"
  cat >"${COVERAGE}" <<STUB
#!/usr/bin/env bash
cp '${WORK}/measured.tsv' "\$1"
STUB
  chmod +x "${COVERAGE}"
}

# 替身量測失敗：非零結束、什麼都沒寫。
stub_coverage_fails() {
  printf '#!/usr/bin/env bash\nexit 1\n' >"${COVERAGE}"
  chmod +x "${COVERAGE}"
}

# repo 裡進版控的 badge，用真的 coverage_badge.sh 從給定的版本與數字畫出來。
commit_badge() {
  local version="$1"
  shift
  printf '%s\n' "$@" | tr '|' '\t' >"${WORK}/committed.tsv"
  mkdir -p "${WORK}/repo/doc/badge"
  "${REPO_ROOT}/script/coverage_badge.sh" "${version}" "${WORK}/committed.tsv" \
    >"${WORK}/repo/doc/badge/coverage.svg"
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# 一份全綠的替身報表。最後兩行（摘要行與判定行）照現行 acceptance.sh 的格式寫：release.sh
# 靠這兩行分辨「一份報表」與「一段建置錯誤」（#377），格式不對的替身會被當成報表不能信。
# <版本> 是報表說它驗的那一版（預設 v0.1.0）。替身把收到的參數記在 acceptance-args：patch 版
# 的規格看的是 release.sh 交給驗收的是哪一版（#407）。
stub_green() {
  local version="${1:-v0.1.0}"
  cat >"${ACCEPTANCE}" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" >'${WORK}/acceptance-args'
printf 'acceptance: ${version} 資料層與唯讀介面 —— 5 條檢查點\n'
printf '  檢查點 1  通過  ADR lint 可攔截\n'
printf 'acceptance: ${version} —— 5 通過／0 未通過／0 未涵蓋／0 人工待驗／0 已簽核\n'
printf 'acceptance: ${version} 通過（--gate overall）\n'
STUB
  chmod +x "${ACCEPTANCE}"
}

# 一份有一條紅的替身報表。結束碼 1 == 有檢查點未通過或未涵蓋。<版本> 同上。
stub_red() {
  local version="${1:-v0.1.0}"
  cat >"${ACCEPTANCE}" <<STUB
#!/usr/bin/env bash
printf '%s\n' "\$@" >'${WORK}/acceptance-args'
printf 'acceptance: ${version} 資料層與唯讀介面 —— 5 條檢查點\n'
printf '  檢查點 1  通過  ADR lint 可攔截\n'
printf '  檢查點 3  未通過  修改清單檔後寫出，註解與欄位順序完整保留\n'
printf 'acceptance: ${version} —— 4 通過／1 未通過／0 未涵蓋／0 人工待驗／0 已簽核\n'
printf 'acceptance: ${version} 未通過（--gate overall）\n'
exit 1
STUB
  chmod +x "${ACCEPTANCE}"
}

# 自動檢查全綠，只剩一條人工檢查點還沒簽核（#355）。結束碼 1 == 整體未通過。
stub_manual_pending() {
  cat >"${ACCEPTANCE}" <<'STUB'
#!/usr/bin/env bash
printf 'acceptance: v0.1.0 資料層與唯讀介面 —— 2 條檢查點，閘門 --gate overall\n'
printf '【自動檢查】\n'
printf '  檢查點 1  通過  ADR lint 可攔截\n'
printf '【人工待驗清單】\n'
printf '  檢查點 2  人工待驗  由未參與開發的同事在無人指導下完成\n'
printf '【整體】\n'
printf 'acceptance: v0.1.0 —— 1 通過／0 未通過／0 未涵蓋／1 人工待驗／0 已簽核\n'
printf 'acceptance: v0.1.0 未通過（--gate overall）\n'
exit 1
STUB
  chmod +x "${ACCEPTANCE}"
}

# **真的** acceptance.sh，餵替身對照表與替身規格（#355）：自動檢查一條會過，另一條是還沒
# 簽核的人工檢查點。其餘規格用替身報表；這一組要驗的是兩支串起來時 release.sh 用的是整體
# 閘門，替身報表驗不到這件事。真的對照表不參與。
real_acceptance_with_manual_pending() {
  local root="${WORK}/acceptance-root" map="${WORK}/checkpoints.toml"
  mkdir -p "${root}/test/pytest/unit"
  cat >"${root}/test/pytest/unit/test_green.py" <<'PY'
def test_一條會過的規格():
    assert True
PY
  cat >"${map}" <<'TOML'
[[milestone]]
id = 'v0.1.0'
title = '替身'

[[milestone.checkpoint]]
number = 1
text = '自動的'
specs = ['test/pytest/unit/test_green.py']

[[milestone.checkpoint]]
number = 2
text = '只能由真人驗收的'
manual = true
TOML
  cat >"${ACCEPTANCE}" <<STUB
#!/usr/bin/env bash
CM_ACCEPTANCE_MAP='${map}' CM_ACCEPTANCE_ROOT='${root}' exec '${REPO_ROOT}/script/acceptance.sh' "\$@"
STUB
  chmod +x "${ACCEPTANCE}"
}

# 對照表自己壞掉。結束碼 2 == 這份報表不能信。
stub_broken() {
  cat >"${ACCEPTANCE}" <<'STUB'
#!/usr/bin/env bash
printf 'acceptance: 對照表 doc/acceptance-checkpoints.toml 有 1 條規格參照指不到東西\n' >&2
exit 2
STUB
  chmod +x "${ACCEPTANCE}"
}

# 以 <結束碼> 結束，輸出卻不是一份報表：在印出任何判定之前就停了（#377 重現到的就是這種
# 輸出——建映像失敗時只有一段建置錯誤，結束碼是 docker 的 1）。
stub_not_a_report() {
  cat >"${ACCEPTANCE}" <<STUB
#!/usr/bin/env bash
printf 'acceptance: 建置 config_manager-test-tools:local\n'
printf 'ERROR: failed to solve: process "/bin/sh -c apt-get update" did not complete successfully: exit code: 100\n' >&2
exit $1
STUB
  chmod +x "${ACCEPTANCE}"
}

# 有判定行、卻少了摘要行（#377）。一份跑完的報表兩行都有；這一個與下一個替身各缺一半，
# 讓兩項核對各自有規格看著。
stub_without_summary() {
  cat >"${ACCEPTANCE}" <<'STUB'
#!/usr/bin/env bash
printf 'acceptance: v0.1.0 資料層與唯讀介面 —— 5 條檢查點\n'
printf '  檢查點 3  未通過  修改清單檔後寫出，註解與欄位順序完整保留\n'
printf 'acceptance: v0.1.0 未通過（--gate overall）\n'
exit 1
STUB
  chmod +x "${ACCEPTANCE}"
}

# 報表寫未通過、結束碼卻是 0（#377）：兩者對不上，說不出哪一個才是真的。
stub_verdict_disagrees_with_exit() {
  stub_red
  sed -i 's/^exit 1$/exit 0/' "${ACCEPTANCE}"
}

# **真的** acceptance.sh，建映像那一步失敗（#377）。不真的建：PATH 上的假 docker 對任何呼叫
# 都回 1，與假 gh 同一個手法。規格本身在映像裡跑、CM_IN_TEST_IMAGE 是設著的，設著它
# acceptance.sh 就不去建映像，所以只對它拿掉。
real_acceptance_build_fails() {
  cat >"${STUB}/docker" <<'STUB'
#!/usr/bin/env bash
printf 'ERROR: failed to solve: process "/bin/sh -c apt-get update" did not complete successfully: exit code: 100\n' >&2
exit 1
STUB
  chmod +x "${STUB}/docker"
  cat >"${ACCEPTANCE}" <<STUB
#!/usr/bin/env bash
exec env -u CM_IN_TEST_IMAGE -u CM_TEST_LOCAL '${REPO_ROOT}/script/acceptance.sh' "\$@"
STUB
  chmod +x "${ACCEPTANCE}"
}

# 假 gh 收到的參數裡，緊接在 <旗標> 後面的那一個值。
gh_value_after() {
  local want="$1" previous="" line
  while IFS= read -r line; do
    if [[ "${previous}" == "${want}" ]]; then
      printf '%s' "${line}"
      return 0
    fi
    previous="${line}"
  done <"${GH_CALLS}"
  return 1
}

gh_was_called() {
  [[ -s "${GH_CALLS}" ]]
}

# 「正式 tag 前的核對」（#407 調整需求第 10 點）：每一則叫人打正式 tag <版號> 的下一步都帶這一段。
# 斷言它的骨架——先推 <版號> 的 rc，在同一個 SHA 上整體判定通過、badge 一致，兩者都成立才打；
# 期望的字句照 #407 定案裡那段文字寫。
carries_formal_check() {
  local version="$1"
  [[ "${output}" == *"正式 tag 前的核對：先推 script/release.sh --next ${version} 印出的 rc"*"在同一個 SHA 上確認那個 rc 的 release notes 裡 --gate overall 的判定是通過、覆蓋率 badge 一致，兩者都成立才打正式 tag ${version}"* ]]
}

# 覆蓋率量到的數字與 repo 裡那份 badge 不同（api 少一行）。
stub_coverage_differs() {
  stub_coverage 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|89|100|85|pass' 'web|90|100|85|pass'
}

# patch 版的規格共用的起點：v0.9.0 已經有正式 tag（發壞、沒有 release 也一樣），替身報表與
# 版控裡的 badge 都是 v0.9.0 的——patch 版驗收與 badge 都用 vX.Y.0（#407）。
given_v090_tagged() {
  git tag v0.9.0
  stub_green v0.9.0
  commit_badge v0.9.0 "${SAME[@]}"
}

# ── rc：紅的也發得出去 ────────────────────────────────────────────────────

@test "報表未通過時 rc 的 release 仍然建立" {
  # 這一則是整張 issue 的核心決定。擋下紅色的 rc 等於讓 N 無法遞增，
  # 那就失去 rc 的意義了——rc1 可以是紅的，rc2 轉綠。
  stub_red

  run "${SCRIPT}" v0.1.0-rc1

  [ "${status}" -eq 0 ]
}

@test "報表未通過的 rc，標題上就標示未通過" {
  # 建得起來還不夠：一個看不出紅綠的 release，與一份手寫的「已通過」表格
  # 一樣沒有資訊。
  stub_red

  run "${SCRIPT}" v0.1.0-rc1

  [[ "$(gh_value_after --title)" == *"未通過"* ]]
}

@test "報表通過的 rc，標題上標示通過" {
  stub_green

  run "${SCRIPT}" v0.1.0-rc1

  [[ "$(gh_value_after --title)" == *"通過"* ]]
}

@test "rc 建的是預發布" {
  # rc 不是正式版本。標成正式版本的 rc 會出現在「最新版本」上。
  stub_green

  run "${SCRIPT}" v0.1.0-rc1

  grep -qx -- --prerelease "${GH_CALLS}"
}

# ── 報表進 release notes，也進附加檔案 ────────────────────────────────────

@test "通過與否寫在 release notes 的第一行" {
  stub_red

  run "${SCRIPT}" v0.1.0-rc1

  [[ "$(head -n 1 "$(gh_value_after --notes-file)")" == *"未通過"* ]]
}

@test "報表的逐條判定進 release notes" {
  # 只寫一句「未通過」的 release notes，讀者還是得自己去別的地方找是哪一條。
  stub_red

  run "${SCRIPT}" v0.1.0-rc1

  grep -q "檢查點 3  未通過" "$(gh_value_after --notes-file)"
}

@test "報表同時作為附加檔案交給 gh" {
  # notes 會被編輯，附加檔案不會——要拿去比對的是後者。
  stub_green

  run "${SCRIPT}" v0.1.0-rc1

  grep -q "檢查點 1  通過" "$(grep -F "acceptance-v0.1.0-rc1.txt" "${GH_CALLS}")"
}

# ── 正式 tag：紅的發不出去 ────────────────────────────────────────────────

@test "報表未通過時非 rc 的正式 tag 被擋下" {
  stub_red

  run "${SCRIPT}" v0.1.0

  [ "${status}" -ne 0 ]
}

@test "正式 tag 被擋下時，一個 release 都沒有建立" {
  # 只斷言結束碼分不出「擋下來了」與「建了一個 release 然後才抱怨」。
  stub_red

  run "${SCRIPT}" v0.1.0

  ! gh_was_called
}

@test "正式 tag 被擋下時，訊息指出是哪一條檢查點" {
  # 說不出是哪一條的閘門，修的時候只能靠猜。
  stub_red

  run "${SCRIPT}" v0.1.0

  [[ "${output}" == *"檢查點 3"* ]]
}

@test "正式 tag 只因人工待驗被擋下時，訊息指名那一條人工檢查點" {
  # 自動檢查全綠、只差人工簽核時，沒有「未通過／未涵蓋」可列；不列出人工待驗那一條，
  # 擋下的訊息就說不出還差什麼。
  stub_manual_pending

  run "${SCRIPT}" v0.1.0

  [[ "${output}" == *"檢查點 2  人工待驗"* ]]
}

@test "真的驗收報表自動檢查全過、只剩人工待驗時，正式 tag 以 1 結束" {
  # release.sh 不帶 --gate，用的是整體：main 上的 job 是綠的，不代表這一版發得出去（#355）。
  real_acceptance_with_manual_pending

  run "${SCRIPT}" v0.1.0

  [ "${status}" -eq 1 ]
}

@test "真的驗收報表只剩人工待驗時，正式 tag 一個 release 都沒有建立" {
  real_acceptance_with_manual_pending

  run "${SCRIPT}" v0.1.0

  ! gh_was_called
}

@test "真的驗收報表只剩人工待驗時，擋下的訊息指名那一條人工檢查點" {
  # 替身報表那一則（上面）的格式是照著寫的；這一則確認真的報表也對得上 release.sh 的比對。
  real_acceptance_with_manual_pending

  run "${SCRIPT}" v0.1.0

  [[ "${output}" == *"檢查點 2  人工待驗"* ]]
}

@test "真的驗收報表只剩人工待驗時，rc 的 release 照常建立，標題標示未通過" {
  # 報表完整、回 1，是真正的未通過（#377）：release.sh 核對報表的結構，替身報表的格式是
  # 照著寫的，這一則確認真的報表通得過那道核對——否則每一個紅的 rc 都會被當成報表壞掉。
  real_acceptance_with_manual_pending

  run "${SCRIPT}" v0.1.0-rc1

  [ "$(gh_value_after --title)" = "v0.1.0-rc1 — 驗收未通過" ]
}

# ── 覆蓋率 badge（#354）：在 tag 指的 commit 上重新量測，畫出來要與版控裡的逐位元組相同 ──

@test "正式 tag，badge 的數字與重新量測不同時以 1 結束" {
  stub_green
  stub_coverage 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|89|100|85|pass' 'web|90|100|85|pass'

  run "${SCRIPT}" v0.1.0

  [ "${status}" -eq 1 ]
}

@test "正式 tag，badge 與重新量測不同時一個 release 都沒有建立" {
  stub_green
  stub_coverage 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|89|100|85|pass' 'web|90|100|85|pass'

  run "${SCRIPT}" v0.1.0

  ! gh_was_called
}

@test "正式 tag，badge 標的版本不是這一版時以 1 結束" {
  stub_green
  commit_badge v0.0.9 "${SAME[@]}"

  run "${SCRIPT}" v0.1.0

  [ "${status}" -eq 1 ]
}

@test "正式 tag 因 badge 被擋下時，訊息指出 badge 與重新量測畫出來的不同" {
  stub_green
  stub_coverage 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|89|100|85|pass' 'web|90|100|85|pass'

  run "${SCRIPT}" v0.1.0

  [[ "${output}" == *"doc/badge/coverage.svg 與這個 commit 重新量測畫出來的不同"* ]]
}

@test "rc tag，badge 與重新量測不同時 release 仍然建立" {
  stub_green
  stub_coverage 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|89|100|85|pass' 'web|90|100|85|pass'

  run "${SCRIPT}" v0.1.0-rc1

  gh_was_called
}

@test "rc tag，badge 與重新量測不同時 release notes 寫明" {
  stub_green
  stub_coverage 'core|90|100|85|pass' 'io|90|100|85|pass' 'api|89|100|85|pass' 'web|90|100|85|pass'

  run "${SCRIPT}" v0.1.0-rc1

  grep -qF '覆蓋率 badge：與這個 commit 重新量測畫出來的不同' "$(gh_value_after --notes-file)"
}

@test "重新量測失敗時，正式 tag 以 1 結束" {
  stub_green
  stub_coverage_fails

  run "${SCRIPT}" v0.1.0

  [ "${status}" -eq 1 ]
}

@test "重新量測失敗時，rc 的 release notes 寫明沒有量到" {
  stub_green
  stub_coverage_fails

  run "${SCRIPT}" v0.1.0-rc1

  grep -qF '覆蓋率 badge：這個 commit 的覆蓋率沒有量到' "$(gh_value_after --notes-file)"
}

@test "報表全數通過時正式 tag 建得起來" {
  # 沒有這一則，一支永遠擋下所有正式 tag 的腳本也是綠的。
  stub_green

  run "${SCRIPT}" v0.1.0

  [ "${status}" -eq 0 ]
}

# ── 報表壞掉不是未通過 ────────────────────────────────────────────────────

@test "對照表壞掉時連 rc 都不建立 release" {
  # acceptance.sh 的 2 說的是「這份報表不能信」，不是「這一版還沒做完」。
  # 附著一份壞掉報表的 release，正是這張 issue 要防的東西。
  stub_broken

  run "${SCRIPT}" v0.1.0-rc1

  ! gh_was_called
}

@test "對照表壞掉時大聲失敗，訊息說得出下一步" {
  # 只斷言結束碼非零分不出「大聲失敗」與「假 acceptance 的輸出被原樣吐出來」。
  stub_broken

  run "${SCRIPT}" v0.1.0-rc1

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"下一步："* ]]
}

@test "acceptance.sh 回 1 但輸出不是報表時，連 rc 都不建立 release" {
  # 回 1 不等於未通過：建映像失敗也回 1，那時一條判定都沒有。當成未通過，rc 就會帶著一段
  # 建置錯誤、標題寫「驗收未通過」發出去（#377 重現到的就是這樣）。
  stub_not_a_report 1

  run "${SCRIPT}" v0.1.0-rc1

  ! gh_was_called
}

@test "acceptance.sh 回 1 但輸出不是報表時以 2 結束——報表不能信，不是未通過" {
  stub_not_a_report 1

  run "${SCRIPT}" v0.1.0-rc1

  [ "${status}" -eq 2 ]
}

@test "acceptance.sh 回 0 但輸出不是報表時，正式 tag 一個 release 都沒有建立" {
  # 回 0 卻沒有「通過」的判定行，同樣說不出通過了什麼；正式 tag 在這裡發出去，是一個沒有
  # 報表的正式版。
  stub_not_a_report 0

  run "${SCRIPT}" v0.1.0

  ! gh_was_called
}

@test "輸出少了摘要行時，連 rc 都不建立 release" {
  stub_without_summary

  run "${SCRIPT}" v0.1.0-rc1

  ! gh_was_called
}

@test "結束碼與報表的判定行對不上時，正式 tag 一個 release 都沒有建立" {
  # 回 0 而報表寫未通過：照結束碼發出去，就是一個附著「未通過」報表的正式版。
  stub_verdict_disagrees_with_exit

  run "${SCRIPT}" v0.1.0

  ! gh_was_called
}

@test "真的 acceptance.sh 建映像失敗時，連 rc 都不建立 release" {
  # 替身報表是照著 #377 的重現寫的；這一則讓真的 acceptance.sh 走到建映像那一步、在那裡失敗。
  real_acceptance_build_fails

  run "${SCRIPT}" v0.1.0-rc1

  ! gh_was_called
}

# ── 編號由既有 tag 推導（#150、#407）────────────────────────────────────────
#
# 推導下一號：既有 tag 全部算進去（包括跳號被擋下而保留的），取最小的缺號，沒有缺號就是最大號
# 加 1。驗證收到的 tag：排除它自己，比它小的號碼都已經有 tag 就合法。

@test "一個 rc 都還沒有時，推導出來的是 rc1" {
  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc1" ]
}

@test "已經有 rc1 與 rc2 時，推導出來的是 rc3" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc3" ]
}

@test "推導取最小的缺號：rc1、rc2 之後誤推的 rc5 留著時，推導出來的是 rc3" {
  # 跳號被擋下的 rc5 刪不掉（#407 定案 14），所以它被算進去。看最大號會得到 rc6、看個數會得到
  # rc4，兩者都留下一個永遠補不上的缺號。
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  git tag v0.1.0-rc5

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc3" ]
}

@test "誤推的 rc5 留著時，推上來的 rc3 通過編號檢查" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  git tag v0.1.0-rc5
  git tag v0.1.0-rc3
  stub_green

  run "${SCRIPT}" v0.1.0-rc3

  [ "${status}" -eq 0 ]
}

@test "rc3 補上之後推導出來的是 rc4" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  git tag v0.1.0-rc3
  git tag v0.1.0-rc5

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc4" ]
}

@test "rc4 也補上之後推導出來的是 rc6，跳過留著的 rc5" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  git tag v0.1.0-rc3
  git tag v0.1.0-rc4
  git tag v0.1.0-rc5

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc6" ]
}

@test "跳過留著的 rc5 推上來的 rc6 通過編號檢查" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  git tag v0.1.0-rc3
  git tag v0.1.0-rc4
  git tag v0.1.0-rc5
  git tag v0.1.0-rc6
  stub_green

  run "${SCRIPT}" v0.1.0-rc6

  [ "${status}" -eq 0 ]
}

@test "別的版號的 rc 不影響這一版的推導" {
  git tag v0.2.0-rc7

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc1" ]
}

@test "--next 收的是版號：--next v0.9.1 印出 v0.9.1-rc1" {
  run "${SCRIPT}" --next v0.9.1

  [ "${output}" = "v0.9.1-rc1" ]
}

@test "patch 版號的 rc 從它自己的 rc1 起算，不接著 vX.Y.0 的 rc 編" {
  git tag v0.9.0-rc1
  git tag v0.9.0-rc2
  git tag v0.9.0

  run "${SCRIPT}" --next v0.9.1

  [ "${output}" = "v0.9.1-rc1" ]
}

@test "那個 patch 版號已經有 rc 時，--next 接著編，不撞上既有的 tag" {
  git tag v0.9.0
  git tag v0.9.1-rc1

  run "${SCRIPT}" --next v0.9.1

  [ "${output}" = "v0.9.1-rc2" ]
}

@test "--next 收到的不是版號（是一個 rc tag 名）時以 2 結束" {
  run "${SCRIPT}" --next v0.9.1-rc1

  [ "${status}" -eq 2 ]
}

@test "人工跳號推上來的 rc tag 被擋下" {
  # 人工指定會撞號或跳號。編號要由工具推導，就得有工具擋著人工指定的那一個。
  git tag v0.1.0-rc1
  git tag v0.1.0-rc5
  stub_green

  run "${SCRIPT}" v0.1.0-rc5

  [ "${status}" -ne 0 ]
}

@test "跳號被擋下時，訊息指名應該用哪一個編號" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc5
  stub_green

  run "${SCRIPT}" v0.1.0-rc5

  [[ "${output}" == *"v0.1.0-rc2"* ]]
}

@test "跳號被擋下時，下一步寫這個 tag 留著、改推 --next 印出的那一個" {
  # 跳號的 tag 刪不掉（tag ruleset 擋刪除，#407 定案 14）：叫人收回它的下一步照著做會被拒絕。
  git tag v0.1.0-rc1
  git tag v0.1.0-rc5
  stub_green

  run "${SCRIPT}" v0.1.0-rc5

  [[ "${output}" == *"下一步：這個 tag 留著（tag 不能刪），改推 script/release.sh --next v0.1.0 印出的那一個"* ]]
}

@test "重跑同一個 rc tag 不會因為它自己已經存在而被擋" {
  # tag 被推上來之後 CI 才跑，所以推導必須把這個 tag 自己排除在外。
  # 少了這一條，每一次 release 都會擋下自己。
  git tag v0.1.0-rc1
  stub_green

  run "${SCRIPT}" v0.1.0-rc1

  [ "${status}" -eq 0 ]
}

@test "正常的下一個 rc（rc1 已有，推上來的是 rc2）不被當成跳號" {
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  stub_green

  run "${SCRIPT}" v0.1.0-rc2

  [ "${status}" -eq 0 ]
}

@test "已經有更新的 rc3 時，重跑舊的 rc2 仍然通過編號檢查" {
  # 驗的是「比它小的都在」，不是「它是最大號加 1」：後者會讓重跑舊 tag 的 release 被擋下。
  git tag v0.1.0-rc1
  git tag v0.1.0-rc2
  git tag v0.1.0-rc3
  stub_green

  run "${SCRIPT}" v0.1.0-rc2

  [ "${status}" -eq 0 ]
}

@test "正常的下一個 patch（v0.9.0 已有，推上來的是 v0.9.1）不被當成跳號" {
  # v0.9.0 發壞、沒有 release 也一樣算「已經有 tag」：這也是「正式 tag 發壞之後，推導出的下一個
  # patch 發得出去」那一條。
  given_v090_tagged
  git tag v0.9.1

  run "${SCRIPT}" v0.9.1

  [ "${status}" -eq 0 ]
}

@test "比它小的 patch 號有缺時擋下：只有 v0.9.0 就推上來的 v0.9.2 以 2 結束" {
  given_v090_tagged
  git tag v0.9.2

  run "${SCRIPT}" v0.9.2

  [ "${status}" -eq 2 ]
}

@test "patch 號跳號被擋下時，一個 release 都沒有建立" {
  given_v090_tagged
  git tag v0.9.2

  run "${SCRIPT}" v0.9.2

  ! gh_was_called
}

@test "patch 號跳號被擋下時，下一步指向推導出的缺號 v0.9.1" {
  given_v090_tagged
  git tag v0.9.2

  run "${SCRIPT}" v0.9.2

  [[ "${output}" == *"下一步：這個 tag 留著（tag 不能刪），改推 script/release.sh --next v0.9.1 印出的那一個 rc"* ]]
}

@test "patch 版的 rc 在比它小的 patch 號有缺時也被擋下" {
  # 只有 v0.9.0 時推 v0.9.2-rc1：rc 也要比它小的 patch 號都有正式 tag。
  given_v090_tagged

  run "${SCRIPT}" v0.9.2-rc1

  [ "${status}" -eq 2 ]
}

# ── 正式 tag 發壞之後改發推導出的下一個 patch 版（#407）────────────────────────

@test "已有誤推而保留的 v0.9.5 時 v0.9.0 發壞，推導出的下一個 patch 是 v0.9.1" {
  # 取最小的缺號，不是最大號加 1：看最大號會得到 v0.9.6，留下 v0.9.1～v0.9.4 永遠補不上。
  given_v090_tagged
  git tag v0.9.5
  stub_red v0.9.0

  run "${SCRIPT}" v0.9.0

  [[ "${output}" == *"改發下一個 patch 版 v0.9.1（"* ]]
}

@test "已有誤推而保留的 v0.9.5 時，推上來的 v0.9.1 通過編號檢查" {
  given_v090_tagged
  git tag v0.9.5
  git tag v0.9.1

  run "${SCRIPT}" v0.9.1

  [ "${status}" -eq 0 ]
}

@test "v0.9.0 發壞時 v0.9.1 已被占用而且沒有 release，推導出的下一個 patch 是 v0.9.2" {
  given_v090_tagged
  git tag v0.9.1
  stub_red v0.9.0

  run "${SCRIPT}" v0.9.0

  [[ "${output}" == *"改發下一個 patch 版 v0.9.2（"* ]]
}

@test "連續兩次正式版發壞（v0.9.0、v0.9.1），v0.9.1 的下一步推導出的是 v0.9.2" {
  given_v090_tagged
  git tag v0.9.1
  stub_red v0.9.0

  run "${SCRIPT}" v0.9.1

  [[ "${output}" == *"改發下一個 patch 版 v0.9.2（"* ]]
}

@test "連續兩次正式版發壞（v0.9.0、v0.9.1）之後，v0.9.2 發得出去" {
  given_v090_tagged
  git tag v0.9.1
  git tag v0.9.2

  run "${SCRIPT}" v0.9.2

  [ "${status}" -eq 0 ]
}

@test "v0.9.0 發壞之後，推導出的 v0.9.1-rc1 發得出去" {
  given_v090_tagged

  run "${SCRIPT}" v0.9.1-rc1

  [ "${status}" -eq 0 ]
}

# ── patch 版用 vX.Y.0 的檢查點（#407）──────────────────────────────────────────

@test "patch 版交給驗收的是 vX.Y.0 那一版" {
  # patch 版不加功能，該驗的是那個 minor 的檢查點；對照表裡沒有 v0.9.1 這一版。
  given_v090_tagged

  run "${SCRIPT}" v0.9.1-rc1

  [ "$(cat "${WORK}/acceptance-args")" = "v0.9.0" ]
}

@test "patch 版的覆蓋率 badge 與 vX.Y.0 那一版畫出來的比對" {
  # 版控裡的 badge 是 v0.9.0 畫的；用 v0.9.1 畫，版本那一格就對不上。
  given_v090_tagged

  run "${SCRIPT}" v0.9.1-rc1

  grep -qF '覆蓋率 badge：與這個 commit 重新量測畫出來的相同' "$(gh_value_after --notes-file)"
}

@test "patch 版的 release 標題用 patch 版號" {
  given_v090_tagged

  run "${SCRIPT}" v0.9.1-rc1

  [ "$(gh_value_after --title)" = "v0.9.1-rc1 — 驗收通過" ]
}

@test "patch 版的 release 建在 patch 版號的 tag 上" {
  given_v090_tagged

  run "${SCRIPT}" v0.9.1-rc1

  [ "$(gh_value_after create)" = "v0.9.1-rc1" ]
}

@test "patch 版的 release notes 第一行寫驗收：vX.Y.0" {
  given_v090_tagged

  run "${SCRIPT}" v0.9.1-rc1

  [ "$(head -n 1 "$(gh_value_after --notes-file)")" = "## 驗收：v0.9.0 通過" ]
}

@test "patch 版的 vX.Y.0 不在對照表時（真的 acceptance.sh、替身對照表），連 rc 都不建立 release" {
  # 替身對照表只有 v0.1.0。v0.2.1 驗的是 v0.2.0，對照表裡沒有：打錯的版號不會看起來像通過。
  real_acceptance_with_manual_pending
  git tag v0.2.0

  run "${SCRIPT}" v0.2.1-rc1

  ! gh_was_called
}

@test "patch 版的 vX.Y.0 不在對照表時（真的 acceptance.sh、替身對照表）以 2 結束" {
  real_acceptance_with_manual_pending
  git tag v0.2.0

  run "${SCRIPT}" v0.2.1-rc1

  [ "${status}" -eq 2 ]
}

# ── 正式 tag 前的核對（#407）：每一則叫人打正式 tag 的下一步都帶這一段 ──────────
#
# rc 不論整體判定、不論 badge 一不一致都建得起 release，所以「rc 的 workflow 成功」或「標題寫
# 通過」都不代表可以打正式 tag。下面每一條是一則會叫人打正式 tag 的下一步。

@test "正式 tag 的報表壞掉時，下一步帶正式 tag 前的核對" {
  stub_broken

  run "${SCRIPT}" v0.1.0

  carries_formal_check v0.1.1
}

@test "正式 tag 的輸出不是一份報表時，下一步帶正式 tag 前的核對" {
  stub_not_a_report 1

  run "${SCRIPT}" v0.1.0

  carries_formal_check v0.1.1
}

@test "正式 tag 的報表未通過時，下一步帶正式 tag 前的核對" {
  stub_red

  run "${SCRIPT}" v0.1.0

  carries_formal_check v0.1.1
}

@test "正式 tag 的 badge 與重新量測不同時，下一步帶正式 tag 前的核對" {
  stub_green
  stub_coverage_differs

  run "${SCRIPT}" v0.1.0

  carries_formal_check v0.1.1
}

@test "正式 tag 的覆蓋率量不到時，下一步帶正式 tag 前的核對" {
  stub_green
  stub_coverage_fails

  run "${SCRIPT}" v0.1.0

  carries_formal_check v0.1.1
}

@test "正式 tag 跳號被擋下時，下一步帶正式 tag 前的核對" {
  given_v090_tagged
  git tag v0.9.2

  run "${SCRIPT}" v0.9.2

  carries_formal_check v0.9.1
}

@test "rc 整體通過、badge 一致時，下一步帶正式 tag 前的核對" {
  stub_green

  run "${SCRIPT}" v0.1.0-rc1

  carries_formal_check v0.1.0
}

# ── rc 建好之後的下一步（#407）──────────────────────────────────────────────

@test "rc 的 --gate overall 未通過時，下一步不叫人打正式 tag" {
  stub_red

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" != *"打正式 tag"* ]]
}

@test "rc 的 --gate overall 未通過時，下一步指向推導出的下一個 rc" {
  stub_red

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" == *"下一步："*"推 script/release.sh --next v0.1.0 印出的下一個 rc（現在是 v0.1.0-rc2）"* ]]
}

@test "rc 整體通過、badge 與重新量測不同時，下一步不叫人打正式 tag" {
  stub_green
  stub_coverage_differs

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" != *"打正式 tag"* ]]
}

@test "rc 整體通過、badge 與重新量測不同時，下一步寫出更新 badge 的做法" {
  stub_green
  stub_coverage_differs

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" == *"下一步："*"./script/coverage_badge.sh v0.1.0 coverage.tsv > doc/badge/coverage.svg 並提交"* ]]
}

@test "rc 整體通過、覆蓋率量不到時，下一步不叫人打正式 tag" {
  stub_green
  stub_coverage_fails

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" != *"打正式 tag"* ]]
}

@test "rc 整體通過、badge 一致時，下一步寫出在同一個 SHA 上打正式 tag" {
  stub_green
  local sha
  sha="$(git rev-parse HEAD)"

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" == *"git tag v0.1.0 ${sha}"* ]]
}

# ── 發壞的 rc 指向下一個 rc（#407）────────────────────────────────────────────

@test "rc 的報表壞掉時，下一步指向 --next 推導出的下一個 rc" {
  stub_broken

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" == *"下一步：這個 tag 留著（tag 不能刪）"*"推 script/release.sh --next v0.1.0 印出的下一個 rc（現在是 v0.1.0-rc2）"* ]]
}

@test "rc 的輸出不是一份報表時，下一步指向 --next 推導出的下一個 rc" {
  stub_not_a_report 1

  run "${SCRIPT}" v0.1.0-rc1

  [[ "${output}" == *"下一步：這個 tag 留著（tag 不能刪）"*"推 script/release.sh --next v0.1.0 印出的下一個 rc（現在是 v0.1.0-rc2）"* ]]
}

# ── tag 不刪、不移（#407 定案 14）─────────────────────────────────────────────

@test "沒有一則下一步叫人刪 tag、收回 tag，或把同一個 tag 再推一次" {
  # tag ruleset 擋刪除與 force push：那樣的下一步照著做會被拒絕。逐一走過每一條會印出下一步的
  # 路徑——格式不對、rc 與 patch 跳號、報表壞掉、輸出不是報表、未通過、badge 不同、量不到、
  # rc 建好之後——把輸出全部收起來看。每一條路徑各自印出什麼，由上面各段的規格看著。
  local all=""
  stub_green
  run "${SCRIPT}" v0.1
  all+="${output}"
  git tag v0.1.0-rc1
  git tag v0.1.0-rc5
  run "${SCRIPT}" v0.1.0-rc5
  all+="${output}"
  run "${SCRIPT}" v0.1.0-rc1
  all+="${output}"
  stub_broken
  run "${SCRIPT}" v0.1.0-rc1
  all+="${output}"
  run "${SCRIPT}" v0.1.0
  all+="${output}"
  stub_not_a_report 1
  run "${SCRIPT}" v0.1.0-rc1
  all+="${output}"
  run "${SCRIPT}" v0.1.0
  all+="${output}"
  stub_red
  run "${SCRIPT}" v0.1.0-rc1
  all+="${output}"
  run "${SCRIPT}" v0.1.0
  all+="${output}"
  stub_green
  stub_coverage_differs
  run "${SCRIPT}" v0.1.0-rc1
  all+="${output}"
  run "${SCRIPT}" v0.1.0
  all+="${output}"
  stub_coverage_fails
  run "${SCRIPT}" v0.1.0-rc1
  all+="${output}"
  run "${SCRIPT}" v0.1.0
  all+="${output}"
  git tag v0.1.0
  git tag v0.1.2
  run "${SCRIPT}" v0.1.2
  all+="${output}"

  [[ "${all}" != *"git push origin :"* && "${all}" != *"收回"* && "${all}" != *"重推"* && "${all}" != *"再打"* ]]
}

# ── 認不得的輸入與缺工具 ──────────────────────────────────────────────────

@test "認不得的 tag 格式非零結束，並說出它認不得" {
  # 只斷言結束碼非零分不出「擋下來了」與「腳本自己爆掉」——把格式檢查拿掉之後，
  # MILESTONE 未定義，set -u 一樣讓它非零結束、一樣不建 release，而這一則原本
  # 仍是綠的（突變檢查 M12 抓到的）。
  stub_green

  run "${SCRIPT}" v0.1

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"認不得的 tag 格式"* ]]
}

@test "認不得的 tag 格式不會建出一個 release" {
  stub_green

  run "${SCRIPT}" release-2026-09

  ! gh_was_called
}

@test "認不得的 tag 格式，下一步寫這個 tag 留著、改推 --next 印出的 tag" {
  # 打錯的 tag 一樣刪不掉（tag ruleset 擋 tag 的刪除，#407 定案 14）。
  stub_green

  run "${SCRIPT}" v0.1

  [[ "${output}" == *"下一步：這個 tag 留著"*"改推 script/release.sh --next <版號> 印出的那個 tag"* ]]
}

@test "gh 不在 PATH 上時大聲失敗，不回報建好了" {
  # 與 test.sh 的「缺工具不得靜默通過」同一條（不變式 2）：一支建不了 release
  # 卻回 0 的腳本，與一次成功的發布長得一模一樣。
  # 拿掉工具只能靠重建 PATH：腳本以 command -v 判定，覆寫不掉。
  stub_green
  local bin="${WORK}/only"
  mkdir -p "${bin}"
  local tool
  for tool in bash dirname git grep date; do
    ln -sf "$(command -v "${tool}")" "${bin}/${tool}"
  done

  run env -i PATH="${bin}" HOME="${WORK}" \
    CM_RELEASE_ACCEPTANCE="${ACCEPTANCE}" \
    "${SCRIPT}" v0.1.0-rc1

  [ "${status}" -ne 0 ]
  # 「輸出裡有 gh 這個字」連 usage 都滿足。要斷言它說的是那支工具不在 PATH 上。
  [[ "${output}" == *"gh 不在 PATH 上"* ]]
}

@test "沒有給 tag 時以用法結束，不猜一個版本發出去" {
  run "${SCRIPT}"

  [ "${status}" -ne 0 ]
}
