#!/usr/bin/env bats
#
# `justfile` — 任務進入點的接線。
#
# 被觀察的是命令列（T19）：在一份**乾淨簽出**上跑 `just`，看它列得出哪些指令、
# 結束碼是什麼。不碰 recipe 本體——那些是薄 wrapper，#74 把它們列為刻意的空格，
# 而那個理由到今天仍然成立。**接線不是**：`justfile` 先前把 repo 自己的命令組
# 註冊在 `import? 'script/local/justfile.local'`，而 `.gitignore` 的 `*.local`
# 讓那個檔案永遠不存在於簽出。`import?` 的問號使它缺席時不報錯，所以
# 「repo 送出了一組沒有人叫得動的指令」這件事**安靜地成立**（#108）。
#
# 為什麼要在一份另外簽出的樹上跑，而不是在 REPO_ROOT 上：開發用的簽出可能真的有
# 一份 `script/local/justfile.local`（操作者自有的，永不提交），那樣這則規格會因為
# 一個不在版控裡的檔案而變綠——測到的是那台機器，不是這個 repo。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  CHECKOUT="$(mktemp -d)"
  # 「乾淨簽出」的定義就是 `git ls-files`：被追蹤的檔案，一個不多一個不少。
  # 內容取自工作區而不是 HEAD，這樣一次還沒提交的修改也在這則規格的管轄內。
  local file
  while IFS= read -r -d '' file; do
    # `ls-files` 也會列出 gitlink（`.claude/skills/` 底下有一個），那不是檔案。
    [[ -f "${REPO_ROOT}/${file}" ]] || continue
    mkdir -p "${CHECKOUT}/$(dirname "${file}")"
    cp "${REPO_ROOT}/${file}" "${CHECKOUT}/${file}"
  done < <(git -C "${REPO_ROOT}" ls-files -z)
}

teardown() {
  rm -rf "${CHECKOUT}"
}

@test "乾淨簽出上 just --list 列得出 repo 自己註冊的 cfg 命令組" {
  run just --justfile "${CHECKOUT}/justfile" --working-directory "${CHECKOUT}" --list
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"cfg"* ]]
}

@test "乾淨簽出上 cfg 的 recipe 叫得出來，不是只有命名空間存在" {
  run just --justfile "${CHECKOUT}/justfile" --working-directory "${CHECKOUT}" --list cfg
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"list"* ]]
  [[ "${output}" == *"serve"* ]]
}

@test "操作者自有的 justfile.local 缺席不算錯誤" {
  [ ! -e "${CHECKOUT}/script/local/justfile.local" ]

  run just --justfile "${CHECKOUT}/justfile" --working-directory "${CHECKOUT}" --list
  [ "${status}" -eq 0 ]
}

@test "docker 與 test 兩個既有命名空間仍然列得出來" {
  run just --justfile "${CHECKOUT}/justfile" --working-directory "${CHECKOUT}" --list
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"docker"* ]]
  [[ "${output}" == *"test"* ]]
}
