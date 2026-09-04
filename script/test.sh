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
  exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env GIT_CONFIG_COUNT=1 \
    --env GIT_CONFIG_KEY_0=safe.directory \
    --env GIT_CONFIG_VALUE_0='*' \
    --volume "${REPO_ROOT}:/repo" \
    --workdir /repo \
    "${TEST_IMAGE}" \
    ./script/test.sh "$@"
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
    # lint_messages 讀 Python 的 AST，所以它需要的工具是直譯器本身。
    messages) survey_tools python3 ;;
  esac

  case "${tool}" in
    ruff|all) require_tool ruff && ruff check src test ;;&
    mypy|all) require_tool mypy && mypy --strict src/config_manager/core ;;&
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
      require_tool python3 \
        && ./script/lint_messages.sh
      ;;&
    ruff|mypy|pylint|shellcheck|hadolint|actionlint|commit|adr|paths|portability|messages|all)
      return 0
      ;;
    *) printf 'test.sh: unknown linter %s\n' "${tool}" >&2; return 2 ;;
  esac
}

# 動態層級。smoke 是「類型」不是層級：那些規格在 runtime 映像建置時對映像本身
# 跑，不從這裡跑。
readonly LEVELS="unit integration system acceptance"

# shell 用 bats 測，Python 用 pytest 測；一個層級有哪種規格就跑哪種。bats 不在時
# 交給 survey_tools 大聲回報，不靜默跳過——「沒有東西會執行的規格」正是這段接線
# 要消滅的缺口（不變式 2）。
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
  bats "${specs[@]}"
}

run_bats_levels() {
  local level
  for level in ${LEVELS}; do
    run_bats_level "${level}"
  done
}

main() {
  cd "${REPO_ROOT}"

  case "${1:-}" in -h|--help) usage; return 0 ;; esac

  if [[ "${CM_IN_TEST_IMAGE:-}" != "1" && "${CM_TEST_LOCAL:-}" != "1" ]]; then
    dispatch_to_container "$@"
  fi

  if (( $# == 0 )); then
    run_lint all
    pytest test/pytest --cov=src/config_manager/core --cov-report=term-missing
    run_bats_levels
    return 0
  fi

  case "$1" in
    --lint) shift; run_lint "${1:-all}" ;;
    --level)
      shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      pytest "test/pytest/$1"
      run_bats_level "$1"
      ;;
    --file)
      shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      case "$1" in
        *.bats) survey_tools bats; exec bats "$1" ;;
        *) exec pytest "$1" ;;
      esac
      ;;
    --filter) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest test/pytest -k "$1" ;;
    *) printf 'test.sh: unknown argument %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
}

main "$@"
