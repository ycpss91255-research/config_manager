#!/usr/bin/env bash
#
# 三個測試軸（§3.6.1）分開放，因為它們回答的是不同的問題；把它們揉成一張「四種
# 分類」的清單，正是這個結構要避免的錯誤：
#
#   lint   靜態分析——根本不是動態測試層級
#   level  unit / integration / system / acceptance——範圍
#   type   smoke / e2e / regression——目的，套用在某個層級上
#
# 預設跑 lint 加上全部層級與覆蓋率。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly TEST_IMAGE="config_manager-test-tools:local"
readonly TEST_DOCKERFILE="docker/Dockerfile.test-tools"

usage() {
  cat <<'USAGE'
Usage: script/test.sh [--level <name>] [--lint [<tool>]] [--file <path>] [--filter <regex>]

  (no arguments)      lint + all levels + coverage
  --level <name>      unit | integration | system | acceptance
                      （同時跑該層級的 pytest 與 bats 規格）
  --lint [<tool>]     all linters, or one of:
                      ruff | mypy | pylint | shellcheck | hadolint | actionlint | commit | adr | paths
                      | portability | messages
  --file <path>       a single spec file
  --filter <regex>    specs matching a pattern

Runs inside docker/Dockerfile.test-tools, which carries every checker.
The host is not evidence about the project: its Python, its pytest and its
absent linters have each produced a wrong answer here before.

Whatever did not actually run is listed at the end: specs that skipped
themselves, and levels that hold no specs at all. Neither fails the run --
they are not broken -- but neither is allowed to be quiet either.

  CM_TEST_LOCAL=1     run on this host instead. Whatever is missing is
                      named and skipped -- a loud skip, still not a check.
USAGE
}

# 把這支腳本轉進裝了工具的映像裡重跑，除非已經在裡面了。一個環境、兩個呼叫者：
# `just test` 與 CI 走同一條路徑，所以一項檢查不可能在一邊過、在另一邊掛。
dispatch_to_container() {
  if ! command -v docker >/dev/null 2>&1; then
    printf 'test.sh: docker is not installed, so the checks cannot run in their image.\n' >&2
    printf 'test.sh: install docker, or set CM_TEST_LOCAL=1 to run on this host with whatever it has.\n' >&2
    exit 1
  fi

  # 有快取時很便宜；只有 Dockerfile 或釘住的 requirements 真的變了，層才會重建。
  printf 'test.sh: building %s\n' "${TEST_IMAGE}" >&2
  local -a _build=(docker build --quiet -f "${REPO_ROOT}/${TEST_DOCKERFILE}" -t "${TEST_IMAGE}")
  # 映像預設用台灣的 Debian 鏡像，因為從開發這個 repo 的網路連不到
  # deb.debian.org。在別的地方答案不同的話，設 CM_APT_MIRROR，不要改檔案。
  [[ -n "${CM_APT_MIRROR:-}" ]] && _build+=(--build-arg "APT_MIRROR=${CM_APT_MIRROR}")
  "${_build[@]}" "${REPO_ROOT}" >/dev/null

  # repo 用掛載而不是複製進去，這樣檢查失敗時指出的路徑在主機上真的存在，而且改
  # 一行不必重建映像。掛載是雙向的，所以 --user 才重要：映像自己的使用者是 root，
  # 而 root 程序寫進 bind mount 會在主機上留下一堆 root 所有的檔案。這在這裡不是
  # 假設——它賠掉了一次還沒提交的修改，並留下 47 個 root 所有的路徑，其中包含
  # .git 裡面的一個檔案。
  #
  # HOME 被導向他處，是因為呼叫端的 uid 在這個映像裡沒有家目錄；git.safe.directory
  # 走 GIT_CONFIG_* 而不是全域設定，也是同一個原因：沒有可寫的 HOME，就沒有地方
  # 放那份設定。
  local -a _run=(
    docker run --rm
    --user "$(id -u):$(id -g)"
    --env HOME=/tmp
    --env GIT_CONFIG_COUNT=1
    --env GIT_CONFIG_KEY_0=safe.directory
    --env GIT_CONFIG_VALUE_0='*'
    --volume "${REPO_ROOT}:/repo"
  )

  # git worktree 裡，REPO_ROOT/.git 是一個**檔案**，內容是主 repo 那個 git 目錄的
  # 主機絕對路徑。只掛 REPO_ROOT 的話那條路徑在容器裡不存在，git 就不認得這是一份
  # 簽出，於是 lint_commit 與 lint_paths 雙雙以「這裡不是 git repo」失敗——兩支需要
  # git 的檢查沒跑到，而有跳過的執行不算通過（#103）。
  #
  # 掛載點必須是**同一個絕對路徑**：容器要解開的就是那個檔案裡寫死的那條路徑，
  # 掛在別處解不開。讀寫而非唯讀，因為 git 會寫 index 與 lock；--user 已經是呼叫端
  # 的 uid，所以寫進去的東西在主機上的所有權是對的。
  #
  # 一般簽出的 --git-common-dir 就是 REPO_ROOT/.git，已經在上面那個掛載的範圍內，
  # 再掛一次是多餘的——所以只有它落在 REPO_ROOT 之外時才加。不是 git repo、或主機
  # 上沒有 git 時，這裡什麼都不加：那與修這件事之前的行為相同。
  local _git_common=""
  _git_common="$(git -C "${REPO_ROOT}" rev-parse --git-common-dir 2>/dev/null)" || _git_common=""
  [[ -n "${_git_common}" && "${_git_common}" != /* ]] && _git_common="${REPO_ROOT}/${_git_common}"
  case "${_git_common}" in
    ""|"${REPO_ROOT}"/*) ;;
    *) _run+=(--volume "${_git_common}:${_git_common}") ;;
  esac

  _run+=(--workdir /repo "${TEST_IMAGE}" ./script/test.sh "$@")
  exec "${_run[@]}"
}

# 每個 linter 需要 PATH 上有什麼，以及怎麼取得。只有一張表，這樣底下的事前盤點與
# 每項檢查的守衛，就不可能對「這次執行需要哪些工具」有不同意見。
_tool_install_hint() {
  case "$1" in
    ruff|mypy|pylint|pytest) printf 'pip install -r config/pip/requirements-dev.txt' ;;
    python3) printf 'apt-get install python3' ;;
    shellcheck) printf 'apt-get install shellcheck' ;;
    bats) printf 'apt-get install bats' ;;
    hadolint) printf 'https://github.com/hadolint/hadolint/releases' ;;
    actionlint) printf 'https://github.com/rhysd/actionlint/releases' ;;
    *) printf 'see docker/Dockerfile.test-tools' ;;
  esac
}

# 把這次執行缺的**每一個**工具都回報出來，然後停下——不是只報第一個。碰到第一個
# 就中止，會把「盤點主機」變成裝一個、重跑、再撞下一個；而且那樣的執行從來不會說出
# 它沒有檢查什麼，而那正是整個守衛要讓人看見的東西。
#
# 只有 CM_TEST_LOCAL=1 才走得到這裡：正常執行是在映像裡，那些工具依建置方式必然齊全。
survey_tools() {
  local -a needed=("$@") missing=()
  local t
  for t in "${needed[@]}"; do
    command -v "${t}" >/dev/null 2>&1 || missing+=("${t}")
  done
  (( ${#missing[@]} == 0 )) && return 0

  printf 'test.sh: this host is missing %d of the %d checkers this run needs:\n' \
    "${#missing[@]}" "${#needed[@]}" >&2
  for t in "${missing[@]}"; do
    printf '  %-11s %s\n' "${t}" "$(_tool_install_hint "${t}")" >&2
  done
  if [[ "${CM_LINT_ALLOW_MISSING:-}" == "1" ]]; then
    printf 'test.sh: CM_LINT_ALLOW_MISSING=1 -- continuing, and the above did NOT run.\n' >&2
    printf 'test.sh: a run with skips is not a passing run. Prefer dropping CM_TEST_LOCAL.\n' >&2
    return 0
  fi
  printf 'test.sh: install them, drop CM_TEST_LOCAL to use the image that has them,\n' >&2
  printf 'test.sh: or set CM_LINT_ALLOW_MISSING=1 to run the rest and be told what was skipped.\n' >&2
  exit 1
}

# 工具不在的 linter 絕不可以安靜地通過。「lint 過了」必須等於「lint 跑了」；其餘
# 都是不變式 2 禁止的靜默成功，而且已經讓我們付過代價——hadolint 在本機每次執行都被
# 跳過，CI 卻一直在它上面失敗，於是一個 Dockerfile 的問題連續六次推送都沒人注意到。
# 走到這裡時 survey_tools 已經回報並決定過了；這裡是每項檢查的閘門，擋住對一個不存在
# 的工具發出呼叫。
require_tool() {
  command -v "$1" >/dev/null 2>&1
}

run_lint() {
  local tool="${1:-all}"
  cd "${REPO_ROOT}"

  case "${tool}" in
    all) survey_tools ruff mypy pylint shellcheck hadolint actionlint python3 ;;
    ruff|mypy|pylint|shellcheck|hadolint|actionlint) survey_tools "${tool}" ;;
    # lint_messages 讀 Python 的 AST，所以它需要的工具是直譯器本身。shell 那一半
    # 另外需要 bash（用 `bash -n` 確認檔案真的解析得了），而這支腳本自己就是 bash。
    messages) survey_tools python3 ;;
  esac

  case "${tool}" in
    ruff|all) require_tool ruff && ruff check src test ;;&
    mypy|all) require_tool mypy && mypy --strict src/config_manager ;;&
    pylint|all) require_tool pylint && pylint src ;;&
    shellcheck|all)
      if require_tool shellcheck; then
        local -a _sh=()
        mapfile -t _sh < <(find script -name '*.sh' -type f | sort)
        shellcheck --severity=warning "${_sh[@]}"
      fi
      ;;&
    hadolint|all)
      require_tool hadolint \
        && hadolint --config .hadolint.yaml Dockerfile
      ;;&
    actionlint|all)
      # workflow 的運算式不是 YAML，沒有任何 YAML parser 會檢查它們。${{ }} 裡面
      # 一個雙引號字串字面值是合法的 YAML、卻是不合法的運算式——GitHub 會拒收整個
      # 檔案，一個 job 都不跑，並回報成一次沒有 job 可以點開的執行失敗。現在在這裡
      # 抓，而不是推上去再讀後果。
      require_tool actionlint \
        && actionlint .github/workflows/*.yaml
      ;;&
    commit|all) ./script/lint_commit.sh ;;&
    adr|all) ./script/lint_adr.sh ;;&
    paths|all) ./script/lint_paths.sh ;;&
    portability|all) ./script/lint_portability.sh ;;&
    messages|all)
      # 不帶參數：標的是 lint_messages.sh 自己的預設，也就是 src/config_manager
      # **加上 script/**（#133）。先前只有前者，於是 32 支腳本的執行期輸出完全不在
      # 三要素的管轄內，而 review 只能逐則人工判讀——§0.4 說那等同不存在。
      require_tool python3 \
        && ./script/lint_messages.sh
      ;;&
    ruff|mypy|pylint|shellcheck|hadolint|actionlint|commit|adr|paths|portability|messages|all)
      return 0
      ;;
    *) printf 'test.sh: unknown linter %s\n' "${tool}" >&2; return 2 ;;
  esac
}

# 動態層級。四個，與設計 §3.6.1 的軸 2 逐字相同。smoke 不在其中：它是軸 3 的
# **型別**，寫在檔名裡，而它的執行者是 `docker build --target runtime-test`。
readonly LEVELS="unit integration system acceptance"

# 本次執行**沒有真的跑到**的東西，累積在這裡，結束前一次列出。
#
# 兩種來源，同一個形狀：一組每次都跳過的規格，以及一個一條規格都沒有的層級。
# 兩者在輸出上與「全綠」的差別都只有幾行字，而這個 repo 已經抓到八次
# 「看起來在檢查、其實沒在檢查」——每一次都是這個形狀。它們不算失敗（那些規格
# 在這個容器裡確實跑不了），但絕不可以安靜。降級要大聲：與 CM_LINT_ALLOW_MISSING
# 同一個先例。
_NOT_RUN=""

_note_not_run() {
  [[ -n "${_NOT_RUN}" ]] || _NOT_RUN="$(mktemp)"
  printf '%s\n' "$1" >>"${_NOT_RUN}"
}

report_not_run() {
  if [[ -z "${_NOT_RUN}" ]]; then
    return 0
  fi

  local -a lines=()
  mapfile -t lines <"${_NOT_RUN}"
  rm -f "${_NOT_RUN}"
  _NOT_RUN=""

  if (( ${#lines[@]} == 0 )); then
    return 0
  fi

  printf '\ntest.sh: %d spec(s)/level(s) did NOT run in this invocation:\n' "${#lines[@]}" >&2
  printf '  %s\n' "${lines[@]}" >&2
  printf 'test.sh: a spec that always skips is not a check, and a level with no\n' >&2
  printf 'test.sh: specs is not a covered level. Each line above needs a reason\n' >&2
  printf 'test.sh: written down in doc/TEST-PLAN.md, or it is a gap.\n' >&2
}

# shell 用 bats 測，Python 用 pytest 測；一個層級有哪種規格就跑哪種。bats 不在時
# 交給 survey_tools 大聲回報，不靜默跳過——「沒有東西會執行的規格」正是這段接線
# 要消滅的缺口（不變式 2）。
#
# 輸出格式定死成 TAP，不隨「stdout 是不是 terminal」而變。bats 兩種格式標記 skip
# 的寫法不同，而被解析的那份輸出若在兩個環境裡長得不一樣，這裡數出來的數字就會
# 在其中一個環境裡是錯的——那比不數還糟。
run_bats() {
  local label="$1"; shift
  local tap status=0
  tap="$(mktemp)"

  bats --formatter tap "$@" | tee "${tap}" || status=$?

  local line
  while IFS= read -r line; do
    _note_not_run "${label}: ${line}"
  done < <(sed -n 's/^ok [0-9][0-9]* \(.*\) # skip[ ]*\(.*\)$/\1 -- skipped: \2/p' "${tap}")

  rm -f "${tap}"
  return "${status}"
}

run_bats_level() {
  # 兩行不是風格：local 的參數會在它執行前就全部展開，寫成一行的話 ${level}
  # 在展開當下還沒被賦值，set -u 會直接判定 unbound。
  local level="$1"
  local dir="${REPO_ROOT}/test/bats/${level}"
  [[ -d "${dir}" ]] || return 0

  local -a specs=()
  mapfile -t specs < <(find "${dir}" -name '*.bats' -type f | sort)
  (( ${#specs[@]} == 0 )) && return 0

  survey_tools bats
  require_tool bats || return 0
  run_bats "test/bats/${level}" "${specs[@]}"
}

run_bats_levels() {
  local level
  for level in ${LEVELS}; do
    run_bats_level "${level}"
  done
}

# 一個層級的 pytest 規格。空層級交給 _note_not_run，不交給 pytest：pytest 對
# 「一條都沒收集到」回 5，而 5 在這裡是個假的紅燈——那一層是空的，不是壞的，
# 而假紅燈與真紅燈混在一起的下場，是兩種都不再被相信。
run_pytest_level() {
  local level="$1"
  local dir="${REPO_ROOT}/test/pytest/${level}"

  local -a specs=()
  if [[ -d "${dir}" ]]; then
    mapfile -t specs < <(find "${dir}" -name 'test_*.py' -type f | sort)
  fi
  if (( ${#specs[@]} == 0 )); then
    _note_not_run "test/pytest/${level}/ -- the level has no specs at all"
    return 0
  fi

  pytest "${dir}"
}

# 預設執行把 test/pytest 一次收集完（覆蓋率要一個總數，分四次跑就得不到），
# 於是空層級在那一次裡完全看不見。這裡把它們單獨點出來。
note_levels_without_specs() {
  local level dir
  local -a specs
  for level in ${LEVELS}; do
    dir="${REPO_ROOT}/test/pytest/${level}"
    specs=()
    if [[ -d "${dir}" ]]; then
      mapfile -t specs < <(find "${dir}" -name 'test_*.py' -type f | sort)
    fi
    if (( ${#specs[@]} == 0 )); then
      _note_not_run "test/pytest/${level}/ -- the level has no specs at all"
    fi
  done
}

main() {
  cd "${REPO_ROOT}"

  case "${1:-}" in -h|--help) usage; return 0 ;; esac

  if [[ "${CM_IN_TEST_IMAGE:-}" != "1" && "${CM_TEST_LOCAL:-}" != "1" ]]; then
    dispatch_to_container "$@"
  fi

  # 掛在 EXIT 上而不是每條路徑的結尾：一次失敗的執行裡「什麼沒跑到」與一次成功的
  # 執行裡一樣重要，而 set -e 會讓收尾那一行永遠到不了。
  trap report_not_run EXIT

  if (( $# == 0 )); then
    run_lint all
    note_levels_without_specs
    # --cov 涵蓋整個套件；把它拆成四個資料夾各自的門檻，是 coverage_gate 的事。
    # 一次測試、一份資料、四個獨立的判定（#97）。
    pytest test/pytest --cov=src/config_manager --cov-report=term-missing
    ./script/coverage_gate.sh
    run_bats_levels
    return 0
  fi

  case "$1" in
    --lint) shift; run_lint "${1:-all}" ;;
    --level)
      shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      run_pytest_level "$1"
      run_bats_level "$1"
      ;;
    --file)
      shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      case "$1" in
        # exec 會把 EXIT trap 一起換掉，那樣單檔執行就是唯一數不出 skip 的入口
        # ——而「跑單一個檔案」正好是最容易碰到一個整檔跳過的規格的入口。
        *.bats) survey_tools bats; run_bats "$1" "$1" ;;
        *) exec pytest "$1" ;;
      esac
      ;;
    --filter) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest test/pytest -k "$1" ;;
    *) printf 'test.sh: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
}

main "$@"
