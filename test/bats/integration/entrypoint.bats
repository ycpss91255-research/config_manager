#!/usr/bin/env bats
#
# script/entrypoint.sh — 啟動前置檢查（測試介面 T15）。
#
# 這一層測的是「掛載進來的 config-repo 長什麼樣，entrypoint 怎麼反應」。用真實的
# 目錄與真實的 git，因為要驗的正是它對檔案系統狀態的判斷。
#
# entrypoint 的最後一行是 exec "$@"，所以規格餵它一個 true：前置檢查過了就結束碼 0，
# 沒過就在 exec 之前 die。
#
# test/bats/system/entrypoint_smoke.bats 是不同的東西——那是建置期的煙霧測試，在
# Dockerfile 的 runtime-test 階段對建好的映像跑，問的是「裝進去了嗎」。這裡問的是行為，
# 而且問的對象是工作目錄裡的那份腳本，所以它屬於整合層、由 script/test.sh 執行。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  ENTRYPOINT="${REPO_ROOT}/script/entrypoint.sh"
  WORK="$(mktemp -d)"
  # 注入用的 git 替身要委派給真正的 git，所以先把它的位置記下來——PATH 被改過
  # 之後就找不到了。
  REAL_GIT="$(command -v git)"
  export CM_ROLE=backend
  # 對齊 runtime 映像的 ENV（Dockerfile:172）。entrypoint 不自己推算它——那是
  # 映像的職責——所以規格要把它給進來，否則測到的是一個產品裡不存在的環境。
  export PYTHONPATH="${REPO_ROOT}/src"
}

teardown() {
  # 有幾則規格會把目錄改成不可寫（那正是它們要驗的狀況）。不可寫的目錄裡刪不掉
  # 東西，所以先把權限加回來，否則 teardown 自己會靜默留下一堆暫存目錄。
  chmod -R u+w "${WORK}" 2>/dev/null || true
  rm -rf "${WORK}"
}

# 能通過 load() 的最小清單檔，內容同設計文件 §4.3 的範例。
write_minimal_list() {
  cat >"$1/config-list.toml" <<'TOML'
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
TOML
}

@test "掛載非空但沒有 .git 會失敗，不自動初始化" {
  # 這不是首次啟動——那裡有沒人審過的檔案。#86 之後 io/git.record 的第一步是
  # git add -A，自動 git init 等於把它們默默收編進第一次提交，而掛錯路徑會
  # 因此看起來像啟動成功（不變式 2 禁止的靜默成功）。
  printf 'not ours\n' >"${WORK}/stray.txt"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [ ! -e "${WORK}/.git" ]
}

@test "空目錄被初始化並繼續，訊息帶解析後的絕對路徑" {
  # 空目錄是首次啟動的合法狀態。掛錯到一個空目錄與真正的首次啟動，從容器內部
  # 分不出來——所以路徑要印成解析後的絕對形式，那是操作者唯一能認的東西。
  # 餵一條繞路的路徑，斷言印出來的不是原樣回吐。
  mkdir -p "${WORK}/repo" "${WORK}/other"

  CM_CONFIG_REPO="${WORK}/other/../repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"${WORK}/repo"* ]]
  [ -d "${WORK}/repo/.git" ]
}

@test "空目錄初始化時一併種下最小清單檔，且 load 解析得過" {
  # compose 的 config_repo 是具名 volume，第一次 up 時是空的。沒有種子檔的話
  # 「清單檔不存在 → 失敗」會讓全新安裝根本起不來——而一個什麼都還沒納管的
  # 系統，清單應該是空的，不是開不起來（#66）。
  mkdir -p "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ -f "${WORK}/repo/config-list.toml" ]

  # 種子檔的合法性由 core 的 load 判定，不由這支規格自己重寫一套 TOML 檢查。
  run python -c "
import sys
from config_manager.core.config_list import load
config_list = load(open('${WORK}/repo/config-list.toml', encoding='utf-8').read())
sys.exit(0 if config_list.files == [] else 1)
"
  [ "${status}" -eq 0 ]
}

@test "種下的清單檔已被提交，不是留在工作區未追蹤" {
  # 留成未追蹤檔的話，io/git.record 的第一次 git add -A 才會把它掃進某一筆
  # 使用者變更裡，那筆紀錄就說了謊——它宣稱的改動不是它真正含的東西。
  mkdir -p "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ -z "$(git -C "${WORK}/repo" status --porcelain)" ]
}

@test "已是有效 git repo 且清單檔就緒時直接繼續，不重新初始化" {
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  local before
  before="$(git -C "${WORK}" rev-parse --git-dir)"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ "$(git -C "${WORK}" rev-parse --git-dir)" = "${before}" ]
}

@test "repo 已存在但清單檔不見時失敗，原因指名該路徑" {
  # 這不是首次啟動——首次啟動時已經種下了。所以是有人刪了它，或掛錯路徑。
  git init --quiet --initial-branch=main "${WORK}"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"config-list.toml"* ]]
}

@test "清單檔壞掉時在 exec 之前就失敗" {
  # 前置檢查的意義是「不啟動服務」。清單檔壞掉卻讓 exec 跑起來的話，錯誤會延到
  # 之後某個請求才爆——離現場最遠的地方（不變式 2）。
  git init --quiet --initial-branch=main "${WORK}"
  printf 'list_version = [unclosed\n' >"${WORK}/config-list.toml"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"config-list.toml"* ]]
}

@test "來源內容不存在時在 exec 之前就失敗，訊息指名該條目" {
  git init --quiet --initial-branch=main "${WORK}"
  cat >"${WORK}/config-list.toml" <<'TOML'
list_version = 1

[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"

[[files]]
uid      = "mfz3k9q1"
name     = "navigation-params"
hostname = "amr01"
source   = "files/amr01/nav2_params.yaml"
target   = "/opt/robot/config/nav2_params.yaml"
format   = "yaml"
groups   = []
TOML

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"navigation-params@amr01-mfz3k9q1"* ]]
}

@test "掛載不存在會失敗，原因指名該路徑" {
  CM_CONFIG_REPO="${WORK}/nowhere" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"${WORK}/nowhere"* ]]
}

@test "有 .git 但不是有效 git repo 會失敗" {
  printf 'not a gitfile\n' >"${WORK}/.git"

  CM_CONFIG_REPO="${WORK}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
}

@test "CM_CONFIG_REPO 未設定會失敗" {
  unset CM_CONFIG_REPO

  run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"CM_CONFIG_REPO"* ]]
}

# --- 首次啟動路徑上的三則失敗訊息（#118）--------------------------------------
#
# 設計 §0.4：面向使用者的錯誤訊息必須含三要素——發生什麼、在哪裡、該怎麼改。
# 這三條路徑（git init、種子寫出、初始 commit）觸發的時機是容器起不來，也就是
# 沒有人在旁邊可以問的時候，所以訊息裡沒帶原因等於把唯一的線索丟掉。
#
# 斷言的是「有沒有把底層工具說的話帶出來」與「有沒有下一步」，不是只看結束碼——
# 只斷言結束碼的規格，把訊息改壞也不會轉紅（#118 明寫）。
#
# 前兩條要的是「git init 成功、之後的寫入失敗」，那在真實檔案系統上做不出來
# （目錄不可寫的話 git init 自己就先失敗了）。所以用注入的方式把那個失敗做出來，
# 與 T8 用 monkeypatch 讓 os.replace 失敗是同一種手法。

# PATH 前面放一支 git 替身。除了被指名要失敗的那個子指令，其餘一律委派給真正的
# git——替身只負責注入一個失敗，不重寫 git 的行為。
stub_git_failing_at() {
  local subcommand="$1"
  mkdir -p "${WORK}/bin"
  cat >"${WORK}/bin/git" <<STUB
#!/usr/bin/env bash
for arg in "\$@"; do
  if [[ "\${arg}" == "${subcommand}" ]]; then
    printf 'fatal: injected %s failure\n' "${subcommand}" >&2
    exit 128
  fi
done
exec "${REAL_GIT}" "\$@"
STUB
  chmod +x "${WORK}/bin/git"
  PATH="${WORK}/bin:${PATH}"
  export PATH
}

# git init 成功之後才讓掛載變成不可寫。read-only remount、NFS、SELinux 都會產生
# 這個形狀：目錄建得起來，之後的寫入被拒。
stub_git_locking_the_repo_after_init() {
  local repo="$1"
  mkdir -p "${WORK}/bin"
  cat >"${WORK}/bin/git" <<STUB
#!/usr/bin/env bash
if [[ "\$1" == "init" ]]; then
  "${REAL_GIT}" "\$@" || exit \$?
  chmod 0555 "${repo}"
  exit 0
fi
exec "${REAL_GIT}" "\$@"
STUB
  chmod +x "${WORK}/bin/git"
  PATH="${WORK}/bin:${PATH}"
  export PATH
}

# entrypoint 自己那一則訊息，不是整段輸出。底層工具的 stderr 也會出現在 output
# 裡，拿整段去比對等於「原因有沒有出現在螢幕上」——那一題在改壞訊息之後仍然是
# 是，所以規格必須只看 die 印出來的那一行。
die_message() {
  printf '%s\n' "${output}" | grep '^entrypoint: ' | tail -1
}

@test "git init 失敗時訊息帶得出 git 說的原因與下一步" {
  # 可讀不可寫的空掛載：git init 在這裡是真的失敗，不是注入的。
  mkdir -p "${WORK}/repo"
  chmod 0555 "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  local message
  message="$(die_message)"
  # 在哪裡
  [[ "${message}" == *"${WORK}/repo"* ]]
  # 該怎麼改
  [[ "${message}" == *"next:"* ]]
  # 發生什麼——git 自己說的原因要被帶進這一行，不是丟掉換成一句「做不到」
  [[ "${message}" == *"ermission denied"* || "${message}" == *"annot"* ]]
}

@test "種子清單檔寫不進去時訊息帶得出原因與下一步" {
  mkdir -p "${WORK}/repo"
  stub_git_locking_the_repo_after_init "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  local message
  message="$(die_message)"
  [[ "${message}" == *"config-list.toml"* ]]
  [[ "${message}" == *"next:"* ]]
  [[ "${message}" == *"ermission denied"* ]]
}

@test "初始 commit 失敗時訊息帶得出 git 說的原因與下一步" {
  mkdir -p "${WORK}/repo"
  stub_git_failing_at commit

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  local message
  message="$(die_message)"
  [[ "${message}" == *"config-list.toml"* ]]
  [[ "${message}" == *"next:"* ]]
  [[ "${message}" == *"injected commit failure"* ]]
}
