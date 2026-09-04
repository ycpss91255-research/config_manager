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
# 它被提交進版控，不是留在工作區未追蹤。io/git.record 開頭是 `git add -A`，
# 未追蹤的種子會被掃進「剛好第一個被記錄的使用者變更」裡——那筆紀錄於是宣稱了
# 它其實沒有包含的改動。
seed_config_list() {
  local repo="$1"
  local list="${repo}/config-list.toml"

  cat >"${list}" <<'TOML' || die "could not write ${list}"
list_version = 1

# 未個別指定時套用的預設權限
[defaults.permissions]
owner = "root"
group = "root"
mode = "0644"
TOML

  # 用 -c 而不用 `git config`：這個身分只屬於這一筆 commit，不屬於操作者之後
  # 會用到的那個 repo。
  git -C "${repo}" add config-list.toml \
    && git -C "${repo}" \
      -c user.name="config_manager" \
      -c user.email="config_manager@localhost" \
      commit --quiet -m "chore(repo): 初始化空的 config 清單檔" \
    || die "could not commit the initial ${list}"

  printf 'entrypoint: seeded an empty config list at %s\n' "${list}"
}

check_backend_preconditions() {
  local repo="${CM_CONFIG_REPO:-}"

  [[ -n "${repo}" ]] || die "CM_CONFIG_REPO is unset; the backend has no source repo to serve"
  [[ -d "${repo}" ]] || die "config-repo mount ${repo} does not exist (is the volume mounted?)"

  if [[ ! -e "${repo}/.git" ]]; then
    # 空與非空是兩種不同的狀況，而這裡曾經把它們混為一談：註解寫著「空目錄」，
    # 條件卻只問 .git 在不在，於是一個裝滿檔案的目錄被初始化，還被宣告成空的
    # （#69）。
    #
    # 這件事之所以要緊，是因為 io/git.record 開頭是 `git add -A`。在別人的檔案
    # 上面初始化，會把那些檔案全部掃進第一筆 commit，而一個打錯的掛載路徑看起來
    # 就跟啟動成功一模一樣。
    if [[ -n "$(ls -A "${repo}")" ]]; then
      die "config-repo mount ${repo} is not empty but is not under version control;" \
        "initialise it deliberately (git init) or check the mount path"
    fi

    # 空目錄就是首次啟動的情況：volume 存在，還沒有東西初始化過它。掛到另一個
    # 空目錄的錯誤設定，在這裡分辨不出來——所以印出解析後的絕對路徑，讓讀到的人
    # 自己認出這是不是他要的位置，而不是假裝這個差別在這裡就知道得了。
    printf 'entrypoint: initialising empty config-repo at %s\n' "$(cd "${repo}" && pwd)"
    git init --quiet --initial-branch=main "${repo}" || die "git init failed at ${repo}"
    seed_config_list "${repo}"
    # 刻意往下掉到 check_config_list。種子複製了 core/models 宣告的形狀，而這裡
    # 正是讓那份複製不會靜默出錯的機制：一份已經不再合法的種子會在這裡被擋下，
    # 就在寫出它的同一次啟動裡，而不是在之後某個請求。
  else
    git -C "${repo}" rev-parse --git-dir >/dev/null 2>&1 \
      || die "config-repo mount ${repo} exists but is not a git repository"
  fi

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

main() {
  case "${CM_ROLE:-backend}" in
    backend) check_backend_preconditions ;;
    frontend) ;;
    *) die "unknown CM_ROLE '${CM_ROLE}'; expected 'backend' or 'frontend'" ;;
  esac

  # 交棒。這一行之後不得再有任何東西。
  exec "$@"
}

main "$@"
