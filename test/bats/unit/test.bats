#!/usr/bin/env bats
#
# script/test.sh — 「缺工具不得靜默通過」的保證（#72）。
#
# 這是五支守門腳本裡唯一真的付出過代價的一支：本機沒有 hadolint，lint 靜默跳過，
# 每次本機都報乾淨而 CI 連紅六次（ADR-00000027 的第二列、CHANGELOG 2026-09-04）。
# 修法是「缺工具即中止」，但那之後沒有任何規格釘住它——#63 關閉時是靠人工逐條複驗
# 才確認行為成立的，那本身就是「沒有自動化保證」的證據。這份補上那一條。
#
# 測的是公開行為：餵一組受控的 PATH 與環境變數進去，看結束碼與訊息，不碰腳本內部。
#
# **「拿掉一支工具」只能靠重建 PATH。** 腳本以 command -v 判定工具在不在，而 command
# 是 shell 內建，沒有覆寫的餘地。所以每個案例自己組一個只放指定工具的目錄當 PATH，
# 沒被連進去的就是缺席——docker 也在其中：沒有任何案例可以真的去建映像。
#
# **子行程一律以 env -i 啟動。** 腳本讀四個環境變數，而規格自己就跑在檢查映像裡
# （CM_IN_TEST_IMAGE=1 由映像設定），不清乾淨的話「不在映像內」的案例造不出來。

# 連真工具進來，不放假的：哪些檢查工具在場是映像的保證（ADR-00000027），
# 不該由規格自己捏造一個。
link_tool() {
  local src
  src="$(command -v "$1")"
  ln -sf "${src}" "${BIN}/$1"
}

# 以受控環境呼叫守門腳本。VAR=VAL 先給，之後是腳本路徑與它的參數。
gate() {
  run env -i PATH="${BIN}" "$@"
}

# 假 docker：把每次呼叫的參數逐行記下來，然後成功離開。
#
# 被觀察的行為是**容器是怎麼被啟動的**，而規格自己就跑在那個容器裡——它看不見自己
# 的掛載，也起不了第二層容器（映像裡沒有 docker，而為了測試對主機開一個例外，代價
# 比 ADR-00000027 願意付的大）。所以觀察位置是 dispatch_to_container 組出來的 argv。
# 假的只有 docker 一個：git 是真的、worktree 也是真的，因為被測的判斷正是拿真的 git
# 問出來的（#103）。與 lint_checkpoints.sh 用假 gh 觀察「對 gh 發了什麼呼叫」同一個先例。
#
# 假 docker 以 printf 寫出，不用 heredoc：bats 的前處理是逐行的，heredoc 的內容
# 對它與其餘的行沒有分別（同一個理由寫在下面「規格自己跳過」那條裡）。
fake_docker() {
  DOCKER_ARGV="${WORK}/docker-argv"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'printf "%%s\\n" "$@" >>"%s"\n' "${DOCKER_ARGV}"
  } >"${BIN}/docker"
  chmod +x "${BIN}/docker"
}

# 一份真的簽出，帶著一份被測腳本的複本。斷言若掛在本 repo 自己的 .git 上，這些規格
# 就會隨著「這份簽出剛好是不是 worktree」而變——而那正是它們要判的那件事。
make_repo() {
  MAIN="${WORK}/main"
  mkdir -p "${MAIN}/script"
  cp "${SCRIPT}" "${MAIN}/script/test.sh"
  git -C "${MAIN}" init -q
  git -C "${MAIN}" -c user.email=spec@example.com -c user.name=spec add script/test.sh
  git -C "${MAIN}" -c user.email=spec@example.com -c user.name=spec commit -qm "chore: fixture"
}

# 由上面那份簽出長出一個真的 worktree：它的 .git 是一個檔案，內容是 ${MAIN}/.git
# 底下的一條絕對路徑。
make_worktree() {
  make_repo
  WT="${WORK}/wt"
  git -C "${MAIN}" worktree add -q "${WT}"
}

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  SCRIPT="${REPO_ROOT}/script/test.sh"
  # pwd -P：被測的腳本以 pwd -P 算 REPO_ROOT，而 mktemp 在某些平台給的是一條帶符號
  # 連結的路徑。兩邊解得不一樣時，對掛載路徑的斷言會對不上一個其實正確的答案。
  WORK="$(cd -- "$(mktemp -d)" && pwd -P)"
  BIN="${WORK}/bin"
  mkdir -p "${BIN}"

  # 這四支與被測的保證無關，是腳本自己跑起來就要的：shebang 找 bash、REPO_ROOT
  # 的計算用 dirname、列舉 shell 腳本用 find 與 sort。少連一支，紅的會是別的東西。
  link_tool bash
  link_tool dirname
  link_tool find
  link_tool sort
}

teardown() {
  rm -rf "${WORK}"
}

@test "缺一支檢查工具時 lint 中止，並指名該工具與安裝方式" {
  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --lint hadolint

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"hadolint"* ]]
  [[ "${output}" == *"github.com/hadolint/hadolint/releases"* ]]
}

@test "一次列出全部缺席的檢查工具，且只列缺的那些" {
  link_tool ruff
  link_tool mypy
  link_tool pylint
  link_tool shellcheck

  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --lint

  [[ "${output}" == *"hadolint"* ]]
  [[ "${output}" == *"actionlint"* ]]
  [[ "${output}" != *"ruff"* ]]
}

@test "CM_LINT_ALLOW_MISSING=1 讓缺工具的執行以 0 結束" {
  gate CM_IN_TEST_IMAGE=1 CM_LINT_ALLOW_MISSING=1 "${SCRIPT}" --lint hadolint

  [ "${status}" -eq 0 ]
}

@test "降級之後的訊息仍指名哪一項沒跑" {
  gate CM_IN_TEST_IMAGE=1 CM_LINT_ALLOW_MISSING=1 "${SCRIPT}" --lint hadolint

  [[ "${output}" == *"hadolint"* ]]
  [[ "${output}" == *"did NOT run"* ]]
}

@test "降級之後缺席的 shellcheck 不被呼叫，結束碼仍為 0" {
  # 這條與上面那條走的是不同的分支形狀：hadolint 是 `require_tool && 執行`，
  # shellcheck 是 `if require_tool; then 列檔案再執行; fi`。少了守門，缺席的工具
  # 會被真的呼叫下去，結束碼變成 127——那正是「缺工具靜默通過」的反面失敗。
  gate CM_IN_TEST_IMAGE=1 CM_LINT_ALLOW_MISSING=1 "${SCRIPT}" --lint shellcheck

  [ "${status}" -eq 0 ]
  [[ "${output}" != *"command not found"* ]]
}

@test "bats 缺席時 bats 規格不被靜默跳過" {
  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --file "${WORK}/nowhere.bats"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"apt-get install bats"* ]]
}

# 底下兩條保留完整的 PATH，因為它們測的不是「工具在不在」而是「沒跑到的東西有沒有
# 被說出來」。真的把 PATH 拆到只剩四支，被執行的會是 bats 自己起不來，而那條紅燈
# 指的是別的東西。
#
# 環境仍然清乾淨（env -i）：其中一條會讓 bats 跑起另一個 bats，而 BATS_* 那組變數
# 是給**這一次**執行用的。留著它們，內層會拿外層的測試名稱去找一個它沒有的測試。
in_image() {
  # bats 把自己的 libexec 前置到 PATH，而那個目錄裡也有一支叫 bats 的檔案——它是
  # 內層入口，預期呼叫端已經載好了函式庫，直接執行會死在 bats_readlinkf 找不到。
  # 被測的腳本自己會去 PATH 上找 bats，所以那一段要拿掉，否則轉紅的是 bats 的內部
  # 細節，不是 test.sh 的行為。
  local path="${PATH}"
  if [[ -n "${BATS_LIBEXEC:-}" ]]; then
    path="${path//"${BATS_LIBEXEC}:"/}"
  fi
  run env -i PATH="${path}" HOME="${HOME:-/tmp}" CM_IN_TEST_IMAGE=1 "$@"
}

@test "規格自己跳過時被數出來並指名，不混在一片 ok 裡" {
  # 一組永遠在跳過的規格，與「沒有規格」的差別只在輸出多了幾行 skip。這條釘住的是
  # 那幾行不會沉在成功的輸出裡——本 repo 抓過八次「看起來在檢查、其實沒在檢查」。
  #
  # 那一行不能直接寫成 @test：bats 的前處理會改寫**這個檔案裡**每一行看起來像
  # @test 的東西，heredoc 裡的也不例外，於是被寫出去的已經是改寫過的產物。
  {
    printf '%s "只在建好的映像裡才有意義" {\n' '@test'
    printf '  skip "這裡不是被測的映像"\n'
    printf '}\n'
  } >"${WORK}/skips.bats"

  in_image "${SCRIPT}" --file "${WORK}/skips.bats"

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"did NOT run"* ]]
  [[ "${output}" == *"這裡不是被測的映像"* ]]
}

@test "一條規格都沒有的層級被指名，且不以 pytest 的 5 結束" {
  # 自備一個只有腳本與一個空層級的 repo：斷言若掛在真的 test/pytest/acceptance/ 上，
  # 這條規格會在那一層終於有規格的那天轉紅，而那天什麼都沒壞。
  mkdir -p "${WORK}/script" "${WORK}/test/pytest/acceptance"
  cp "${SCRIPT}" "${WORK}/script/test.sh"

  in_image "${WORK}/script/test.sh" --level acceptance

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"test/pytest/acceptance/"* ]]
  [[ "${output}" == *"no specs"* ]]
}

@test "已在檢查映像內時就地執行，不轉進容器" {
  # 以一個不存在的 linter 當探針：它便宜、必定走到分派判斷之後，而且回的碼
  # （2，unknown linter）與 docker 缺席時的碼（1）分得開。
  gate CM_IN_TEST_IMAGE=1 "${SCRIPT}" --lint no-such-linter

  [ "${status}" -eq 2 ]
  [[ "${output}" != *"docker"* ]]
}

@test "CM_TEST_LOCAL=1 時就地執行，不轉進容器" {
  gate CM_TEST_LOCAL=1 "${SCRIPT}" --lint no-such-linter

  [ "${status}" -eq 2 ]
  [[ "${output}" != *"docker"* ]]
}

@test "兩者皆未設定時轉進容器，docker 缺席就指出逃生口" {
  gate "${SCRIPT}" --lint no-such-linter

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"docker is not installed"* ]]
  [[ "${output}" == *"CM_TEST_LOCAL=1"* ]]
}

@test "在 git worktree 裡轉進容器時，主 repo 的 git 目錄以同一個絕對路徑掛進去" {
  link_tool git
  link_tool id
  fake_docker
  make_worktree

  gate "${WT}/script/test.sh" --lint no-such-linter

  # 期望值直接由 fixture 的版面寫出來，不是再問一次 git——用與實作相同的方式重算
  # 答案，等於斷言 git 跟自己一致。worktree 的 .git 檔案裡指的就是這條路徑，而它
  # 要能在容器裡解得開，掛載點就必須是**同一個**絕對路徑，不能是別處。
  grep -qxF -- "${MAIN}/.git:${MAIN}/.git" "${DOCKER_ARGV}"
}

@test "在一般簽出裡轉進容器時，git 目錄不重複掛載" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate "${MAIN}/script/test.sh" --lint no-such-linter

  # 一般簽出的 --git-common-dir 就是 .git，已經在 repo 那個掛載的範圍內。多掛一次
  # 不會壞掉，所以錯誤不會自己現形——只有數掛載的個數才看得見那個判斷有沒有在判斷。
  [ "$(grep -cxF -- '--volume' "${DOCKER_ARGV}")" -eq 1 ]
}
