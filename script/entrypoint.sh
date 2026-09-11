#!/usr/bin/env bash
#
# 容器啟動。這支就是容器的 entrypoint（Dockerfile 的 ENTRYPOINT），不是被某個
# orchestrator source 進去的片段——共用模板那種兩段式 entrypoint 是跟著模板一起
# 來的，而 v0.10.0 不引入模板。
#
# 有一條契約無論如何都要守住：這個檔案做的最後一件事是 `exec "$@"`。少了它，
# 工作負載會變成這個 shell 的子程序，PID 1 留在這裡，SIGTERM 就永遠送不到真正
# 需要收工的那個程序——變成一個無法乾淨停止的容器。
#
# frontend 與 backend 共用這個檔案。差別在於它們被交付的指令，所以下面的檢查是
# 兩者都成立的那些，另外加上一段以 CM_ROLE 圍起來、只給 backend 的區塊。
set -euo pipefail

die() {
  # 錯誤訊息要指名檔案與原因。「啟動失敗」沒有給操作者任何可以動手的東西
  # （設計原則：錯誤訊息必須可據以行動），而啟動正好是沒有人盯著看的時候。
  printf 'entrypoint: %s\n' "$*" >&2
  exit 1
}

# core/models 能接受的最小 config-list.toml：list_version 加上一段
# defaults.permissions。`files` 預設就是空清單，所以不寫——首次啟動時還沒有任何
# 東西被納管，那正是正確的狀態。
#
# 值取自設計文件自己的範例（§4.3），不是在這裡自己編的。
#
# 這裡複製了 core/models 宣告的形狀，那是實實在在的成本。但它不會靜默出錯：
# preflight 在同一次啟動的稍後就會跑，load() 會擋下一份已經不再合法的種子。
# 偏離會在這裡大聲失敗，而不是在之後某個請求裡才浮出來。
#
# 它被提交進版控，不是留在工作區未追蹤：repo 從一個已追蹤、合法的基線起步，第一次
# 納管的 commit 才會只含那次動到的東西。納管會 stage config-list.toml；種子若留成
# 未追蹤，會被折進第一筆 import commit，讓那筆紀錄宣稱了它其實沒做的初始化。
# 種子內容單獨一支，讓寫出那一步可以把失敗原因捕捉起來。heredoc 與錯誤捕捉寫在
# 同一個指令上時，捕不捕得到由重導向的先後順序決定——那不是應該靠讀者自己看出來
# 的東西。
seed_toml() {
  cat <<'TOML'
list_version = 1

# 未個別指定時套用的預設權限
[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
TOML
}

seed_config_list() {
  local repo="$1"
  local list="${repo}/config-list.toml"
  local output

  # 底層工具說了什麼要一起帶出來。「could not write」只說了做不到——權限不足、
  # 磁碟滿、路徑被別的東西佔住，三者的下一步完全不同，而操作者從那句話裡分辨
  # 不出來（設計 §0.4 三要素）。
  if ! output="$({ seed_toml >"${list}"; } 2>&1)"; then
    die "寫不出初始的 config 清單檔 ${list}：" \
      "${output:-shell 沒有給出任何錯誤輸出}；" \
      "下一步：確認 config-repo 的掛載對執行這個容器的使用者可寫，" \
      "且該 volume 沒有滿、也不是唯讀掛載"
  fi

  # 用 -c 而不用 `git config`：這個身分只屬於這一筆 commit，不屬於操作者之後
  # 會用到的那個 repo。
  if ! output="$({ git -C "${repo}" add config-list.toml \
    && git -C "${repo}" \
      -c user.name="config_manager" \
      -c user.email="config_manager@localhost" \
      commit --quiet -m "chore(repo): 初始化空的 config 清單檔"; } 2>&1)"; then
    die "提交不了初始的 config 清單檔 ${list}：" \
      "${output:-git 沒有給出任何錯誤輸出}；" \
      "下一步：確認 ${repo}/.git 可寫、也沒有被別的程序鎖住，" \
      "然後手動提交 ${list}，或在重新啟動前把它刪掉——" \
      "留成未追蹤的話，第一次納管會把它折進那筆 import commit，讓紀錄宣稱了它沒做的初始化"
  fi

  printf 'entrypoint: 已在 %s 種下一份空的 config 清單檔\n' "${list}"
}

_emit_allowed_roots() {
  # 把 CM_ALLOWED_ROOTS 種成一份白名單設定檔（§7.9, #202）：逗號分隔、去前後空白，
  # 與 check_allowed_roots_visible／api.cli._allowed_roots 同一種切法。每個根記下部署來源
  # 與種下的時間；CM_ALLOWED_ROOTS 空的話就一個根都不放（什麼都不放行，落向安全，不變式 4）。
  local now root trimmed
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'roots_version = 1\n'
  while IFS= read -r root; do
    trimmed="${root#"${root%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -n "${trimmed}" ]] || continue
    printf '\n[[roots]]\nprefix = "%s"\nadded_by = "部署設定 (CM_ALLOWED_ROOTS)"\nadded_at = "%s"\n' \
      "${trimmed}" "${now}"
  done < <(printf '%s\n' "${CM_ALLOWED_ROOTS:-}" | tr ',' '\n')
}

seed_allowed_roots() {
  local repo="$1"
  local file="${repo}/allowed-roots.toml"
  local output

  # 以檔為準（#202）：已經有一份就不動它——之後的增減都經由介面寫這份檔。升級既有部署時
  # 這份檔還不存在，所以這裡是 seed-if-missing、每次啟動都跑，而不是只在首次 git init 時。
  [[ -f "${file}" ]] && return 0

  if ! output="$({ _emit_allowed_roots >"${file}"; } 2>&1)"; then
    die "寫不出初始的白名單設定檔 ${file}：" \
      "${output:-shell 沒有給出任何錯誤輸出}；" \
      "下一步：確認 config-repo 的掛載對執行這個容器的使用者可寫，" \
      "且該 volume 沒有滿、也不是唯讀掛載"
  fi

  if ! output="$({ git -C "${repo}" add allowed-roots.toml \
    && git -C "${repo}" \
      -c user.name="config_manager" \
      -c user.email="config_manager@localhost" \
      commit --quiet -m "chore(repo): 從 CM_ALLOWED_ROOTS 初始化白名單設定檔"; } 2>&1)"; then
    die "提交不了初始的白名單設定檔 ${file}：" \
      "${output:-git 沒有給出任何錯誤輸出}；" \
      "下一步：確認 ${repo}/.git 可寫、也沒有被別的程序鎖住，" \
      "然後手動提交 ${file}，或在重新啟動前把它刪掉"
  fi

  printf 'entrypoint: 已從 CM_ALLOWED_ROOTS 種下白名單設定檔 %s\n' "${file}"
}

check_backend_preconditions() {
  local repo="${CM_CONFIG_REPO:-}"
  local output

  [[ -n "${repo}" ]] || die "CM_CONFIG_REPO 未設定，backend 沒有來源 repo 可以服務。" \
    "下一步：把它設成掛載進來的 config-repo 路徑"
  [[ -d "${repo}" ]] || die "config-repo 的掛載點 ${repo} 不存在。" \
    "下一步：確認那個 volume 真的掛上來了，或改 CM_CONFIG_REPO 指到正確的位置"

  if [[ ! -e "${repo}/.git" ]]; then
    # 空與非空是兩種不同的狀況，而這裡曾經把它們混為一談：註解寫著「空目錄」，
    # 條件卻只問 .git 在不在，於是一個裝滿檔案的目錄被初始化，還被宣告成空的
    # （#69）。
    #
    # 這件事之所以要緊，是因為在別人的檔案上面 git init，等於把一個不屬於我們的
    # 目錄當成 config-repo 收下：一個打錯的掛載路徑看起來就跟啟動成功一模一樣，
    # 而那個目錄從此被這個系統當成唯一真實來源。
    if [[ -n "$(ls -A "${repo}")" ]]; then
      die "config-repo 的掛載點 ${repo} 不是空的，但也不在版控之下。" \
        "下一步：確定要用它的話就自己 git init，否則檢查掛載路徑是不是打錯了"
    fi

    # 空目錄就是首次啟動的情況：volume 存在，還沒有東西初始化過它。掛到另一個
    # 空目錄的錯誤設定，在這裡分辨不出來——所以印出解析後的絕對路徑，讓讀到的人
    # 自己認出這是不是他要的位置，而不是假裝這個差別在這裡就知道得了。
    printf 'entrypoint: 正在初始化空的 config-repo：%s\n' "$(cd "${repo}" && pwd)"
    # git 自己的失敗原因要帶出來：權限、磁碟滿、路徑被佔住，看起來都一樣是
    # 「git init failed」，但下一步不同（設計 §0.4 三要素）。
    if ! output="$(git init --quiet --initial-branch=main "${repo}" 2>&1)"; then
      die "在 ${repo} 初始化 git repo 失敗：" \
        "${output:-git 沒有給出任何錯誤輸出}；" \
        "下一步：確認那個掛載對執行這個容器的使用者可寫、也不是唯讀掛載，" \
        "或在啟動容器前自己對它跑 'git init --initial-branch=main'"
    fi
    seed_config_list "${repo}"
    # 刻意往下掉到 check_config_list。種子複製了 core/models 宣告的形狀，而這裡
    # 正是讓那份複製不會靜默出錯的機制：一份已經不再合法的種子會在這裡被擋下，
    # 就在寫出它的同一次啟動裡，而不是在之後某個請求。
  else
    git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 \
      || die "config-repo 的掛載點 ${repo} 存在，但不是一個 git repo。" \
        "下一步：對它跑 'git init --initial-branch=main'，或檢查掛載路徑"
  fi

  check_allowed_roots_visible
  seed_allowed_roots "${repo}"
  check_config_list "${repo}"
}

# 清單檔，以及它所引用的來源內容。交給 Python 做，因為這個判斷屬於
# core/config_list——在 shell 裡重寫一次「這份清單檔合不合法」，等於給一個
# 已經有答案的問題再生出第二個、會安靜地漂移開來的答案。
#
# 它跑在 `exec "$@"` 之前，這就是重點：壞掉的清單檔必須在這裡把容器擋下來，
# 而不是等服務起來之後才在某個請求裡浮出來。
check_config_list() {
  local repo="$1"
  local output

  if ! output="$(python -m config_manager.io.preflight "${repo}" 2>&1)"; then
    die "${output}"
  fi
}

# 掛載是白名單的上界（#146）：白名單允許的每個根目錄都必須在容器裡看得到，否則寫出會
# 以「目標目錄無法寫入」失敗——訊息指向權限，真正的原因是掛載沒把它帶進來，是一則指錯
# 方向的訊息（§0.4 三要素要防的）。在這裡逐條驗、看不到就大聲失敗並指名，而不是等到第
# 一次寫出才在執行期發現。沒設 CM_ALLOWED_ROOTS = 什麼都不放行（安全預設），沒有根要驗。
check_allowed_roots_visible() {
  local roots="${CM_ALLOWED_ROOTS:-}"
  [[ -n "${roots}" ]] || return 0

  local root trimmed
  # 逗號分隔、去前後空白，與 api/cli._allowed_roots 同一種切法。
  while IFS= read -r root; do
    trimmed="${root#"${root%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -n "${trimmed}" ]] || continue
    # `[[ -d ]]` 失敗有兩種：路徑沒掛進來（ENOENT），或某層上層目錄對服務執行身分沒有
    # traverse（+x）權限（EACCES）——bash 把兩者都收成 false，分不出來。所以訊息把兩個
    # 可能的成因都講出來，而不是只說「去掛載」，否則掛好了卻卡在權限的人會被指錯方向
    # （§0.4；io/source._blocking_parent 在讀取時能分得更細）。
    [[ -d "${trimmed}" ]] || die "白名單允許的路徑在容器裡看不到：${trimmed}。" \
      "下一步：把它掛進容器（compose 的 volumes）、給它某層上層目錄對服務執行身分加上" \
      "traverse（+x）權限、或從 CM_ALLOWED_ROOTS 移除；白名單只在掛載掛得到的範圍內有意義（#146）"
  done < <(printf '%s\n' "${roots}" | tr ',' '\n')
}

main() {
  case "${CM_ROLE:-backend}" in
    backend) check_backend_preconditions ;;
    frontend) ;;
    *) die "不認得的 CM_ROLE '${CM_ROLE}'。下一步：設成 'backend' 或 'frontend'" ;;
  esac

  # 交棒。這一行之後不得再有任何東西。
  exec "$@"
}

main "$@"
