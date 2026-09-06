#!/usr/bin/env bash
#
# 單檔檢查。剛編輯過的那一個檔案，立刻跑得動它的那幾項檢查。
#
# **這不是 CI 的替代品。CI 是權威，這支只是把回饋提前。** 它刻意只跑「一個檔案就能
# 判定」的檢查——ruff、lint_messages、shellcheck。mypy 要看整包型別、pylint 要看整包
# 匯入、pytest 要跑整組測試、覆蓋率下限只有在全部跑完之後才有意義：那些留在
# `./script/test.sh` 與 CI。這支說「乾淨」只代表那三項乾淨，不代表這次修改可以合併。
#
# `script/**/*.sh` 除了 shellcheck 之外也走 lint_messages：shell 的執行期輸出從 #133
# 起在三要素的管轄內，而單檔就判定得了它（與 `src/**/*.py` 同一個理由）。
#
# 為什麼要有它：閾值與規則本來就都擋得住（設計 §0.4），但擋下的時機是「推上去之後」。
# 同一個違規，在寫下它的當下看見，跟在十個 commit 之後從 CI 的紅燈回推，成本差很遠。
#
# 兩個呼叫者，一條路徑（比照 script/test.sh）：命令列 `script/check_file.sh <path>`，
# 以及 Claude Code 的 PostToolUse hook——後者不給參數，把 JSON 送進 stdin。
#
# 跟 test.sh 一樣轉進檢查映像，因為主機不是這個專案的證據（ADR-00000027）。差別是
# **不建映像**：hook 要在幾秒內回答，而 Dockerfile 一改，建置就是好幾分鐘。映像不在
# 就說出來並讓路——這支不是閘門，擋不擋得住由 CI 決定。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly TEST_IMAGE="config_manager-test-tools:local"

usage() {
  cat <<'USAGE'
用法：script/check_file.sh [<path>]

  <path>  要檢查的那一個檔案。絕對路徑，或相對於 repo 根目錄。
          不帶參數時，從 stdin 讀 Claude Code 的 PostToolUse hook 酬載，
          取其中的 tool_input.file_path。

  src/**/*.py      ruff check，外加 script/lint_messages.sh
  script/**/*.sh   shellcheck --severity=warning，外加 script/lint_messages.sh
  其餘             沒有可以單檔判定的檢查，結束碼 0

在 docker/Dockerfile.test-tools 裡執行，而且**不建置它**：hook 要在幾秒內回答。
這支不是 ./script/test.sh 或 CI 的替代品——它只是把同一組回饋提前，而且只提前
那些單一檔案就判定得了的檢查。
USAGE
}

# hook 的酬載是 JSON，而 shell 沒有 JSON parser。這裡用主機的 python3 只是為了讀一個
# 欄位——不是拿它當檢查的證據，那件事仍然只在映像裡發生。
hook_path() {
  [[ -t 0 ]] && return 0
  command -v python3 >/dev/null 2>&1 || {
    printf 'check_file: python3 不在 PATH 上，所以 hook 的酬載沒有被讀取。下一步：改用 ./script/test.sh\n' >&2
    return 0
  }
  python3 -c 'import json, sys
try:
    payload = json.load(sys.stdin)
except (ValueError, OSError):
    raise SystemExit(0)
print(payload.get("tool_input", {}).get("file_path", "") or "")
'
}

# repo 根目錄底下的相對路徑，或空字串（檔案不在這個 repo 裡）。掛載進容器的是
# repo 根目錄，所以主機的絕對路徑到了那邊沒有意義，必須先相對化再送進去。
relative_path() {
  local path="$1"
  [[ "${path}" == /* ]] || path="${REPO_ROOT}/${path}"
  case "${path}" in
    "${REPO_ROOT}"/*) printf '%s' "${path#"${REPO_ROOT}"/}" ;;
    *) printf '' ;;
  esac
}

# 每一項都跑完再回報，不是第一項紅了就停。停在第一項會讓「修好再跑、又冒出一條」
# 重複好幾輪，而這支存在的理由正是縮短那個迴圈。
run_checks() {
  local file="$1" failed=0
  cd "${REPO_ROOT}"

  case "${file}" in
    src/*.py)
      ruff check "${file}" || failed=1
      ./script/lint_messages.sh "${file}" || failed=1
      ;;
    script/*.sh)
      shellcheck --severity=warning "${file}" || failed=1
      ./script/lint_messages.sh "${file}" || failed=1
      ;;
    *)
      printf 'check_file: %s has no single-file check; nothing was run.\n' "${file}"
      ;;
  esac
  return "${failed}"
}

dispatch_to_container() {
  if ! command -v docker >/dev/null 2>&1; then
    printf 'check_file: docker 沒有安裝，所以 %s **沒有**被檢查。\n' "$1" >&2
    printf 'check_file: 這條只是提前回饋的路徑，擋不擋得住仍由 ./script/test.sh 與 CI 決定。\n' >&2
    printf 'check_file: 下一步：安裝 docker，或直接跑 ./script/test.sh\n' >&2
    return 0
  fi
  if ! docker image inspect "${TEST_IMAGE}" >/dev/null 2>&1; then
    printf 'check_file: %s 還不存在，所以 %s **沒有**被檢查。\n' "${TEST_IMAGE}" "$1" >&2
    printf 'check_file: 下一步：先跑一次 ./script/test.sh 把它建起來。這裡不建，是因為 hook 要快\n' >&2
    return 0
  fi

  # --user：映像的使用者是 root，而 root 程序寫進 bind mount 會在主機留下 root 所有
  # 的檔案。這裡只讀不寫，但掛載是雙向的，理由與 test.sh 相同，做法也保持相同。
  exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env CM_IN_TEST_IMAGE=1 \
    --volume "${REPO_ROOT}:/repo" \
    --workdir /repo \
    "${TEST_IMAGE}" \
    ./script/check_file.sh "$1"
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  local given="${1:-}"
  [[ -n "${given}" ]] || given="$(hook_path)"
  [[ -n "${given}" ]] || return 0

  local file
  file="$(relative_path "${given}")"
  [[ -n "${file}" ]] || return 0
  [[ -f "${REPO_ROOT}/${file}" ]] || return 0

  # 只有會被檢查的路徑才值得付一次容器啟動的代價。這個判斷要在轉進去之前做，
  # 否則每一次編輯任何檔案都會多等一秒。
  case "${file}" in
    src/*.py | script/*.sh) ;;
    *) return 0 ;;
  esac

  if [[ "${CM_IN_TEST_IMAGE:-}" != "1" && "${CM_TEST_LOCAL:-}" != "1" ]]; then
    dispatch_to_container "${file}"
    return 0
  fi

  run_checks "${file}"
}

main "$@"
