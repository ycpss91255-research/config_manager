#!/usr/bin/env bats
#
# script/ci_rollup.sh — CI 唯一的必要狀態檢查背後的判定（#358）。
#
# 分支保護只看 ci-rollup 一個 job，所以它放行的就是被合併的。它先前把每個相依的
# skipped 都當成通過，而覆蓋率那一段在任何一份分片沒過時就是被跳過——那樣的一次執行
# 少判了覆蓋率，卻與全綠分不出來。這份規格釘住的是「每個 job 照它這一次該不該跑判」，
# 以及 workflow、needs、規則三份清單對不起來時不放行。
#
# 測的是公開行為：餵一份 workflow、一份 needs 的 JSON 與觸發事件進去，看結束碼與它
# 指名了哪個 job。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  ROLLUP="${REPO_ROOT}/script/ci_rollup.sh"
  WORK="$(mktemp -d)"
  WORKFLOW="${WORK}/ci.yaml"

  # 與 ci.yaml 同一個形狀，縮小到每種規則各有一個 job：每次都跑的 lint 與 coverage、
  # 只在 pull request 上跑的 checkpoints、只在 push 到 main 時跑的 acceptance。
  {
    printf 'name: CI\n'
    printf 'on:\n'
    printf '  pull_request:\n'
    printf 'jobs:\n'
    printf '  checkpoints:\n'
    printf '    if: github.event_name == '"'"'pull_request'"'"'\n'
    printf '    runs-on: ubuntu-latest\n'
    printf '    steps:\n'
    printf '      - run: true\n'
    printf '  # 註解不是 job\n'
    printf '  lint:\n'
    printf '    runs-on: ubuntu-latest\n'
    printf '    steps:\n'
    printf '      - run: |\n'
    printf '          echo not-a-job:\n'
    printf '  coverage:\n'
    printf '    needs: [lint]\n'
    printf '    runs-on: ubuntu-latest\n'
    printf '    steps:\n'
    printf '      - run: true\n'
    printf '  acceptance:\n'
    printf '    runs-on: ubuntu-latest\n'
    printf '    steps:\n'
    printf '      - run: true\n'
    printf '  ci-rollup:\n'
    printf '    needs: [checkpoints, lint, coverage, acceptance]\n'
    printf '    runs-on: ubuntu-latest\n'
    printf '    steps:\n'
    printf '      - run: true\n'
  } >"${WORKFLOW}"
}

teardown() {
  rm -rf "${WORK}"
}

# needs 的 JSON，參數是成對的 job 與結果。
needs() {
  local json="{" sep=""
  while (( $# > 0 )); do
    json+="${sep}\"$1\": {\"result\": \"$2\", \"outputs\": {}}"
    sep=", "
    shift 2
  done
  printf '%s}' "${json}"
}

# 以 GitHub 會給的那組環境變數呼叫，事件與 ref 由 $1 $2 指定，其後是 needs 的成對參數。
rollup() {
  local event="$1" ref="$2"
  shift 2
  run env -i PATH="${PATH}" GITHUB_JOB=ci-rollup GITHUB_EVENT_NAME="${event}" GITHUB_REF="${ref}" \
    NEEDS="$(needs "$@")" \
    "${ROLLUP}" "${WORKFLOW}" --always "lint coverage" --pull-request "checkpoints" --main-push "acceptance"
}

@test "pull request 上該跑的都過了、只在 main 跑的被跳過時，結束碼為 0" {
  rollup pull_request refs/pull/1/merge checkpoints success lint success coverage success acceptance skipped

  [ "${status}" -eq 0 ]
}

@test "push 到 main 時只在 pull request 跑的被跳過，結束碼為 0" {
  rollup push refs/heads/main checkpoints skipped lint success coverage success acceptance success

  [ "${status}" -eq 0 ]
}

@test "每次都該跑的 job 失敗時不放行，並指名那個 job" {
  rollup pull_request refs/pull/1/merge checkpoints success lint failure coverage success acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：lint"* ]]
}

@test "每次都該跑的 job 被取消時不放行" {
  rollup pull_request refs/pull/1/merge checkpoints success lint success coverage cancelled acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：coverage"* ]]
}

@test "每次都該跑的 job 被跳過時不放行——相依沒過而被跳過的覆蓋率不是通過" {
  rollup pull_request refs/pull/1/merge checkpoints success lint success coverage skipped acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：coverage"* ]]
}

@test "只在 pull request 跑的 job 在 pull request 上被跳過時不放行" {
  rollup pull_request refs/pull/1/merge checkpoints skipped lint success coverage success acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：checkpoints"* ]]
}

@test "只在 main 跑的 job 在 push 到 main 時被跳過就不放行" {
  rollup push refs/heads/main checkpoints skipped lint success coverage success acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：acceptance"* ]]
}

@test "這次本來不跑的 job 若跑了而失敗，仍然不放行" {
  rollup push refs/heads/main checkpoints failure lint success coverage success acceptance success

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：checkpoints"* ]]
}

@test "不認得的結果不是通過" {
  rollup pull_request refs/pull/1/merge checkpoints success lint neutral coverage success acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"有 job 沒有通過：lint"* ]]
}

@test "workflow 裡有、needs 沒列的 job 不放行，並指名那個 job" {
  rollup pull_request refs/pull/1/merge checkpoints success lint success acceptance skipped

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"workflow 有 coverage"* ]]
}

@test "needs 裡有、沒被分進任何一組規則的 job 不放行" {
  run env -i PATH="${PATH}" GITHUB_JOB=ci-rollup GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/1/merge \
    NEEDS="$(needs checkpoints success lint success coverage success acceptance skipped)" \
    "${ROLLUP}" "${WORKFLOW}" --always "lint" --pull-request "checkpoints" --main-push "acceptance"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"needs 有 coverage，但沒有被分進"* ]]
}

@test "規則列了、needs 裡沒有的 job 不放行" {
  run env -i PATH="${PATH}" GITHUB_JOB=ci-rollup GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/1/merge \
    NEEDS="$(needs checkpoints success lint success coverage success acceptance skipped)" \
    "${ROLLUP}" "${WORKFLOW}" --always "lint coverage bats" --pull-request "checkpoints" --main-push "acceptance"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"規則列了 bats"* ]]
}

@test "NEEDS 不是 JSON 時不放行，不當成零個 job 全部通過" {
  run env -i PATH="${PATH}" GITHUB_JOB=ci-rollup GITHUB_EVENT_NAME=pull_request NEEDS="not json" \
    "${ROLLUP}" "${WORKFLOW}" --always "lint coverage" --pull-request "checkpoints" --main-push "acceptance"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"NEEDS 不是一份非空的 JSON 物件"* ]]
}

@test "workflow 裡讀不到任何 job 時不放行" {
  printf 'name: CI\non:\n  pull_request:\n' >"${WORKFLOW}"

  rollup pull_request refs/pull/1/merge lint success

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"讀不到任何 job"* ]]
}
