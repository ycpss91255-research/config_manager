#!/usr/bin/env bats
#
# script/lint_messages.sh — 面向使用者的錯誤訊息必須包含三要素。
#
# 設計 §0.4「例外處理的具體要求」第 2 條：「面向使用者的錯誤訊息必須包含三要素：
# 發生什麼、在哪裡（檔案／行號／欄位）、該怎麼改。『格式錯誤』不合格。」而同一節
# 的開頭寫著「所有規範必須可由工具檢查——無法自動檢查的規範等同不存在」。這一條
# 在此之前沒有任何工具檢查它，所以依它自己的規則，它先前並不存在。
#
# 被觀察的是命令列（T19）：餵一個目錄進去，看結束碼與訊息，不碰腳本內部。
#
# 判準刻意保守，因為一支上線就噴一堆錯的 lint 會被關掉，而被關掉的 lint 等於不存在：
#
#   R1  訊息必須含「下一步：」——三要素的第三項「該怎麼改」。
#   R2  訊息必須帶一個具體標的——三要素的第二項「在哪裡」。內插值算，字面的機器
#       識別碼（環境變數名、含 / 的路徑、帶副檔名的檔名）也算；整則都是中文散文、
#       沒有任何可以指過去的東西——「格式錯誤」那一類——才被擋下。
#
#       R2 起初只認內插值，實測 27 則現有訊息擋下 8 則，其中 1 則是誤報
#       （`CM_CONFIG_REPO 未設定`，標的就是那個環境變數名，沒有東西可內插）。
#       誤報率 1/8 對剛上線的 lint 太高，所以放寬到現在這個判準。
#
# 不含中文的訊息判定為「轉述既有訊息或非使用者可見」而不擋——介面文案一律中文
# （CONTEXT.md），所以純 ASCII 的字串是 usage 行或 `f"preflight: {error}"` 這種
# 轉述。**但它們要被列出來**：靜默跳過正是不變式 2 禁止的形狀。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_messages.sh"
  DIR="$(mktemp -d)"
}

teardown() {
  rm -rf "${DIR}"
}

write_py() {
  local name="$1"
  shift
  printf '%s\n' "$@" >"${DIR}/${name}"
}

@test "三要素齊全的訊息通過" {
  write_py ok.py \
    'def f(path: str) -> None:' \
    '    raise ConfigListMissing(' \
    '        f"config-repo 裡沒有清單檔：{path}。下一步：確認掛載指向正確的 config-repo"' \
    '    )'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "缺「下一步：」被擋下，並指名檔案、行號與例外名稱" {
  write_py nofix.py \
    'def f(ref: str) -> None:' \
    '    raise DuplicateUid(f"uid 重複：{ref} 與另一筆共用同一個 uid")'

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"nofix.py:2"* ]]
  [[ "${output}" == *"DuplicateUid"* ]]
  [[ "${output}" == *"下一步"* ]]
}

@test "只說「格式錯誤」——沒有具體標的——被擋下" {
  write_py vague.py \
    'def f() -> None:' \
    '    raise InvalidFormat("格式錯誤。下一步：修正它")'

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"vague.py:2"* ]]
}

@test "字面的環境變數名算標的：那種訊息沒有東西可以內插" {
  write_py envvar.py \
    'import sys' \
    '' \
    'def f() -> None:' \
    '    print("CM_CONFIG_REPO 未設定。下一步：設定它指向掛載進來的 config-repo",' \
    '          file=sys.stderr)'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "字面的路徑也算標的" {
  write_py hardcoded.py \
    'def f() -> None:' \
    '    raise ConfigListMissing("找不到 /etc/config-list.toml。下一步：還原該檔")'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "print 到 stderr 的訊息同樣被檢查" {
  write_py cli.py \
    'import sys' \
    '' \
    'def f(api: str) -> None:' \
    '    print(f"config_manager: 讀不到 {api} 的清單", file=sys.stderr)'

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"cli.py:4"* ]]
}

@test "print 到 stdout 不是錯誤訊息，不被檢查" {
  write_py out.py \
    'def f(name: str) -> None:' \
    '    print(f"已納管 {name}")'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "不含中文的訊息判定為轉述，不擋，但要被列出來" {
  write_py relay.py \
    'import sys' \
    '' \
    'def f(error: Exception) -> None:' \
    '    print(f"preflight: {error}", file=sys.stderr)' \
    '    raise HTTPException(status_code=422, detail=str(error))'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"SKIP"* ]]
  [[ "${output}" == *"relay.py:4"* ]]
  [[ "${output}" == *"relay.py:5"* ]]
}

@test "隱含串接的多段字串算同一則訊息" {
  write_py joined.py \
    'def f(ref: str) -> None:' \
    '    raise SourceMissing(' \
    '        f"清單檔引用的來源內容不存在：{ref}。"' \
    '        "下一步：還原該檔，或從清單檔移除這筆條目"' \
    '    )'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "不是 raise、也不是 stderr 的中文字串不被誤判" {
  write_py doc.py \
    'MESSAGE = "格式錯誤"' \
    '' \
    'def f() -> str:' \
    '    """回傳一段說明文字。"""' \
    '    return "沒有下一步的說明"'

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "回報每一個違規，不是只報第一個" {
  write_py one.py 'def f(a: str) -> None:' '    raise A(f"壞了：{a}")'
  write_py two.py 'def g(b: str) -> None:' '    raise B(f"也壞了：{b}")'

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"one.py"* ]]
  [[ "${output}" == *"two.py"* ]]
}

@test "語法壞掉的檔案大聲失敗，不當作沒有訊息" {
  write_py broken.py 'def f(' '    pass'

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"broken.py"* ]]
}

@test "給一個檔案時只檢查那一個檔案" {
  write_py bad.py 'def f(a: str) -> None:' '    raise A(f"壞了：{a}")'
  write_py ok.py 'def g(b: str) -> None:' '    raise B(f"壞了：{b}。下一步：修好它")'

  run "${LINT}" "${DIR}/ok.py"
  [ "${status}" -eq 0 ]
  [[ "${output}" != *"bad.py"* ]]

  run "${LINT}" "${DIR}/bad.py"
  [ "${status}" -ne 0 ]
}

@test "目錄裡沒有 Python 檔時不報錯" {
  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "預設標的含 src/config_manager，且它現在是乾淨的" {
  cd "${REPO_ROOT}"
  run "${LINT}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"message(s) in src/config_manager"* ]]
}

# --- shell（#133）-----------------------------------------------------------
#
# `script/` 底下 32 支腳本的執行期輸出先前完全不在管轄內：預設標的是
# `src/config_manager`，單檔模式明寫「不是 .py 就什麼都不檢查」。而
# `doc/review/2026-09-04-pdf-conformance.md` 把 `script/` 「另計」、逐則人工判讀
# ——人工判讀正是 §0.4 說「等同不存在」的那種規範。

write_sh() {
  cat >"${DIR}/$1"
  chmod +x "${DIR}/$1"
}

@test "shell 的訊息缺「下一步：」被擋下，並指名檔案與行號" {
  write_sh nofix.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf 'seed: 寫不進清單檔 %s\n' "$1" >&2
  exit 1
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"nofix.sh:5"* ]]
  [[ "${output}" == *"下一步"* ]]
}

@test "第一行說發生什麼、最後一行說下一步——相鄰的 stderr 寫入算同一則訊息" {
  write_sh block.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf 'seed: 寫不進清單檔 %s\n' "$1" >&2
  printf 'seed: 下一步：確認掛載點可寫，或改指到另一個 config-repo\n' >&2
  exit 1
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "隔太遠的兩則訊息不會互相頂替：後一則的下一步救不了前一則" {
  write_sh apart.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf 'seed: 寫不進清單檔 %s\n' "$1" >&2
  cd /tmp
  ls >/dev/null
  cat /dev/null
  printf 'seed: 下一步：確認掛載點可寫，或改指到另一個 config-repo\n' >&2
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"apart.sh:5"* ]]
}

@test "字面裡的 >&2 不是出口，不會被誤認成訊息" {
  write_sh quoted.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf '把它寫成 foo >&2 就會送到標準錯誤\n'
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
  [[ "${output}" != *"quoted.sh"* ]]
}

@test "heredoc 主體裡的中文散文不是訊息" {
  write_sh heredoc.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/heredoc.sh
  說明文字，沒有下一步，也沒有任何標的
USAGE
}

usage
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "註解裡的 >&2 不是出口" {
  write_sh commented.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  # 這裡本來寫成 printf '格式錯誤\n' >&2，已經拿掉
  return 0
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "轉述函式由結構推導：die 的呼叫點才是訊息，定義不是" {
  write_sh relay.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

die() {
  printf 'entrypoint: %s\n' "$*" >&2
  exit 1
}

main() {
  die "找不到 /etc/config-list.toml"
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"relay.sh:10"* ]]
  [[ "${output}" == *"die()"* ]]
}

@test "只內插第一個位置參數的函式不算轉述函式：任何吃位置參數的函式都長那樣" {
  write_sh positional.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  case "$1" in
    *) printf 'build.sh: unknown argument %s\n' "$1" >&2; exit 2 ;;
  esac
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
  [[ "${output}" != *"main()"* ]]
}

@test "同一個出口把用法說明一起送到 fd 2 時，「該怎麼改」已經在使用者眼前" {
  write_sh withusage.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: script/withusage.sh [--all]
USAGE
}

main() {
  case "${1:-}" in
    --all) ;;
    *) printf 'withusage.sh: 不認得的參數 %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "bash 解析不了的 shell 檔大聲失敗，不當作沒有訊息" {
  write_sh broken.sh <<'SH'
#!/usr/bin/env bash
main() {
  if [[ -z "$1" ]]; then
    printf 'broken.sh: 壞了。下一步：修好它\n' >&2
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"broken.sh"* ]]
  [[ "${output}" == *"NOT"* || "${output}" == *"cannot be parsed"* ]]
}

@test "不含中文的 shell 訊息判為轉述，但跳過的數量印成一段大聲的結論" {
  write_sh english.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf 'english.sh: something went wrong\n' >&2
  exit 1
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"SKIP"* ]]
  [[ "${output}" == *"english.sh:5"* ]]
  [[ "${output}" == *"1 則訊息不含中文"* ]]
  [[ "${output}" == *"下一步"* ]]
}

@test "全大寫沒有底線的環境變數名算標的" {
  write_sh envvar.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf 'release: gh 不在 PATH 上，release 建不起來\n' >&2
  printf 'release: 下一步：安裝 gh，或改在有 gh 的 CI job 上執行\n' >&2
  exit 1
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
}

@test "被描述的那個「。」不足以判定訊息是中文" {
  write_sh period.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf 'lint_commit: subject ends with a period (. or 。); drop it\n' >&2
  exit 1
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"SKIP"* ]]
}

@test "給一支 shell 腳本時只檢查那一支" {
  write_sh only.sh <<'SH'
#!/usr/bin/env bash
main() {
  printf 'only.sh: 壞了 %s\n' "$1" >&2
}
SH
  write_sh fine.sh <<'SH'
#!/usr/bin/env bash
main() {
  printf 'fine.sh: 壞了 %s。下一步：修好它\n' "$1" >&2
}
SH

  run "${LINT}" "${DIR}/fine.sh"
  [ "${status}" -eq 0 ]
  [[ "${output}" != *"only.sh"* ]]

  run "${LINT}" "${DIR}/only.sh"
  [ "${status}" -ne 0 ]
}

@test "預設標的含 script，且它現在是乾淨的" {
  cd "${REPO_ROOT}"
  run "${LINT}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"message(s) in script"* ]]
}

@test "掃不下去時大聲失敗：讀不懂的引號不等於那底下沒有訊息" {
  write_sh ansiquote.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail

main() {
  printf $'don\'t\n'
  printf 'main: 壞了 %s\n' "$1" >&2
}

main "$@"
SH

  run "${LINT}" "${DIR}"
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"ansiquote.sh"* ]]
  [[ "${output}" == *"NOT"* ]]
}
