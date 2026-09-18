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

check_allowed_roots_seedable() {
  # 種檔前先驗每個根，種子的驗證範圍要與 Python 端 load() 對齊——否則 shell 種出一份
  # core 隨後會拒絕的 allowed-roots.toml，讓 preflight 以「白名單設定檔無法解析」死掉、把
  # 矛頭指向這份機器自動生成的檔，而真正該修的是 CM_ALLOWED_ROOTS（§0.4 的指錯方向）。
  # 更糟：毒檔在 preflight 之前就已提交，重啟時 seed-if-missing 跳過重種 → 每次啟動再死一次
  # → 崩潰迴圈。在這裡大聲失敗、指名是哪個根與哪個變數。
  #
  # 三類 core 會拒的形狀（對齊 core.allowed_roots.check_prefix 與 _check_integrity）：
  #   1. 含 " 或反斜線 —— 手刻 printf 寫不成合法 TOML basic string
  #   2. 含 .. 路徑段 —— check_prefix 拒（InvalidPrefix，會逃逸到預期目錄外）
  #   3. 重複前綴 —— _check_integrity 拒（DuplicatePrefix）
  local root trimmed
  declare -A seen
  while IFS= read -r root; do
    trimmed="${root#"${root%%[![:space:]]*}"}"
    trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
    [[ -n "${trimmed}" ]] || continue
    case "${trimmed}" in
      *'"'* | *'\'*)
        die "CM_ALLOWED_ROOTS 的根「${trimmed}」含 \" 或反斜線，寫不成合法的白名單設定檔。" \
          "下一步：從 CM_ALLOWED_ROOTS 移除或修正這個路徑——config 目錄的路徑不該含這些字元"
        ;;
    esac
    # 前後補 / 讓 .. 位在開頭、結尾或中間都被 */../* 命中。
    case "/${trimmed}/" in
      */../*)
        die "CM_ALLOWED_ROOTS 的根「${trimmed}」含 .. 路徑段，core 會拒（會逃逸到預期目錄外）、" \
          "寫成的白名單設定檔隨後於 preflight 無法解析。" \
          "下一步：從 CM_ALLOWED_ROOTS 移除 .. 、改成不含 .. 的正規絕對路徑"
        ;;
    esac
    if [[ -n "${seen[${trimmed}]:-}" ]]; then
      die "CM_ALLOWED_ROOTS 有重複的根「${trimmed}」，core 會拒成重複前綴（DuplicatePrefix）、" \
        "寫成的白名單設定檔隨後於 preflight 無法解析。" \
        "下一步：從 CM_ALLOWED_ROOTS 移除重複的那一筆"
    fi
    seen[${trimmed}]=1
  done < <(printf '%s\n' "${CM_ALLOWED_ROOTS:-}" | tr ',' '\n')
}

_emit_allowed_roots() {
  # 把 CM_ALLOWED_ROOTS 種成一份白名單設定檔（§7.9, #202）：逗號分隔、去前後空白，
  # 與 check_allowed_roots_visible／api.cli._allowed_roots 同一種切法。每個根記下部署來源
  # 與種下的時間；CM_ALLOWED_ROOTS 空的話就一個根都不放（什麼都不放行，落向安全，不變式 4）。
  # 前綴的特殊字元已由 check_allowed_roots_seedable 先擋下，這裡只負責輸出。
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

  # 以檔為準（#202）：工作區已有一份就不動它——之後的增減都經由介面寫這份檔。
  [[ -f "${file}" ]] && return 0

  # 工作區沒有，但它是不是已被追蹤（HEAD 有）？已追蹤卻工作區缺 = 不是首次開機，而是
  # 遺失（volume 損毀、還原不全）。這時從 CM_ALLOWED_ROOTS 重種會靜默用部署 env 蓋掉
  # 之後經介面增減的白名單（#232 發現6）——大聲失敗、指路從 git 還原，不從 env 重生，對照
  # config-list 遺失時 preflight 直接死。首次開機（含升級既有部署）HEAD 沒有這份檔，cat-file
  # 非零 → 落到下面照常種子。
  if git -C "${repo}" cat-file -e "HEAD:allowed-roots.toml" 2>/dev/null; then
    die "白名單設定檔 ${file} 已在版控裡，但工作區的這份不見了——這不是首次開機。" \
      "從 CM_ALLOWED_ROOTS 重種會靜默蓋掉之後經介面增減的白名單。" \
      "下一步：從 git 還原它（git -C '${repo}' checkout -- allowed-roots.toml），不要靠重種"
  fi

  check_allowed_roots_seedable

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

  # git ≥2.35.2（CVE-2022-24765）在 repo 探索時要求 worktree／.git 的擁有者 == 執行身分，
  # 不符就 `fatal: detected dubious ownership` 並拒絕**所有**操作（連 rev-parse 唯讀都拒）。
  # 服務以非 root（uid 1000）跑，而 config-repo 可能由別的 uid 擁有——從備份還原、掛既有
  # 簽出、或以 root 建好後改回服務身分執行。那是一份合法 repo，卻會讓下面的 rev-parse 非零
  # 退出、落入「不是 git repo」的 die，把矛頭指向重 init（真因是擁有權，#232 發現1）。
  # 明示信任這一個 repo：走 GIT_CONFIG_*（而非全域設定，比照 script/test.sh／acceptance.sh
  # ——不依賴可寫的 HOME），並 export 讓稍後 exec 出去的服務（io/git.py 也對同一 repo 跑
  # git）一併繼承。範圍限這一個 repo、不用 '*' 全信任——entrypoint 只碰 CM_CONFIG_REPO 這份。
  export GIT_CONFIG_COUNT=1
  export GIT_CONFIG_KEY_0=safe.directory
  export GIT_CONFIG_VALUE_0="${repo}"

  if [[ ! -e "${repo}/.git" ]]; then
    # 空與非空是兩種不同的狀況，而這裡曾經把它們混為一談：註解寫著「空目錄」，
    # 條件卻只問 .git 在不在，於是一個裝滿檔案的目錄被初始化，還被宣告成空的
    # （#69）。
    #
    # 這件事之所以要緊，是因為在別人的檔案上面 git init，等於把一個不屬於我們的
    # 目錄當成 config-repo 收下：一個打錯的掛載路徑看起來就跟啟動成功一模一樣，
    # 而那個目錄從此被這個系統當成唯一真實來源。
    # ls 讀不動時（EACCES：NFS root_squash，或 drop 掉 CAP_DAC_OVERRIDE／DAC_READ_SEARCH
    # 而目錄拒讀）stdout 是空的——命令替換的失敗在 set -e 下不會中止外層，[[ ]] 只看被捕捉
    # 的 stdout，於是「讀不到」被當成「空」，一個裝滿檔案的目錄被誤判成空首啟、被 git init
    # 收下（正是 #69 要防的形狀，#232 發現2）。先接住 ls 的退出碼：讀不進來就大聲失敗、指向
    # 權限，不當成空。單獨一行宣告再賦值——local 會吃掉右側的退出碼，寫在同一行就檢查不到。
    local listing
    if ! listing="$(ls -A "${repo}" 2>/dev/null)"; then
      die "config-repo 的掛載點 ${repo} 列不出內容（多半是權限——讀不進來）。" \
        "空與非空在這裡分不出來，不能當成空目錄逕自 git init（那會把別人的目錄收成 config-repo，#69）。" \
        "下一步：確認執行這個容器的使用者對 ${repo} 有讀取權（NFS root_squash、或 drop 掉" \
        "CAP_DAC_OVERRIDE／DAC_READ_SEARCH 都會擋讀），或改 CM_CONFIG_REPO 指到讀得到的掛載"
    fi
    if [[ -n "${listing}" ]]; then
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

  seed_allowed_roots "${repo}"
  check_config_list "${repo}"
  # 可見性驗的是「生效白名單」＝檔（#202），故排在種子與 preflight 之後：種子確保檔在、
  # preflight 確保檔可解析，這裡才逐條讀它列出的根驗可見（#232 發現5）。
  check_allowed_roots_visible "${repo}"
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
    # preflight 若被 OOM/信號中止（exit 137、無輸出），${output} 會是空的——比照檔內其他每個
    # die 站點補後備，否則只印「entrypoint: 」（無原因），違反 die() 自身承諾與 §0.4 三要素。
    die "${output:-preflight 檢查未輸出任何訊息就失敗了（可能被 OOM 或信號中止）；" \
      "下一步：檢查容器的記憶體上限，並手動檢視 ${repo}/config-list.toml 與 ${repo}/allowed-roots.toml}"
  fi
}

# 掛載是白名單的上界（#146）：白名單允許的每個根目錄都必須在容器裡看得到，否則寫出會
# 以「目標目錄無法寫入」失敗——訊息指向權限，真正的原因是掛載沒把它帶進來，是一則指錯
# 方向的訊息（§0.4 三要素要防的）。在這裡逐條驗、看不到就大聲失敗並指名，而不是等到第
# 一次寫出才在執行期發現。
#
# 驗的是**生效白名單＝檔**（allowed-roots.toml，#202），不是 CM_ALLOWED_ROOTS：首啟後
# 兩者會漂移——經介面增減只寫檔，驗 env 會驗錯對象（env 裡失效的根被驗、檔裡生效的根不被
# 驗，#232 發現5）。shell 不自己 parse TOML（會與 core 漂移，比照 check_config_list）：交給
# io.allowed_roots 印出 realpath 正規化後的根（與 browse／onboard 取用一致），逐一驗 isdir。
# 檔的根為空 = 什麼都不放行（安全預設，不變式 4），沒有根要驗。
check_allowed_roots_visible() {
  local repo="$1"
  local prefixes root

  if ! prefixes="$(python -m config_manager.io.allowed_roots "${repo}" 2>&1)"; then
    die "讀不出生效白名單設定檔的根：" \
      "${prefixes:-io.allowed_roots 沒有給出任何錯誤輸出}；" \
      "下一步：檢查 ${repo}/allowed-roots.toml 的權限與內容"
  fi
  [[ -n "${prefixes}" ]] || return 0

  while IFS= read -r root; do
    [[ -n "${root}" ]] || continue
    # `[[ -d ]]` 失敗有兩種：路徑沒掛進來（ENOENT），或某層上層目錄對服務執行身分沒有
    # traverse（+x）權限（EACCES）——bash 把兩者都收成 false，分不出來。所以訊息把兩個
    # 可能的成因都講出來，而不是只說「去掛載」，否則掛好了卻卡在權限的人會被指錯方向
    # （§0.4；io/source._blocking_parent 在讀取時能分得更細）。
    [[ -d "${root}" ]] || die "白名單允許的路徑在容器裡看不到：${root}。" \
      "下一步：把它掛進容器（compose 的 volumes）、給它某層上層目錄對服務執行身分加上" \
      "traverse（+x）權限、或經 API／CLI 從白名單移除該根；白名單只在掛載掛得到的範圍內有意義（#146）"
  done <<< "${prefixes}"
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
