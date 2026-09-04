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

@test "掃描的預設目標是 src/config_manager，且它現在是乾淨的" {
  cd "${REPO_ROOT}"
  run "${LINT}"
  [ "${status}" -eq 0 ]
}
