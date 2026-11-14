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
  [[ "${output}" == *"上面那幾項沒有執行"* ]]
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
  [[ "${output}" == *"沒有真的跑到"* ]]
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
  [[ "${output}" == *"一條規格都沒有"* ]]
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
  [[ "${output}" == *"docker 沒有安裝"* ]]
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

# ── 映像的層從哪裡來（#358）──────────────────────────────────────────────────
#
# CI 的每個 job 都是新機器，CM_TEST_IMAGE_CACHE 讓它們從 GitHub Actions 的快取取層。
# 被觀察的仍是 dispatch_to_container 組出來的 argv：快取真的有沒有被讀到，要真的
# GitHub Actions 才看得見，那由 CI 日誌裡逐層的建置紀錄（CACHED）回答，不在這裡。
# 這裡只釘住「紀錄有印出來、印在哪裡」。

@test "不設 CM_TEST_IMAGE_CACHE 時照舊以 docker build 建映像，不碰快取" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate "${MAIN}/script/test.sh" --lint no-such-linter

  ! grep -qxF -- 'buildx' "${DOCKER_ARGV}"
}

@test "CM_TEST_IMAGE_CACHE=gha 時從 GitHub Actions 的快取讀層，不寫回" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate CM_TEST_IMAGE_CACHE=gha "${MAIN}/script/test.sh" --lint no-such-linter

  grep -qxF -- 'type=gha,scope=config_manager-test-tools' "${DOCKER_ARGV}"
  ! grep -qxF -- '--cache-to' "${DOCKER_ARGV}"
}

@test "CM_TEST_IMAGE_CACHE=gha-write 時建好的層寫回快取" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate CM_TEST_IMAGE_CACHE=gha-write "${MAIN}/script/test.sh" --lint no-such-linter

  grep -qxF -- 'type=gha,mode=max,scope=config_manager-test-tools' "${DOCKER_ARGV}"
}

@test "用了快取時建好的映像放進 docker 的映像清單，docker run 才找得到它" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate CM_TEST_IMAGE_CACHE=gha "${MAIN}/script/test.sh" --lint no-such-linter

  grep -qxF -- '--load' "${DOCKER_ARGV}"
}

@test "不認得的 CM_TEST_IMAGE_CACHE 以 2 結束並指名它，不建映像" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate CM_TEST_IMAGE_CACHE=s3 "${MAIN}/script/test.sh" --lint no-such-linter

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"CM_TEST_IMAGE_CACHE=s3"* ]]
  [ ! -e "${DOCKER_ARGV}" ]
}

# 建置紀錄要逐層留下來，CI 的日誌才看得出每一層是不是取自快取（#358 定案 3）。
# 以下兩條的假 docker 在建映像那一次多做一件事：往 fd 2 印一行建置紀錄，或以 17
# 結束。17 不是 docker 會用的碼，看到它就知道結束碼是照傳、不是另外寫死的。

@test "建映像時不收起建置紀錄：不用 --quiet，以 --progress=plain 逐層印出" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate "${MAIN}/script/test.sh" --lint no-such-linter

  [ "$(grep -cxF -- '--quiet' "${DOCKER_ARGV}")" -eq 0 ]
  grep -qxF -- '--progress=plain' "${DOCKER_ARGV}"
}

@test "用了快取時同樣逐層印出建置紀錄，看得出每一層是否取自快取" {
  link_tool git
  link_tool id
  fake_docker
  make_repo

  gate CM_TEST_IMAGE_CACHE=gha "${MAIN}/script/test.sh" --lint no-such-linter

  [ "$(grep -cxF -- '--quiet' "${DOCKER_ARGV}")" -eq 0 ]
  grep -qxF -- '--progress=plain' "${DOCKER_ARGV}"
}

@test "建置紀錄印在 stdout，不在 fd 2" {
  link_tool git
  link_tool id
  fake_docker
  printf 'if [[ "$1" == build || "$1" == buildx ]]; then printf "#5 CACHED\\n" >&2; fi\n' >>"${BIN}/docker"
  make_repo

  run env -i PATH="${BIN}" bash -c '"$0" --lint no-such-linter 2>"$1"' "${MAIN}/script/test.sh" "${WORK}/stderr"

  [[ "${output}" == *"#5 CACHED"* ]]
  ! grep -qF -- '#5 CACHED' "${WORK}/stderr"
}

@test "映像建不起來時以 docker 的結束碼結束，在 fd 2 指出並給下一步，不跑檢查" {
  link_tool git
  link_tool id
  fake_docker
  printf 'if [[ "$1" == build || "$1" == buildx ]]; then exit 17; fi\n' >>"${BIN}/docker"
  make_repo

  run env -i PATH="${BIN}" bash -c '"$0" --lint no-such-linter 2>"$1"' "${MAIN}/script/test.sh" "${WORK}/stderr"

  [ "${status}" -eq 17 ]
  grep -qF -- '建不起來' "${WORK}/stderr"
  grep -qF -- '下一步：' "${WORK}/stderr"
  ! grep -qxF -- 'run' "${DOCKER_ARGV}"
}

# ── 分段執行（#358）─────────────────────────────────────────────────────────
#
# 被觀察的是「每一段交給 pytest 的是哪幾條」與「gate 放不放行」。pytest 與 coverage
# 換成 PATH 上的假工具：被測的是 test.sh 怎麼切、怎麼合，不是 pytest 怎麼跑——真的
# 切法對真的規格樹對不對得起來，由 CI 上 gate 的那一次回答。
#
# 假 pytest 的收集結果是一份固定的清單：兩條一般的規格、三條 test_api.py 的規格、
# 五條 test_web.py 的規格，交錯排著。收集的參數指到一支規格檔時只印那支的，與真的
# pytest 相同。

COLLECTED_UNIT_1="test/pytest/unit/test_a.py::test_one"
COLLECTED_UNIT_2="test/pytest/unit/test_a.py::test_two"

make_parts_repo() {
  PARTS_REPO="${WORK}/repo"
  mkdir -p "${PARTS_REPO}/script"
  cp "${SCRIPT}" "${PARTS_REPO}/script/test.sh"
  # gate 最後交給 coverage_gate.sh；這裡被觀察的是它有沒有被叫到，不是它怎麼判
  # ——那由 coverage_gate.bats 擋著。
  {
    printf '#!/usr/bin/env bash\n'
    printf 'echo coverage_gate-was-called\n'
  } >"${PARTS_REPO}/script/coverage_gate.sh"
  chmod +x "${PARTS_REPO}/script/coverage_gate.sh"

  {
    printf '%s\n' "${COLLECTED_UNIT_1}"
    printf 'test/pytest/system/test_api.py::test_a%s\n' 1
    printf 'test/pytest/system/test_web.py::test_w%s\n' 1 2
    printf '%s\n' "${COLLECTED_UNIT_2}"
    printf 'test/pytest/system/test_api.py::test_a%s\n' 2 3
    printf 'test/pytest/system/test_web.py::test_w%s\n' 3 4 5
  } >"${WORK}/collected"

  # 收集時印固定清單；執行時把 @檔案 的內容記下來，並像 pytest-cov 一樣留下 .coverage。
  {
    printf '#!/usr/bin/env bash\n'
    printf 'if [[ " $* " == *" --collect-only "* ]]; then\n'
    printf '  spec="${!#}"\n'
    printf '  if [[ "${spec}" == *.py ]]; then\n'
    printf '    grep -F "${spec}::" "%s"\n' "${WORK}/collected"
    printf '  else\n'
    printf '    cat "%s"\n' "${WORK}/collected"
    printf '  fi\n'
    printf '  printf "\\n10 tests collected\\n"\n'
    printf '  exit 0\n'
    printf 'fi\n'
    printf 'for arg in "$@"; do case "${arg}" in @*) cp "${arg#@}" "%s" ;; esac; done\n' "${WORK}/pytest-ran"
    printf ': >.coverage\n'
  } >"${BIN}/pytest"
  chmod +x "${BIN}/pytest"

  COVERAGE_ARGV="${WORK}/coverage-argv"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'printf "%%s\\n" "$@" >>"%s"\n' "${COVERAGE_ARGV}"
  } >"${BIN}/coverage"
  chmod +x "${BIN}/coverage"
}

# 假工具放在 PATH 最前面，其餘照舊（python3、awk 這些是腳本自己跑起來就要的）。
in_parts_repo() {
  local path="${PATH}"
  if [[ -n "${BATS_LIBEXEC:-}" ]]; then
    path="${path//"${BATS_LIBEXEC}:"/}"
  fi
  run env -i PATH="${BIN}:${path}" HOME="${HOME:-/tmp}" CM_IN_TEST_IMAGE=1 "${PARTS_REPO}/script/test.sh" "$@"
}

@test "web <i>/<n> 交給 pytest 的是 test_web.py 收集順序裡第 i、i+n…條" {
  make_parts_repo

  in_parts_repo --part web 2/3

  [ "$(cat "${WORK}/pytest-ran")" = "$(printf 'test/pytest/system/test_web.py::test_w%s\n' 2 5)" ]
}

@test "api <i>/<n> 交給 pytest 的是 test_api.py 收集順序裡第 i、i+n…條" {
  make_parts_repo

  in_parts_repo --part api 1/2

  [ "$(cat "${WORK}/pytest-ran")" = "$(printf 'test/pytest/system/test_api.py::test_a%s\n' 1 3)" ]
}

@test "fast 交給 pytest 的是 test_api.py 與 test_web.py 以外的全部規格" {
  make_parts_repo

  in_parts_repo --part fast

  [ "$(cat "${WORK}/pytest-ran")" = "$(printf '%s\n' "${COLLECTED_UNIT_1}" "${COLLECTED_UNIT_2}")" ]
}

@test "一段記下的清單就是交給 pytest 的那一份" {
  make_parts_repo

  in_parts_repo --part web 1/3

  cmp -s "${WORK}/pytest-ran" "${PARTS_REPO}/ci-parts/web-1-of-3/ids.txt"
}

@test "分到零條規格的那一份以 2 結束，不交給 pytest——空的參數會讓 pytest 跑全部" {
  make_parts_repo

  in_parts_repo --part web 6/6

  [ "${status}" -eq 2 ]
  [ ! -e "${WORK}/pytest-ran" ]
}

@test "web 的第 i 份超出總份數時以 2 結束" {
  in_image "${SCRIPT}" --part web 7/6

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"超出了總共 6 份"* ]]
}

@test "不認得的段名以 2 結束" {
  in_image "${SCRIPT}" --part everything

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"不認得的段名 everything"* ]]
}

# 一段已經跑完的結果：ids.txt 與 python.coverage。
write_part() {
  local name="$1"
  shift
  mkdir -p "${PARTS_REPO}/ci-parts/${name}"
  printf '%s\n' "$@" >"${PARTS_REPO}/ci-parts/${name}/ids.txt"
  : >"${PARTS_REPO}/ci-parts/${name}/python.coverage"
}

# 一段的 web 報告：index.html 共 10 行程式碼，$2 是沒被執行到的行（JSON 陣列）。
write_web_report() {
  local name="$1" uncovered="$2" count
  count="$(python3 -c 'import json, sys; print(len(json.loads(sys.argv[1])))' "${uncovered}")"
  printf '{"area": "web", "file": "src/config_manager/web/index.html", "covered_lines": %s, "code_lines": 10, "percent": 0, "uncovered_lines": %s}\n' \
    "$(( 10 - count ))" "${uncovered}" >"${PARTS_REPO}/ci-parts/${name}/web.json"
}

# fast 一段、api 一份、web 兩份，合起來恰好是那十條。
write_complete_parts() {
  write_part fast "${COLLECTED_UNIT_1}" "${COLLECTED_UNIT_2}"
  write_part api-1-of-1 "test/pytest/system/test_api.py::test_a1" \
    "test/pytest/system/test_api.py::test_a2" "test/pytest/system/test_api.py::test_a3"
  write_part web-1-of-2 "test/pytest/system/test_web.py::test_w1" \
    "test/pytest/system/test_web.py::test_w3" "test/pytest/system/test_web.py::test_w5"
  write_web_report web-1-of-2 '[2, 3, 5]'
  write_part web-2-of-2 "test/pytest/system/test_web.py::test_w2" "test/pytest/system/test_web.py::test_w4"
  write_web_report web-2-of-2 '[3, 5, 7]'
}

@test "gate 合併 web 覆蓋率時，沒被執行到的行取各段的交集" {
  make_parts_repo
  write_complete_parts

  in_parts_repo --part gate

  # 一份沒執行到 2、3、5，另一份沒執行到 3、5、7：兩份都沒執行到的只有 3 與 5。
  [ "$(python3 -c 'import json, sys; r = json.load(open(sys.argv[1])); print(r["covered_lines"], r["uncovered_lines"])' \
    "${PARTS_REPO}/.coverage-web.json")" = "8 [3, 5]" ]
}

@test "gate 把每一段的 Python 覆蓋率資料都交給 coverage combine" {
  make_parts_repo
  write_complete_parts

  in_parts_repo --part gate

  for part in fast api-1-of-1 web-1-of-2 web-2-of-2; do
    grep -qxF -- "${PARTS_REPO}/ci-parts/${part}/python.coverage" "${COVERAGE_ARGV}"
  done
}

@test "各段齊全時 gate 交給 coverage_gate.sh 判" {
  make_parts_repo
  write_complete_parts

  in_parts_repo --part gate

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"coverage_gate-was-called"* ]]
}

@test "一條規格沒有被任何一段跑到時 gate 不判覆蓋率，並指名那條規格" {
  make_parts_repo
  write_complete_parts
  write_part fast "${COLLECTED_UNIT_1}"

  in_parts_repo --part gate

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"沒有被任何一段跑到：${COLLECTED_UNIT_2}"* ]]
  [[ "${output}" != *"coverage_gate-was-called"* ]]
}

@test "一條規格被兩段跑到時 gate 不放行" {
  make_parts_repo
  write_complete_parts
  write_part fast "${COLLECTED_UNIT_1}" "${COLLECTED_UNIT_2}" "test/pytest/system/test_web.py::test_w2"

  in_parts_repo --part gate

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"被不只一段跑到：test/pytest/system/test_web.py::test_w2"* ]]
}

@test "少了一份分片時 gate 不放行，並指名少了第幾份" {
  make_parts_repo
  write_complete_parts
  rm -rf "${PARTS_REPO}/ci-parts/web-2-of-2"

  in_parts_repo --part gate

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"web 分成 2 份，少了第 2 份"* ]]
}

@test "api 那一組整組沒跑時 gate 不放行，並指名那一組" {
  make_parts_repo
  write_complete_parts
  rm -rf "${PARTS_REPO}/ci-parts/api-1-of-1"

  in_parts_repo --part gate

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"一段 api 都沒有"* ]]
}

@test "一份分片沒有 web 報告時 gate 不放行" {
  make_parts_repo
  write_complete_parts
  rm -f "${PARTS_REPO}/ci-parts/web-1-of-2/web.json"

  in_parts_repo --part gate

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"ci-parts/web-1-of-2/web.json 不存在"* ]]
}
