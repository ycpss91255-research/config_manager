#!/usr/bin/env bats
#
# script/coverage_gate.sh — 四個資料夾各自的覆蓋率門檻（T19、#97）。
#
# 一個閘門自己沒有規格，就只是一個看起來在守的設定。這個 repo 已經抓到七次
# 「看起來在檢查、其實沒在檢查」，而覆蓋率門檻是其中最容易長成那樣的一種：它平常
# 永遠是綠的，所以「它其實不會紅」這件事可以藏很多年。
#
# 測的是公開行為：餵一組數字給它，看結束碼與它指名了哪一層。真的覆蓋率不參與——
# 那會讓這些規格的結果取決於別的規格有沒有跑。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  GATE="${REPO_ROOT}/script/coverage_gate.sh"
  WORK="$(mktemp -d)"
}

teardown() {
  rm -rf "${WORK}"
}

# 一份 coverage json，三個 Python 資料夾各自指定「執行過幾行／共幾行」。
write_python_report() {
  local core_covered="$1" core_total="$2"
  local io_covered="$3" io_total="$4"
  local api_covered="$5" api_total="$6"
  cat >"${WORK}/coverage.json" <<JSON
{
  "files": {
    "src/config_manager/core/state.py":  {"summary": {"covered_lines": ${core_covered}, "num_statements": ${core_total}}},
    "src/config_manager/io/writer.py":   {"summary": {"covered_lines": ${io_covered}, "num_statements": ${io_total}}},
    "src/config_manager/api/routes.py":  {"summary": {"covered_lines": ${api_covered}, "num_statements": ${api_total}}}
  }
}
JSON
}

write_web_report() {
  local covered="$1" total="$2"
  cat >"${WORK}/web.json" <<JSON
{"covered_lines": ${covered}, "code_lines": ${total}}
JSON
}

# 全部達標的那組數字：每一層 90/100。
all_green() {
  write_python_report 90 100 90 100 90 100
  write_web_report 90 100
}

run_gate() {
  CM_COVERAGE_JSON="${WORK}/coverage.json" CM_WEB_COVERAGE="${WORK}/web.json" run "${GATE}"
}

@test "四層都達標時通過" {
  all_green
  run_gate
  [ "${status}" -eq 0 ]
}

@test "四層都達標時，四層都被報出來——不是只報有問題的那些" {
  all_green
  run_gate
  local area
  for area in core io api web; do
    [[ "${output}" == *"${area}"* ]]
  done
}

@test "core 低於下限時擋下" {
  write_python_report 80 100 90 100 90 100
  write_web_report 90 100
  run_gate
  [ "${status}" -ne 0 ]
}

@test "core 低於下限時，被指名的只有 core" {
  # 這一則才是「四個門檻各自獨立」的證據：一個守著總數的門檻在這裡也會非零結束，
  # 但它說不出是哪一層——而說不出來的門檻，修的時候只能靠猜。
  write_python_report 80 100 90 100 90 100
  write_web_report 90 100
  run_gate
  [[ "${output}" == *"core/ 低於它自己的下限"* ]]
  [[ "${output}" != *"io/ 低於"* ]]
  [[ "${output}" != *"api/ 低於"* ]]
  [[ "${output}" != *"web/ 低於"* ]]
}

@test "io 低於下限時，被指名的只有 io" {
  write_python_report 90 100 80 100 90 100
  write_web_report 90 100
  run_gate
  [[ "${output}" == *"io/ 低於它自己的下限"* ]]
  [[ "${output}" != *"core/ 低於"* ]]
}

@test "api 低於下限時，被指名的只有 api" {
  write_python_report 90 100 90 100 80 100
  write_web_report 90 100
  run_gate
  [[ "${output}" == *"api/ 低於它自己的下限"* ]]
  [[ "${output}" != *"io/ 低於"* ]]
}

@test "web 低於下限時，被指名的只有 web" {
  write_python_report 90 100 90 100 90 100
  write_web_report 80 100
  run_gate
  [[ "${output}" == *"web/ 低於它自己的下限"* ]]
  [[ "${output}" != *"core/ 低於"* ]]
}

@test "兩層同時低於下限時，兩層都被指名——不是碰到第一個就停" {
  # 碰到第一個就中止的閘門，會把「修覆蓋率」變成修一層、重跑、再撞下一層。
  write_python_report 80 100 80 100 90 100
  write_web_report 90 100
  run_gate
  [[ "${output}" == *"core/ 低於"* ]]
  [[ "${output}" == *"io/ 低於"* ]]
}

@test "剛好等於下限算通過" {
  write_python_report 85 100 90 100 90 100
  write_web_report 90 100
  run_gate
  [ "${status}" -eq 0 ]
}

@test "web 的報告不存在時擋下" {
  write_python_report 90 100 90 100 90 100
  rm -f "${WORK}/web.json"
  run_gate
  [ "${status}" -ne 0 ]
}

@test "報告不存在說的是「沒有被量到」，不是「覆蓋率不足」" {
  # 兩者要做的事完全不同：一個是補測試，一個是量測本身壞了。訊息混在一起，
  # 修的人會往錯的方向走。
  write_python_report 90 100 90 100 90 100
  rm -f "${WORK}/web.json"
  run_gate
  [[ "${output}" == *"沒有被量到"* ]]
  [[ "${output}" != *"web/ 低於它自己的下限"* ]]
}

@test "某一層一行可量的都沒有時擋下，而不是當成 100%" {
  # 0/0 在算術上沒有答案，而「沒有東西可量」被讀成滿分，正是一個門檻能出的最糟的錯。
  write_python_report 90 100 90 100 0 0
  write_web_report 90 100
  run_gate
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"沒有被量到"* ]]
}
