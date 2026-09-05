#!/usr/bin/env bats
#
# script/release.sh — 哪一種 tag 發得出去（T19、#150）。
#
# 被觀察的不是驗收報表的內容——那是 acceptance.bats 的事——而是**這支腳本怎麼處置
# 那份報表的結束碼**：
#
#   rc tag      不論綠紅都建立 release。擋下紅色的 rc 等於讓 N 無法遞增，
#               而 rc 的用途就是記錄「現在走到哪」。
#   報表壞掉    連 rc 都不建。那不是「未通過」，是根本沒有報表。
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
}

teardown() {
  cd / || true
  rm -rf "${WORK}"
}

# 一份全綠的替身報表。
stub_green() {
  cat >"${ACCEPTANCE}" <<'STUB'
#!/usr/bin/env bash
printf 'acceptance: v0.1.0 資料層與唯讀介面 —— 5 條檢查點\n'
printf '  檢查點 1  通過  ADR lint 可攔截\n'
printf 'acceptance: v0.1.0 —— 5 通過／0 未通過／0 未涵蓋\n'
printf 'acceptance: v0.1.0 通過\n'
STUB
  chmod +x "${ACCEPTANCE}"
}

# 一份有一條紅的替身報表。結束碼 1 == 有檢查點未通過或未涵蓋。
stub_red() {
  cat >"${ACCEPTANCE}" <<'STUB'
#!/usr/bin/env bash
printf 'acceptance: v0.1.0 資料層與唯讀介面 —— 5 條檢查點\n'
printf '  檢查點 1  通過  ADR lint 可攔截\n'
printf '  檢查點 3  未通過  修改清單檔後寫出，註解與欄位順序完整保留\n'
printf 'acceptance: v0.1.0 —— 4 通過／1 未通過／0 未涵蓋\n'
printf 'acceptance: v0.1.0 未通過\n'
exit 1
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


# ── 報表進 release notes ──────────────────────────────────────────────────

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

# ── rc 編號由既有 tag 推導 ────────────────────────────────────────────────

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

@test "推導看的是最大的編號，不是既有 rc 的個數" {
  # 中間刪掉一個 tag 之後，用個數會撞號。
  git tag v0.1.0-rc4

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc5" ]
}

@test "別的 milestone 的 rc 不影響這一版的推導" {
  git tag v0.2.0-rc7

  run "${SCRIPT}" --next v0.1.0

  [ "${output}" = "v0.1.0-rc1" ]
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

@test "重跑同一個 rc tag 不會因為它自己已經存在而被擋" {
  # tag 被推上來之後 CI 才跑，所以推導必須把這個 tag 自己排除在外。
  # 少了這一條，每一次 release 都會擋下自己。
  git tag v0.1.0-rc1
  stub_green

  run "${SCRIPT}" v0.1.0-rc1

  [ "${status}" -eq 0 ]
}

# ── 認不得的輸入與缺工具 ──────────────────────────────────────────────────

@test "認不得的 tag 格式非零結束" {
  stub_green

  run "${SCRIPT}" v0.1

  [ "${status}" -ne 0 ]
}

@test "認不得的 tag 格式不會建出一個 release" {
  stub_green

  run "${SCRIPT}" release-2026-09

  ! gh_was_called
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
