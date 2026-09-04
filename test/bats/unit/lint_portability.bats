#!/usr/bin/env bats
#
# script/lint_portability.sh — 擋下只有 GNU 工具才吃的指令選項。
#
# repo 的 shell 腳本要在兩個地方跑：CI 與檢查映像是 Linux（GNU coreutils），
# 貢獻者的機器可能是 macOS（BSD）。用了 GNU 專屬選項的腳本在 CI 永遠是綠的，
# 到了 macOS 才爆——而且不一定爆得明顯：#80 那次 grep -P 讓中文檢查整個失效，
# 對正確的主旨回報「沒有中文」，是給了錯的答案而不是報錯。
#
# 清單來自實測，不是憑印象：在 macOS 上逐一執行後，確認會失敗的才列入。
# readlink -f、sort -V、xargs -r 實測可用，刻意不列。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_portability.sh"
  DIR="$(mktemp -d)"
}

teardown() {
  rm -rf "${DIR}"
}

write_script() {
  local name="$1"
  shift
  {
    printf '#!/usr/bin/env bash\n'
    printf '%s\n' "$@"
  } >"${DIR}/${name}"
}

@test "沒有用到 GNU 專屬選項的腳本通過" {
  write_script clean.sh 'grep -q pattern file' 'sed -e "s/a/b/" file' 'head -n 5 file'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "grep -P 被擋下，並指名檔案與該選項" {
  write_script bad.sh "printf 'a' | grep -qP '[0-9]'"

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"bad.sh"* ]]
  [[ "${output}" == *"grep -P"* ]]
}

@test "GNU 形式的 sed -i 被擋下" {
  write_script bad.sh "sed -i 's/a/b/' file"

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"sed -i"* ]]
}

@test "BSD 也吃的 sed -i '' 不被擋" {
  write_script ok.sh "sed -i '' 's/a/b/' file"

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "只出現在註解裡不算違規" {
  write_script commented.sh '# grep -P is GNU-only, which is why we avoid it' '  # sed -i also differs on BSD' 'grep -q x file'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "其餘七個實測會失敗的選項都被擋下" {
  write_script a.sh 'stat -c %s file'
  write_script b.sh 'date -d 2020-01-01'
  write_script c.sh "find . -printf '%p\\n'"
  write_script d.sh 'base64 -w0 file'
  write_script e.sh 'head -n -1 file'
  write_script f.sh 'cp --parents a b'

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"stat -c"* ]]
  [[ "${output}" == *"date -d"* ]]
  [[ "${output}" == *"find -printf"* ]]
  [[ "${output}" == *"base64 -w"* ]]
  [[ "${output}" == *"head -n -"* ]]
  [[ "${output}" == *"cp --parents"* ]]
}

@test "回報每一個違規，不是只報第一個" {
  write_script one.sh "grep -P x file"
  write_script two.sh "stat -c %s file"

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"one.sh"* ]]
  [[ "${output}" == *"two.sh"* ]]
}
