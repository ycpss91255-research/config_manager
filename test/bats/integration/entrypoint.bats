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
  # 這不是首次啟動——那裡有沒人審過的檔案。自動 git init 等於把一個不屬於我們的
  # 目錄當成 config-repo 收下，而掛錯路徑會因此看起來像啟動成功（不變式 2 禁止的
  # 靜默成功）。
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
  # 留成未追蹤檔的話，第一次納管會把它折進那筆 import commit，那筆紀錄就說了謊
  # ——它宣稱的改動不是它真正含的東西。
  mkdir -p "${WORK}/repo"

  CM_CONFIG_REPO="${WORK}/repo" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ -z "$(git -C "${WORK}/repo" status --porcelain)" ]
}

@test "首次啟動從 CM_ALLOWED_ROOTS 種下白名單設定檔，並提交（#202）" {
  # allowed-roots.toml 是持久化白名單（§7.9）：entrypoint 首次啟動從 CM_ALLOWED_ROOTS
  # 種一份，之後以檔為準、由介面增減。沒有種子的話「白名單設定檔不存在 → preflight 失敗」
  # 會讓升級到這版的既有部署起不來。
  mkdir -p "${WORK}/repo" "${WORK}/targets"

  CM_CONFIG_REPO="${WORK}/repo" CM_ALLOWED_ROOTS="${WORK}/targets" \
    run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  [ -f "${WORK}/repo/allowed-roots.toml" ]
  # 已提交、不留在工作區未追蹤（同清單檔種子的理由）。
  [ -z "$(git -C "${WORK}/repo" status --porcelain)" ]

  # 種下的前綴由 core 的 load 判定合法，不由這支規格自己重寫一套 TOML 檢查；
  # 且要含 CM_ALLOWED_ROOTS 給的那個根。
  run python -c "
import sys
from config_manager.core.allowed_roots import load
allowed = load(open('${WORK}/repo/allowed-roots.toml', encoding='utf-8').read())
sys.exit(0 if [r.prefix for r in allowed.roots] == ['${WORK}/targets'] else 1)
"
  [ "${status}" -eq 0 ]
}

@test "CM_ALLOWED_ROOTS 的根含破壞 TOML 的字元時，種子在寫檔前就失敗並指名該變數（#202）" {
  # 種檔是手刻 printf、不像 API 走 tomlkit 跳脫，含 " 或反斜線的根會產出無法解析的檔，
  # 讓 preflight 以「檔案壞了」死掉、指錯方向。改在種檔前擋下、指向真正該修的 CM_ALLOWED_ROOTS。
  # 用一個名字真的含引號的目錄（先過 #146 的可見性檢查），才驗得到種子這道守門。
  local baddir="${WORK}/has\"quote"
  mkdir -p "${WORK}/repo" "${baddir}"

  CM_CONFIG_REPO="${WORK}/repo" CM_ALLOWED_ROOTS="${baddir}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"CM_ALLOWED_ROOTS"* ]]
  # 沒有留下半種的檔——種子在寫出前就擋下。
  [ ! -f "${WORK}/repo/allowed-roots.toml" ]
}

@test "CM_ALLOWED_ROOTS 有重複的根時，種子在寫檔前就失敗並指名該變數（#233/#232 發現4）" {
  # 重複前綴會種成 core 的 _check_integrity 拒絕的 DuplicatePrefix，preflight 以「檔案壞了」
  # 死掉、指向機器生成檔（§0.4 指錯方向），且毒檔已提交→重啟 seed-if-missing 跳過→崩潰迴圈。
  # 兩條都是可見的真實目錄（先過 #146），才驗得到種子這道守門。
  mkdir -p "${WORK}/repo" "${WORK}/targets"

  CM_CONFIG_REPO="${WORK}/repo" CM_ALLOWED_ROOTS="${WORK}/targets,${WORK}/targets" \
    run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"CM_ALLOWED_ROOTS"* ]]
  [ ! -f "${WORK}/repo/allowed-roots.toml" ]
}

@test "CM_ALLOWED_ROOTS 的根含 .. 路徑段時，種子在寫檔前就失敗並指名該變數（#233/#232 發現4）" {
  # 含 .. 的根會種成 core 的 check_prefix 拒絕的 InvalidPrefix，同樣讓 preflight 死在機器生成
  # 檔、且毒檔已提交→崩潰迴圈。可見性檢查以 [[ -d ]] 解析 .. 而通過，故要在種子這道字面擋下。
  # x 與 targets 都建起來，讓 ${WORK}/x/../targets 解析得過 #146 的 -d 檢查。
  mkdir -p "${WORK}/repo" "${WORK}/x" "${WORK}/targets"

  CM_CONFIG_REPO="${WORK}/repo" CM_ALLOWED_ROOTS="${WORK}/x/../targets" \
    run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"CM_ALLOWED_ROOTS"* ]]
  [ ! -f "${WORK}/repo/allowed-roots.toml" ]
}

@test "preflight 非零退出卻無輸出時，die 仍給出可行動訊息而非空原因（#233/#232 發現7）" {
  # preflight 被 OOM/SIGKILL（exit 137、無 stdout/stderr）時，check_config_list 的 die 若直接
  # 用 ${output} 只會印「entrypoint: 」（無原因），違反 die() 自身承諾與 §0.4 三要素。
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  # stub python：非零退出、完全無輸出，模擬被信號中止。
  local stub="${WORK}/stub"
  mkdir -p "${stub}"
  printf '#!/usr/bin/env bash\nexit 137\n' >"${stub}/python"
  chmod +x "${stub}/python"

  CM_CONFIG_REPO="${WORK}" PATH="${stub}:${PATH}" run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  # 不是只印空原因——要帶得出下一步。
  [[ "${output}" == *"下一步"* ]]
}

@test "已存在的白名單設定檔不被種子覆蓋（以檔為準，#202）" {
  # 以檔為準：一旦有了這份檔，重啟不因 CM_ALLOWED_ROOTS 改變而動它——介面加的根不該
  # 被下一次啟動的種子抹掉。檔裡的根指向一個真實可見的目錄（否則會卡在 #146 可見性檢查，
  # 那檢查如今讀的正是這份檔），env 指向另一個目錄，驗種子沒有把 env 的根塞進既有檔。
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  mkdir -p "${WORK}/targets" "${WORK}/kept"
  printf 'roots_version = 1\n\n[[roots]]\nprefix = "%s/kept"\nadded_by = "介面"\nadded_at = "2026-01-01T00:00:00Z"\n' \
    "${WORK}" >"${WORK}/allowed-roots.toml"

  CM_CONFIG_REPO="${WORK}" CM_ALLOWED_ROOTS="${WORK}/targets" run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
  # 既有內容原封不動，種子沒有把 CM_ALLOWED_ROOTS 的根塞進來。
  [[ "$(cat "${WORK}/allowed-roots.toml")" == *"${WORK}/kept"* ]]
  [[ "$(cat "${WORK}/allowed-roots.toml")" != *"targets"* ]]
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

@test "白名單允許的路徑在容器裡看不到時，啟動失敗並指名是哪一條（#146）" {
  # 掛載是白名單的上界：白名單有、掛載沒有 → 大聲失敗，而不是等第一次寫出才以指錯
  # 方向的「權限不足」爆。
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"

  CM_CONFIG_REPO="${WORK}" CM_ALLOWED_ROOTS="${WORK}/not-mounted" \
    run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"${WORK}/not-mounted"* ]]
}

@test "白名單允許的路徑都在容器裡看得到時，繼續啟動（#146）" {
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  mkdir -p "${WORK}/targets"

  CM_CONFIG_REPO="${WORK}" CM_ALLOWED_ROOTS="${WORK}/targets" \
    run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
}

@test "可見性檢查以檔裡的根為準、不以 CM_ALLOWED_ROOTS 為準（env 與檔漂移，#232 發現5）" {
  # 首啟後白名單以檔為準（#202）：經介面增減只寫檔、不改 env。開機可見性（#146）若驗 env
  # 就驗錯對象。這裡讓兩者漂移：檔裡放一個看不到的根、env 放一個看得到的根——驗 env 會
  # （錯誤地）通過，驗檔會（正確地）因看不到而死。檔已存在故種子不覆蓋（#202）。
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  mkdir -p "${WORK}/env-visible"
  printf 'roots_version = 1\n\n[[roots]]\nprefix = "%s/file-missing"\nadded_by = "介面"\nadded_at = "2026-01-01T00:00:00Z"\n' \
    "${WORK}" >"${WORK}/allowed-roots.toml"

  CM_CONFIG_REPO="${WORK}" CM_ALLOWED_ROOTS="${WORK}/env-visible" \
    run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  [[ "${output}" == *"file-missing"* ]]
}

@test "env 的根看不到、但檔裡的根看得到時仍繼續（可見性以檔為準，#232 發現5）" {
  # 對稱：env 裡有個看不到的根，但生效白名單（檔）的根都看得到 → 應繼續。驗 env 會誤死。
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  mkdir -p "${WORK}/file-visible"
  printf 'roots_version = 1\n\n[[roots]]\nprefix = "%s/file-visible"\nadded_by = "介面"\nadded_at = "2026-01-01T00:00:00Z"\n' \
    "${WORK}" >"${WORK}/allowed-roots.toml"

  CM_CONFIG_REPO="${WORK}" CM_ALLOWED_ROOTS="${WORK}/env-missing" \
    run "${ENTRYPOINT}" true
  [ "${status}" -eq 0 ]
}

@test "白名單設定檔已被追蹤卻工作區遺失時大聲失敗，不從 CM_ALLOWED_ROOTS 重種（#232 發現6）" {
  # seed-if-missing 只看工作區檔在不在。一份已 commit、但工作區被刪的 allowed-roots.toml，
  # 若從 env 重種會靜默用部署 env 蓋掉之後經介面增減的白名單。這裡：committed 檔有 api-added，
  # 刪掉工作區檔、env 給別的（可見）根 → 斷言啟動失敗、指路從 git 還原，且沒有從 env 重生。
  git init --quiet --initial-branch=main "${WORK}"
  write_minimal_list "${WORK}"
  mkdir -p "${WORK}/targets"
  printf 'roots_version = 1\n\n[[roots]]\nprefix = "%s/api-added"\nadded_by = "介面"\nadded_at = "2026-01-01T00:00:00Z"\n' \
    "${WORK}" >"${WORK}/allowed-roots.toml"
  git -C "${WORK}" add allowed-roots.toml
  git -C "${WORK}" -c user.name=t -c user.email=t@e.invalid commit -q -m "committed roots"
  rm "${WORK}/allowed-roots.toml"

  CM_CONFIG_REPO="${WORK}" CM_ALLOWED_ROOTS="${WORK}/targets" \
    run "${ENTRYPOINT}" true
  [ "${status}" -ne 0 ]
  # 沒有從 env 重生：若重生，工作區會冒出一份含 targets 的檔。
  [ ! -f "${WORK}/allowed-roots.toml" ]
  [[ "${output}" == *"還原"* ]]
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
  [[ "${message}" == *"下一步："* ]]
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
  [[ "${message}" == *"下一步："* ]]
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
  [[ "${message}" == *"下一步："* ]]
  [[ "${message}" == *"injected commit failure"* ]]
}
