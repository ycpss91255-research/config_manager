#!/usr/bin/env bash
#
# 三個測試軸（§3.6.1）分開放，因為它們回答的是不同的問題；把它們揉成一張「四種
# 分類」的清單，正是這個結構要避免的錯誤：
#
#   lint   靜態分析——根本不是動態測試層級
#   level  unit / integration / system / acceptance——範圍
#   type   smoke / e2e / regression——目的，套用在某個層級上
#
# 預設跑 lint 加上全部層級與覆蓋率。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly TEST_IMAGE="config_manager-test-tools:local"
readonly TEST_DOCKERFILE="docker/Dockerfile.test-tools"
# GitHub Actions 快取裡放這份映像的層所用的範圍名。只有這份映像用它。
readonly TEST_IMAGE_CACHE_SCOPE="config_manager-test-tools"

usage() {
  cat <<'USAGE'
用法：script/test.sh [--level <name>] [--lint [<tool>]] [--file <path>] [--filter <regex>]
                     [--part fast | api <i>/<n> | web <i>/<n> | bats | gate]

  （不帶參數）        lint ＋ 全部層級 ＋ 覆蓋率
  --level <name>      unit | integration | system | acceptance
                      （同時跑該層級的 pytest 與 bats 規格）
  --lint [<tool>]     全部 linter，或指定其中一項：
                      ruff | mypy | pylint | shellcheck | hadolint | actionlint | commit | adr | paths
                      | portability | messages | exceptions | audit | derived | interfaces
  --file <path>       單一規格檔
  --filter <regex>    符合這個樣式的規格
  --part <段>         把不帶參數的那一次執行拆成幾段分開跑，CI 用它平行：
                      fast         test_api.py 與 test_web.py 以外的全部 pytest 規格，帶覆蓋率
                      api <i>/<n>  test_api.py 依收集順序輪流分成 n 份，跑第 i 份，帶覆蓋率
                      web <i>/<n>  test_web.py 依收集順序輪流分成 n 份，跑第 i 份，帶覆蓋率
                      bats         全部層級的 bats 規格
                      gate         確認每一條 pytest 規格恰好被一段跑過，合併各段的覆蓋率，
                                   再交給 script/coverage_gate.sh
                      fast、api、web 把結果寫在 ci-parts/<段名>/，gate 讀那裡。
                      lint 那一段就是 --lint。

在 docker/Dockerfile.test-tools 裡執行，那份映像帶齊每一支檢查工具。
主機不是這個專案的證據：它的 Python、它的 pytest、它缺席的 linter，
三者都在這裡給過錯誤的答案。

這次執行**沒有真的跑到**的東西列在最後：自己跳過的規格，以及一條規格都沒有的
層級。兩者都不算失敗（它們不是壞的），但兩者都不准安靜。

  CM_TEST_LOCAL=1     改在這台主機上跑。缺什麼會被指名並跳過——大聲的跳過，
                      仍然不是一項檢查。
  CM_TEST_IMAGE_CACHE=gha        建映像時從 GitHub Actions 的快取取層（CI 用）
  CM_TEST_IMAGE_CACHE=gha-write  同上，並把建好的層寫回快取
USAGE
}

# 把這支腳本轉進裝了工具的映像裡重跑，除非已經在裡面了。一個環境、兩個呼叫者：
# `just test` 與 CI 走同一條路徑，所以一項檢查不可能在一邊過、在另一邊掛。
dispatch_to_container() {
  if ! command -v docker >/dev/null 2>&1; then
    printf 'test.sh: docker 沒有安裝，所以檢查沒有它們的映像可以跑\n' >&2
    printf 'test.sh: 下一步：安裝 docker，或設 CM_TEST_LOCAL=1 以這台主機現有的工具執行\n' >&2
    exit 1
  fi

  local -a _build
  # CI 的每個 job 都是一台全新的機器，本機那層快取不存在，於是每個 job 都從頭建一次
  # ——拆成平行的 job 之後，這筆時間是每個 job 各付一次（#358）。GitHub Actions 的
  # 快取讓它們共用建好的層。只換掉「層從哪裡來」：建的仍是同一份 Dockerfile、同一個
  # tag，之後的 docker run 一個字都不變，所以本機與 CI 跑的仍是同一份映像。
  #
  # 只有一個 job 寫回快取（gha-write），其餘只讀：每個 job 都寫的話，映像一改，十幾個
  # job 會同時上傳同一批層。不設時與這段之前完全相同——本機沒有那個快取可以讀。
  case "${CM_TEST_IMAGE_CACHE:-}" in
    "")
      _build=(docker build --quiet)
      ;;
    gha | gha-write)
      # --load：buildx 用的建置器把結果留在它自己那裡，不放進 docker 的映像清單；
      # 少了這個，下面的 docker run 找不到這個 tag。
      _build=(docker buildx build --load --quiet
        --cache-from "type=gha,scope=${TEST_IMAGE_CACHE_SCOPE}")
      if [[ "${CM_TEST_IMAGE_CACHE}" == "gha-write" ]]; then
        _build+=(--cache-to "type=gha,mode=max,scope=${TEST_IMAGE_CACHE_SCOPE}")
      fi
      ;;
    *)
      printf 'test.sh: 不認得 CM_TEST_IMAGE_CACHE=%s，映像沒有建，檢查一項都沒跑\n' "${CM_TEST_IMAGE_CACHE}" >&2
      printf 'test.sh: 下一步：不設它，或設成 gha（只讀快取）／gha-write（讀並寫回）\n' >&2
      exit 2
      ;;
  esac
  _build+=(-f "${REPO_ROOT}/${TEST_DOCKERFILE}" -t "${TEST_IMAGE}")

  # 有快取時很便宜；只有 Dockerfile 或釘住的 requirements 真的變了，層才會重建。
  # 進度往 stdout，不往 fd 2。**fd 2 是錯誤的出口**，而建置進度不是錯誤——寫到
  # 那裡的話，它會落在 §0.4 三要素的管轄內，而「下一步」對一行進度訊息沒有意義
  # （#108 實測）。
  printf 'test.sh: 建置 %s\n' "${TEST_IMAGE}"
  # 映像預設用台灣的 Debian 鏡像，因為從開發這個 repo 的網路連不到
  # deb.debian.org。在別的地方答案不同的話，設 CM_APT_MIRROR，不要改檔案。
  [[ -n "${CM_APT_MIRROR:-}" ]] && _build+=(--build-arg "APT_MIRROR=${CM_APT_MIRROR}")
  "${_build[@]}" "${REPO_ROOT}" >/dev/null

  # repo 用掛載而不是複製進去，這樣檢查失敗時指出的路徑在主機上真的存在，而且改
  # 一行不必重建映像。掛載是雙向的，所以 --user 才重要：映像自己的使用者是 root，
  # 而 root 程序寫進 bind mount 會在主機上留下一堆 root 所有的檔案。這在這裡不是
  # 假設——它賠掉了一次還沒提交的修改，並留下 47 個 root 所有的路徑，其中包含
  # .git 裡面的一個檔案。
  #
  # HOME 被導向他處，是因為呼叫端的 uid 在這個映像裡沒有家目錄；git.safe.directory
  # 走 GIT_CONFIG_* 而不是全域設定，也是同一個原因：沒有可寫的 HOME，就沒有地方
  # 放那份設定。
  local -a _run=(
    docker run --rm
    --user "$(id -u):$(id -g)"
    --env HOME=/tmp
    --env GIT_CONFIG_COUNT=1
    --env GIT_CONFIG_KEY_0=safe.directory
    --env GIT_CONFIG_VALUE_0='*'
    --volume "${REPO_ROOT}:/repo"
  )

  # git worktree 裡，REPO_ROOT/.git 是一個**檔案**，內容是主 repo 那個 git 目錄的
  # 主機絕對路徑。只掛 REPO_ROOT 的話那條路徑在容器裡不存在，git 就不認得這是一份
  # 簽出，於是 lint_commit 與 lint_paths 雙雙以「這裡不是 git repo」失敗——兩支需要
  # git 的檢查沒跑到，而有跳過的執行不算通過（#103）。
  #
  # 掛載點必須是**同一個絕對路徑**：容器要解開的就是那個檔案裡寫死的那條路徑，
  # 掛在別處解不開。讀寫而非唯讀，因為 git 會寫 index 與 lock；--user 已經是呼叫端
  # 的 uid，所以寫進去的東西在主機上的所有權是對的。
  #
  # 一般簽出的 --git-common-dir 就是 REPO_ROOT/.git，已經在上面那個掛載的範圍內，
  # 再掛一次是多餘的——所以只有它落在 REPO_ROOT 之外時才加。不是 git repo、或主機
  # 上沒有 git 時，這裡什麼都不加：那與修這件事之前的行為相同。
  local _git_common=""
  _git_common="$(git -C "${REPO_ROOT}" rev-parse --git-common-dir 2>/dev/null)" || _git_common=""
  [[ -n "${_git_common}" && "${_git_common}" != /* ]] && _git_common="${REPO_ROOT}/${_git_common}"
  case "${_git_common}" in
    ""|"${REPO_ROOT}"/*) ;;
    *) _run+=(--volume "${_git_common}:${_git_common}") ;;
  esac

  _run+=(--workdir /repo "${TEST_IMAGE}" ./script/test.sh "$@")
  exec "${_run[@]}"
}

# 每個 linter 需要 PATH 上有什麼，以及怎麼取得。只有一張表，這樣底下的事前盤點與
# 每項檢查的守衛，就不可能對「這次執行需要哪些工具」有不同意見。
_tool_install_hint() {
  case "$1" in
    ruff|mypy|pylint|pytest) printf 'pip install -r config/pip/requirements-dev.txt' ;;
    python3) printf 'apt-get install python3' ;;
    shellcheck) printf 'apt-get install shellcheck' ;;
    bats) printf 'apt-get install bats' ;;
    hadolint) printf 'https://github.com/hadolint/hadolint/releases' ;;
    just) printf 'https://github.com/casey/just/releases' ;;
    actionlint) printf 'https://github.com/rhysd/actionlint/releases' ;;
    *) printf 'see docker/Dockerfile.test-tools' ;;
  esac
}

# 把這次執行缺的**每一個**工具都回報出來，然後停下——不是只報第一個。碰到第一個
# 就中止，會把「盤點主機」變成裝一個、重跑、再撞下一個；而且那樣的執行從來不會說出
# 它沒有檢查什麼，而那正是整個守衛要讓人看見的東西。
#
# 只有 CM_TEST_LOCAL=1 才走得到這裡：正常執行是在映像裡，那些工具依建置方式必然齊全。
survey_tools() {
  local -a needed=("$@") missing=()
  local t
  for t in "${needed[@]}"; do
    command -v "${t}" >/dev/null 2>&1 || missing+=("${t}")
  done
  (( ${#missing[@]} == 0 )) && return 0

  printf 'test.sh: 這次執行需要 %d 支檢查工具，這台主機少了 %d 支。下一步：照下面每一行後面的方式取得\n' \
    "${#needed[@]}" "${#missing[@]}" >&2
  for t in "${missing[@]}"; do
    printf '  %-11s %s\n' "${t}" "$(_tool_install_hint "${t}")" >&2
  done
  if [[ "${CM_LINT_ALLOW_MISSING:-}" == "1" ]]; then
    printf 'test.sh: CM_LINT_ALLOW_MISSING=1——繼續往下跑，而上面那幾項沒有執行\n' >&2
    printf 'test.sh: 有跳過的執行不算通過。下一步：拿掉 CM_TEST_LOCAL，改用帶齊工具的映像\n' >&2
    return 0
  fi
  printf 'test.sh: 下一步：把它們裝起來，或拿掉 CM_TEST_LOCAL 改用帶齊工具的映像，\n' >&2
  printf 'test.sh: 也可以設 CM_LINT_ALLOW_MISSING=1 跑其餘的，並被告知哪幾項沒跑\n' >&2
  exit 1
}

# 工具不在的 linter 絕不可以安靜地通過。「lint 過了」必須等於「lint 跑了」；其餘
# 都是不變式 2 禁止的靜默成功，而且已經讓我們付過代價——hadolint 在本機每次執行都被
# 跳過，CI 卻一直在它上面失敗，於是一個 Dockerfile 的問題連續六次推送都沒人注意到。
# 走到這裡時 survey_tools 已經回報並決定過了；這裡是每項檢查的閘門，擋住對一個不存在
# 的工具發出呼叫。
require_tool() {
  command -v "$1" >/dev/null 2>&1
}

run_lint() {
  local tool="${1:-all}"
  cd "${REPO_ROOT}"

  case "${tool}" in
    all) survey_tools ruff mypy pylint shellcheck hadolint actionlint python3 ;;
    ruff|mypy|pylint|shellcheck|hadolint|actionlint) survey_tools "${tool}" ;;
    # lint_messages 讀 Python 的 AST，所以它需要的工具是直譯器本身。shell 那一半
    # 另外需要 bash（用 `bash -n` 確認檔案真的解析得了），而這支腳本自己就是 bash。
    messages) survey_tools python3 ;;
    # lint_exceptions 同樣走 Python 的 AST（§0.4 第 1 條，#112）。
    exceptions) survey_tools python3 ;;
  esac

  case "${tool}" in
    ruff|all) require_tool ruff && ruff check src test ;;&
    mypy|all) require_tool mypy && mypy --strict src/config_manager ;;&
    pylint|all) require_tool pylint && pylint src ;;&
    shellcheck|all)
      if require_tool shellcheck; then
        local -a _sh=()
        mapfile -t _sh < <(find script -name '*.sh' -type f | sort)
        shellcheck --severity=warning "${_sh[@]}"
      fi
      ;;&
    hadolint|all)
      require_tool hadolint \
        && hadolint --config .hadolint.yaml Dockerfile
      ;;&
    actionlint|all)
      # workflow 的運算式不是 YAML，沒有任何 YAML parser 會檢查它們。${{ }} 裡面
      # 一個雙引號字串字面值是合法的 YAML、卻是不合法的運算式——GitHub 會拒收整個
      # 檔案，一個 job 都不跑，並回報成一次沒有 job 可以點開的執行失敗。現在在這裡
      # 抓，而不是推上去再讀後果。
      require_tool actionlint \
        && actionlint .github/workflows/*.yaml
      ;;&
    commit|all) ./script/lint_commit.sh ;;&
    adr|all) ./script/lint_adr.sh ;;&
    paths|all) ./script/lint_paths.sh ;;&
    portability|all) ./script/lint_portability.sh ;;&
    messages|all)
      # 不帶參數：標的是 lint_messages.sh 自己的預設，也就是 src/config_manager
      # **加上 script/**（#133）。先前只有前者，於是 32 支腳本的執行期輸出完全不在
      # 三要素的管轄內，而 review 只能逐則人工判讀——§0.4 說那等同不存在。
      require_tool python3 \
        && ./script/lint_messages.sh
      ;;&
    exceptions|all)
      # §0.4 第 1 條：捕捉具名例外之後只 pass 或只 log.debug（含 contextlib.suppress）。
      # ruff／pylint 擋 bare 與 Exception，這支補「具名例外的吞」那個缺口（#112）。走 AST，需要 python3。
      require_tool python3 \
        && ./script/lint_exceptions.sh
      ;;&
    audit|all) ./script/lint_coverage_audit.sh ;;&
    derived|all) ./script/lint_derived.sh ;;&
    # 規則 A（order）：新測試介面標題不得與 test/ 底下的檔案同一個 commit。只需要
    # git，所以進得了這裡；規則 B 需要 token，是 CI 的獨立 job（#144）。
    interfaces|all) ./script/lint_test_interfaces.sh order ;;&
    ruff|mypy|pylint|shellcheck|hadolint|actionlint|commit|adr|paths|portability|messages|exceptions|audit|derived|interfaces|all)
      return 0
      ;;
    *) printf 'test.sh: 不認得的 linter %s。下一步：見 --help 列出的那幾項\n' "${tool}" >&2; return 2 ;;
  esac
}

# 動態層級。四個，與設計 §3.6.1 的軸 2 逐字相同。smoke 不在其中：它是軸 3 的
# **型別**，寫在檔名裡，而它的執行者是 `docker build --target runtime-test`。
readonly LEVELS="unit integration system acceptance"

# 本次執行**沒有真的跑到**的東西，累積在這裡，結束前一次列出。
#
# 兩種來源，同一個形狀：一組每次都跳過的規格，以及一個一條規格都沒有的層級。
# 兩者在輸出上與「全綠」的差別都只有幾行字，而這個 repo 已經抓到八次
# 「看起來在檢查、其實沒在檢查」——每一次都是這個形狀。它們不算失敗（那些規格
# 在這個容器裡確實跑不了），但絕不可以安靜。降級要大聲：與 CM_LINT_ALLOW_MISSING
# 同一個先例。
_NOT_RUN=""

_note_not_run() {
  [[ -n "${_NOT_RUN}" ]] || _NOT_RUN="$(mktemp)"
  printf '%s\n' "$1" >>"${_NOT_RUN}"
}

report_not_run() {
  if [[ -z "${_NOT_RUN}" ]]; then
    return 0
  fi

  local -a lines=()
  mapfile -t lines <"${_NOT_RUN}"
  rm -f "${_NOT_RUN}"
  _NOT_RUN=""

  if (( ${#lines[@]} == 0 )); then
    return 0
  fi

  printf '\ntest.sh: 這次執行有 %d 項規格或層級**沒有真的跑到**：\n' "${#lines[@]}" >&2
  printf '  %s\n' "${lines[@]}" >&2
  printf 'test.sh: 一條每次都跳過的規格不是檢查，一個沒有規格的層級不是被涵蓋的層級。\n' >&2
  printf 'test.sh: 下一步：上面每一行都要在 doc/TEST-PLAN.md 有寫下來的理由，否則就是缺口\n' >&2
}

# shell 用 bats 測，Python 用 pytest 測；一個層級有哪種規格就跑哪種。bats 不在時
# 交給 survey_tools 大聲回報，不靜默跳過——「沒有東西會執行的規格」正是這段接線
# 要消滅的缺口（不變式 2）。
#
# 輸出格式定死成 TAP，不隨「stdout 是不是 terminal」而變。bats 兩種格式標記 skip
# 的寫法不同，而被解析的那份輸出若在兩個環境裡長得不一樣，這裡數出來的數字就會
# 在其中一個環境裡是錯的——那比不數還糟。
run_bats() {
  local label="$1"; shift
  local tap status=0
  tap="$(mktemp)"

  bats --formatter tap "$@" | tee "${tap}" || status=$?

  local line
  while IFS= read -r line; do
    _note_not_run "${label}: ${line}"
  done < <(sed -n 's/^ok [0-9][0-9]* \(.*\) # skip[ ]*\(.*\)$/\1 -- skipped: \2/p' "${tap}")

  rm -f "${tap}"
  return "${status}"
}

run_bats_level() {
  # 兩行不是風格：local 的參數會在它執行前就全部展開，寫成一行的話 ${level}
  # 在展開當下還沒被賦值，set -u 會直接判定 unbound。
  local level="$1"
  local dir="${REPO_ROOT}/test/bats/${level}"
  [[ -d "${dir}" ]] || return 0

  local -a specs=()
  mapfile -t specs < <(find "${dir}" -name '*.bats' -type f | sort)
  (( ${#specs[@]} == 0 )) && return 0

  survey_tools bats
  # unit 層有一則規格直接執行 justfile 的接線（#108），所以那一層還多需要 just。
  # 不盤點的話，`just` 缺席時那則規格以 127 失敗——一個看起來像「接線壞了」的紅燈，
  # 而實際上是「工具不在」。兩者的下一步完全不同。
  if [[ "${level}" == "unit" ]]; then
    survey_tools just
  fi
  require_tool bats || return 0
  run_bats "test/bats/${level}" "${specs[@]}"
}

run_bats_levels() {
  local level
  for level in ${LEVELS}; do
    run_bats_level "${level}"
  done
}

# 一個層級的 pytest 規格。空層級交給 _note_not_run，不交給 pytest：pytest 對
# 「一條都沒收集到」回 5，而 5 在這裡是個假的紅燈——那一層是空的，不是壞的，
# 而假紅燈與真紅燈混在一起的下場，是兩種都不再被相信。
run_pytest_level() {
  local level="$1"
  local dir="${REPO_ROOT}/test/pytest/${level}"

  local -a specs=()
  if [[ -d "${dir}" ]]; then
    mapfile -t specs < <(find "${dir}" -name 'test_*.py' -type f | sort)
  fi
  if (( ${#specs[@]} == 0 )); then
    _note_not_run "test/pytest/${level}/ —— 這一層一條規格都沒有"
    return 0
  fi

  pytest "${dir}"
}

# 預設執行把 test/pytest 一次收集完（覆蓋率要一個總數，分四次跑就得不到），
# 於是空層級在那一次裡完全看不見。這裡把它們單獨點出來。
note_levels_without_specs() {
  local level dir
  local -a specs
  for level in ${LEVELS}; do
    dir="${REPO_ROOT}/test/pytest/${level}"
    specs=()
    if [[ -d "${dir}" ]]; then
      mapfile -t specs < <(find "${dir}" -name 'test_*.py' -type f | sort)
    fi
    if (( ${#specs[@]} == 0 )); then
      _note_not_run "test/pytest/${level}/ —— 這一層一條規格都沒有"
    fi
  done
}

# ── 分段執行（#358）────────────────────────────────────────────────────────
#
# 不帶參數的那一次執行從頭跑到尾，而其中 test_web.py 一支就佔了四分之三的時間，
# test_api.py 再佔掉兩、三分鐘。CI 把它拆成幾段平行跑，那兩支各自再分成幾份；
# **拆開之後要對得回原本那一次**，這一段的每一個決定都為了這件事：
#
#   - 每一段 pytest 跑的，正是它寫進 ids.txt 的那幾條。那份檔案就是傳給 pytest 的
#     參數（`@檔案`），不是另外算一次的清單——記下來的與真的跑的是同一份東西。
#   - gate 自己重新收集一次全部規格，要求每一條**恰好**出現在一段裡：漏掉的、重複的、
#     收集裡沒有的都擋。份數寫錯、某一段整個沒跑，都會在那裡變成「有規格沒被跑到」。
#   - 覆蓋率合起來才判：test_web.py 把 uvicorn 起在同一個行程裡，所以它對 api／io／core
#     也有貢獻，分開判會讓每一段都看起來不夠。判的仍是 coverage_gate.sh，一個字不改。
readonly PARTS_DIR="ci-parts"
readonly API_SPEC="test/pytest/system/test_api.py"
readonly WEB_SPEC="test/pytest/system/test_web.py"

# 用法錯誤：說哪裡錯，連同用法一起送到 fd 2，以 2 結束。
part_usage_error() {
  printf 'test.sh: %s。下一步：照下面的用法改 --part 的參數\n' "$1" >&2
  usage >&2
  exit 2
}

# 把收集到的節點 ID 照收集順序寫進 $1，一行一條。只收集，不執行。
#
# `--collect-only -q` 先印節點 ID，接著一個空行，再來才是摘要與可能的警告。取第一個
# 空行之前的那一段，並要求每一行都長得像節點 ID：讀不懂的輸出要大聲失敗——把一行
# 摘要算成一條規格，或少算一條，gate 的「恰好一次」就不再是真的。
collect_ids() {
  local dest="$1"; shift
  local out
  out="$(mktemp)"
  if ! pytest --collect-only -q "$@" >"${out}" 2>&1; then
    cat "${out}" >&2
    printf 'test.sh: 收集 %s 的規格失敗，上面是 pytest 的輸出。下一步：先修好收集錯誤再重跑\n' "$*" >&2
    rm -f "${out}"
    exit 1
  fi
  awk 'NF == 0 { exit } { print }' "${out}" >"${dest}"
  rm -f "${out}"

  if [[ ! -s "${dest}" ]] || grep -v -q -F '::' "${dest}"; then
    printf 'test.sh: 讀不懂 pytest --collect-only -q 對 %s 的輸出，規格清單沒有建起來\n' "$*" >&2
    printf 'test.sh: 下一步：確認 pytest 的輸出格式（第一個空行之前每行一個節點 ID）是否改了\n' >&2
    exit 1
  fi
}

# 一段 pytest：跑 $2 裡的那幾條，結果寫進 ci-parts/$1/。
#
# 覆蓋率報表在這裡不印：一段的數字只是一部分，印出來只會被讀成整體。合起來的那一份
# 由 gate 印。
run_pytest_part() {
  local name="$1" ids="$2"
  local dir="${REPO_ROOT}/${PARTS_DIR}/${name}"

  # 空的清單不能交給 pytest：沒有參數的 pytest 會照 testpaths 跑**全部**規格，而那
  # 看起來就像這一段很盡責。
  if [[ ! -s "${ids}" ]]; then
    printf 'test.sh: %s 這一段一條規格都沒有分到，沒有東西可跑\n' "${name}" >&2
    printf 'test.sh: 下一步：份數不要多於 %s 或 %s 的規格數\n' "${API_SPEC}" "${WEB_SPEC}" >&2
    exit 2
  fi

  rm -rf "${dir}"
  mkdir -p "${dir}"
  cp "${ids}" "${dir}/ids.txt"
  # 上一次留下的資料不能被當成這一次的：.coverage 會被 pytest-cov 覆寫，但
  # .coverage-web.json 只有在這一段真的開過頁面時才會被寫。
  rm -f "${REPO_ROOT}/.coverage" "${REPO_ROOT}/.coverage-web.json"

  pytest "@${dir}/ids.txt" --cov=src/config_manager --cov-report=

  # 名字不以點開頭：CI 上傳結果時預設略過隱藏檔。
  cp "${REPO_ROOT}/.coverage" "${dir}/python.coverage"
  if [[ -f "${REPO_ROOT}/.coverage-web.json" ]]; then
    cp "${REPO_ROOT}/.coverage-web.json" "${dir}/web.json"
  fi
}

# test_api.py 與 test_web.py 以外的全部 pytest 規格。
run_part_fast() {
  note_levels_without_specs
  local all ids
  all="$(mktemp)"
  ids="$(mktemp)"
  collect_ids "${all}" test/pytest
  grep -v -F -e "${API_SPEC}::" -e "${WEB_SPEC}::" "${all}" >"${ids}" || true
  run_pytest_part fast "${ids}"
  rm -f "${all}" "${ids}"
}

# 一支規格檔（$1 是組名 api 或 web，$2 是檔案）的第 i 份（共 n 份）。依收集順序輪流發：
# 第 1、1+n、1+2n… 條是第 1 份。輪流而不是切成連續的 n 段：相鄰的規格多半測同一個
# 端點或畫面、花差不多的時間，輪流發讓每一份分到的長短比較平均。
run_part_share() {
  local group="$1" spec="$2" share="$3"
  [[ "${share}" =~ ^([1-9][0-9]*)/([1-9][0-9]*)$ ]] \
    || part_usage_error "${group} 要一個 <i>/<n>（例如 2/6），拿到的是「${share}」"
  local index="${BASH_REMATCH[1]}" total="${BASH_REMATCH[2]}"
  (( index <= total )) || part_usage_error "${group} 的第 ${index} 份超出了總共 ${total} 份"

  local all ids
  all="$(mktemp)"
  ids="$(mktemp)"
  collect_ids "${all}" "${spec}"
  awk -v i="${index}" -v n="${total}" '(NR - 1) % n == i - 1' "${all}" >"${ids}"
  run_pytest_part "${group}-${index}-of-${total}" "${ids}"
  rm -f "${all}" "${ids}"
}

# 把各段合起來，再交給 coverage_gate.sh。
run_part_gate() {
  local root="${REPO_ROOT}/${PARTS_DIR}"
  if [[ ! -d "${root}" ]]; then
    printf 'test.sh: 找不到 %s/，沒有任何一段的結果可以合併\n' "${PARTS_DIR}" >&2
    printf 'test.sh: 下一步：先跑 --part fast 與每一份 --part api／web <i>/<n>，再跑 --part gate\n' >&2
    exit 1
  fi

  local all data
  all="$(mktemp)"
  data="$(mktemp)"
  collect_ids "${all}" test/pytest

  # 判定與 web 覆蓋率的合併在這裡；失敗時它自己說原因並以非零結束，set -e 接著停下。
  # 成功時把要合併的 Python 覆蓋率資料檔逐行寫進 ${data}。
  python3 - "${root}" "${all}" "${REPO_ROOT}/.coverage-web.json" "${data}" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
collected = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8").splitlines()
web_out = pathlib.Path(sys.argv[3])
data_out = pathlib.Path(sys.argv[4])

SHOWN = 10
SHARE = re.compile(r"(api|web)-([1-9][0-9]*)-of-([1-9][0-9]*)")
GROUPS = ("api", "web")
problems = []


def listed(items):
    head = "、".join(items[:SHOWN])
    return head + (f"……共 {len(items)} 條" if len(items) > SHOWN else "")


# ── 每一段都在 ────────────────────────────────────────────────────────────
parts = sorted(path for path in root.iterdir() if path.is_dir())
names = [path.name for path in parts]
shares = {group: {} for group in GROUPS}
web_parts = []
for path in parts:
    if path.name == "fast":
        continue
    match = SHARE.fullmatch(path.name)
    if match is None:
        problems.append(f"ci-parts/{path.name}/ 不是認得的段名（fast、api-<i>-of-<n> 或 web-<i>-of-<n>）")
        continue
    shares[match[1]].setdefault(int(match[3]), set()).add(int(match[2]))
    if match[1] == "web":
        web_parts.append(path)

if "fast" not in names:
    problems.append("少了 fast 那一段")
for group in GROUPS:
    if not shares[group]:
        problems.append(f"一段 {group} 都沒有")
    elif len(shares[group]) > 1:
        problems.append(f"{group} 各段的總份數不一致：{sorted(shares[group])}")
    else:
        ((total, got),) = shares[group].items()
        missing = sorted(set(range(1, total + 1)) - got)
        if missing:
            problems.append(f"{group} 分成 {total} 份，少了第 {'、'.join(map(str, missing))} 份")

for path in parts:
    needed = ["ids.txt", "python.coverage"] + (["web.json"] if path in web_parts else [])
    for name in needed:
        if not (path / name).is_file():
            problems.append(f"ci-parts/{path.name}/{name} 不存在")

# ── 每一條規格恰好被一段跑過 ──────────────────────────────────────────────
owner = {}
twice = []
for path in parts:
    ids_file = path / "ids.txt"
    if not ids_file.is_file():
        continue
    for node in ids_file.read_text(encoding="utf-8").splitlines():
        if node in owner:
            twice.append(f"{node}（{owner[node]} 與 {path.name}）")
        else:
            owner[node] = path.name

expected = set(collected)
never = [node for node in collected if node not in owner]
unknown = sorted(node for node in owner if node not in expected)
if never:
    problems.append(f"{len(never)} 條規格沒有被任何一段跑到：{listed(never)}")
if twice:
    problems.append(f"{len(twice)} 條規格被不只一段跑到：{listed(twice)}")
if unknown:
    problems.append(f"{len(unknown)} 條跑過的規格不在這次的收集裡：{listed(unknown)}")

# ── web 覆蓋率的合併 ──────────────────────────────────────────────────────
# 每一段的報告是同一份 index.html 的行覆蓋率。「有程式碼的行」只由原始碼決定
# （test/pytest/system/v8_coverage.py 的 code_lines），所以每一段的那一組行相同，
# 合起來「沒被執行到的行」就是各段的交集。這個前提先驗：同一個檔案、同樣的行數、
# 每一份自己的數字加得起來。對不上就停，不猜。
reports = []
for path in web_parts:
    report_file = path / "web.json"
    if report_file.is_file():
        reports.append((path.name, json.loads(report_file.read_text(encoding="utf-8"))))

for name, report in reports:
    if report["covered_lines"] + len(report["uncovered_lines"]) != report["code_lines"]:
        problems.append(f"ci-parts/{name}/web.json 自己的數字加不起來，格式可能改了")
if reports:
    files = {report["file"] for _, report in reports}
    code = {report["code_lines"] for _, report in reports}
    if len(files) != 1 or len(code) != 1:
        problems.append(f"各段的 web 報告不是同一份檔案、同樣的行數：檔案 {sorted(files)}、行數 {sorted(code)}")

if problems:
    print("test.sh: 各段合不起來，覆蓋率沒有判：", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(
        "test.sh: 下一步：補跑缺的那幾段，或修正 --part api／web 的份數，再重跑 --part gate",
        file=sys.stderr,
    )
    sys.exit(1)

uncovered = set(reports[0][1]["uncovered_lines"])
for _, report in reports[1:]:
    uncovered &= set(report["uncovered_lines"])
code_lines = reports[0][1]["code_lines"]
covered = code_lines - len(uncovered)
web_out.write_text(
    json.dumps(
        {
            "area": "web",
            "file": reports[0][1]["file"],
            "covered_lines": covered,
            "code_lines": code_lines,
            "percent": round(100 * covered / code_lines, 2) if code_lines else 0.0,
            "uncovered_lines": sorted(uncovered),
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
data_out.write_text(
    "".join(f"{path / 'python.coverage'}\n" for path in parts), encoding="utf-8"
)
print(
    f"test.sh: {len(parts)} 段、{len(collected)} 條規格，每一條恰好被一段跑過；"
    f"web 覆蓋率由 {len(reports)} 份報告合併"
)
PY

  local -a files=()
  mapfile -t files <"${data}"
  rm -f "${all}" "${data}" "${REPO_ROOT}/.coverage"
  # --keep：各段的資料檔留著，gate 重跑時還讀得到同一份輸入。
  coverage combine --keep --data-file="${REPO_ROOT}/.coverage" "${files[@]}"
  coverage report
  ./script/coverage_gate.sh
}

run_part() {
  case "${1:-}" in
    fast) run_part_fast ;;
    api) [[ $# -ge 2 ]] || part_usage_error "api 後面要接 <i>/<n>"; run_part_share api "${API_SPEC}" "$2" ;;
    web) [[ $# -ge 2 ]] || part_usage_error "web 後面要接 <i>/<n>"; run_part_share web "${WEB_SPEC}" "$2" ;;
    bats) run_bats_levels ;;
    gate) run_part_gate ;;
    "") part_usage_error "--part 後面要接一個段名" ;;
    *) part_usage_error "不認得的段名 $1" ;;
  esac
}

main() {
  cd "${REPO_ROOT}"

  case "${1:-}" in -h|--help) usage; return 0 ;; esac

  if [[ "${CM_IN_TEST_IMAGE:-}" != "1" && "${CM_TEST_LOCAL:-}" != "1" ]]; then
    dispatch_to_container "$@"
  fi

  # 掛在 EXIT 上而不是每條路徑的結尾：一次失敗的執行裡「什麼沒跑到」與一次成功的
  # 執行裡一樣重要，而 set -e 會讓收尾那一行永遠到不了。
  trap report_not_run EXIT

  if (( $# == 0 )); then
    run_lint all
    note_levels_without_specs
    # --cov 涵蓋整個套件；把它拆成四個資料夾各自的門檻，是 coverage_gate 的事。
    # 一次測試、一份資料、四個獨立的判定（#97）。
    pytest test/pytest --cov=src/config_manager --cov-report=term-missing
    ./script/coverage_gate.sh
    run_bats_levels
    return 0
  fi

  case "$1" in
    --lint) shift; run_lint "${1:-all}" ;;
    --level)
      shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      run_pytest_level "$1"
      run_bats_level "$1"
      ;;
    --file)
      shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }
      case "$1" in
        # exec 會把 EXIT trap 一起換掉，那樣單檔執行就是唯一數不出 skip 的入口
        # ——而「跑單一個檔案」正好是最容易碰到一個整檔跳過的規格的入口。
        *.bats) survey_tools bats; run_bats "$1" "$1" ;;
        *) exec pytest "$1" ;;
      esac
      ;;
    --filter) shift; [[ $# -gt 0 ]] || { usage >&2; exit 2; }; exec pytest test/pytest -k "$1" ;;
    --part) shift; run_part "$@" ;;
    *) printf 'test.sh: 不認得的參數 %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
}

main "$@"
