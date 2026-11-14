#!/usr/bin/env bash
#
# 一個 tag 的驗收報表，以及「這個 tag 發不發得出去」的判定。
#
# 本機跑得出來的報表，證明的是「**某個人在某個時間點的某份程式碼上**跑過」。release
# 上的報表證明的是「**這個 tag 指的這份程式碼**跑出來是這樣」——那才複驗得了：任何人
# checkout 那個 tag 重跑，該得到同一份結論（#150）。所以報表由 CI 在 tag 被推上來時
# 產生，不由人手動附上去：一份手動產生再上傳的檔案，與「宣稱有檢查其實沒檢查」是同
# 一種東西——沒有東西保證那份檔案是這份程式碼跑出來的。
#
# ## rc 的紅燈是它要記錄的東西
#
# rc 的用途是記錄「現在走到哪」：rc1 可以是紅的，rc2 轉綠。**擋下紅色的 rc 等於讓 N
# 無法遞增**，那就失去 rc 的意義了。所以報表未通過時 release 仍然建立，紅綠寫在標題
# 上，一眼看得到。
#
# ## 報表未通過與報表壞掉是兩件事
#
# acceptance.sh 的 1 是「這一版還沒做完」，2 是「這份報表本身不能信」（對照表指到
# 不存在的規格、milestone 不存在、對照表解析不了）。前者正是 rc 要記錄的東西；
# 後者連 rc 都不建——一個附著壞掉報表的 release，與一份手寫的「已通過」表格一樣
# 沒有證據力。兩者混成同一種處置，就分不出「還沒做完」與「沒有人在驗」。
#
# ## 正式 tag 的閘門
#
# 非 rc 的 vX.Y.Z **只有報表全數通過才建得起來**。這一條由這裡擋，不靠人記得
# ——只寫在文件裡的規範等同不存在（§0.4）。擋下時逐條指名沒過的檢查點：說不出是
# 哪一條的閘門，修的時候只能靠猜。
#
# 這裡沒有去查「有沒有一個綠的 rc」，而是**在正式 tag 指的那個 commit 上重跑一次
# 報表**。一個 rc 的綠燈是關於那個 rc 的 commit 的，而正式 tag 可能指向別的地方：
# 查「有沒有綠的 rc」擋不住「rc2 是綠的，之後又進了三個 commit 才打 v0.1.0」
# ——而那正是這道閘門要擋的形狀。
#
# ## rc 的編號由既有 tag 推導
#
# 人工指定會撞號或跳號。`--next` 印出下一個編號；而**推上來的 rc tag 如果不是推導
# 出來的那一個就擋下**——少了後面這一半，「由工具推導」就只是一句建議，而建議與規範
# 的差別正是這個 repo 反覆付過代價的地方（§0.4）。推導把這個 tag 自己排除在外：
# CI 是在 tag 推上來之後才跑的，不排除的話每一次 release 都會擋下自己。
#
# ## 覆蓋率 badge 在 tag 指的 commit 上重新量測（#354）
#
# `doc/badge/coverage.svg` 隨 milestone 收尾更新，日常 PR 不核對它。發版時才核對：在 tag 指的
# 這個 commit 上重新量一次覆蓋率，用 `coverage_badge.sh` 畫出這一版的 SVG，與版控裡的那份
# **逐位元組**比對（維護者 11/14 在 #354 的定案：問題 1 選 A、問題 2 照 a）。不加容差。
# 與驗收報表同一個處置：正式 tag 不一致就不建 release；rc 照常建立，notes 寫明不一致——
# rc 記錄的是「現在走到哪」。量測本身的浮動（api 那一行時有時無）另在 #378 修，不在這裡放寬。
#
# ## 為什麼 gh 在容器外
#
# 檢查本身在容器裡：這支腳本呼叫 script/acceptance.sh，那支自己轉進映像（ADR-00000027）。
# 這支只做兩件容器裡做不到的事——讀 git tag、呼叫 gh——與 CI 的 checkpoints job 同一種
# 形狀：跑在映像之外，因為它只需要 gh 與 git，而容器裡沒有 token。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

# 報表由誰產生可以被覆寫，這樣這支腳本自己也測得到：規格餵給它一份替身報表與一個
# 指定的結束碼，看它建不建 release、標題寫什麼。少了這個，唯一能驗證這道閘門的方法
# 是真的弄紅一條檢查點再發一次 release——而那會在 GitHub 上留下對外可見的東西。
# 與 acceptance.sh 的 CM_ACCEPTANCE_MAP 同一個先例。
readonly ACCEPTANCE="${CM_RELEASE_ACCEPTANCE:-${REPO_ROOT}/script/acceptance.sh}"

# 覆蓋率的重新量測也可以被覆寫，理由同上：真的量一次是整組測試。這個指令收一個參數——要寫出
# coverage.tsv 的位置——成功時以 0 結束。畫 SVG 的永遠是真的 coverage_badge.sh。
readonly COVERAGE="${CM_RELEASE_COVERAGE:-_measure_coverage}"
readonly BADGE="${REPO_ROOT}/script/coverage_badge.sh"

usage() {
  cat <<'USAGE'
用法：script/release.sh <tag>
      script/release.sh --next <milestone>

  <tag>        vX.Y.Z-rcN（候選）或 vX.Y.Z（正式）
  <milestone>  對照表裡的一個 id，例如 v0.1.0

在 <tag> 指的這份程式碼上執行 script/acceptance.sh，並以那份報表建立 GitHub release。

  rc tag    不論報表綠紅都建立 release，紅綠寫在標題上
  正式 tag  只有報表全數通過才建立；未通過時擋下，並指名是哪幾條檢查點
  兩者皆是  報表本身壞掉（對照表對不上）時不建立 release

  --next <milestone>  印出下一個 rc tag 名，由既有 tag 推導。編號不由人工指定：
                      人工指定會撞號或跳號，而推上來的 rc 若不是這個名字會被擋下

  exit 0   release 建好了（或 --next 印完了）
  exit 1   正式 tag 的報表未通過，release 沒有建立
  exit 2   用法錯誤、tag 格式認不得、rc 編號跳號、缺 gh，或報表本身壞掉

  覆蓋率 badge：在 <tag> 指的 commit 上重新量測，畫出這一版的 SVG，與 doc/badge/coverage.svg
  逐位元組比對。正式 tag 不一致或量不到就不建立 release；rc 照常建立，notes 寫明

  CM_RELEASE_ACCEPTANCE  改用這個指令產生報表（供 test/bats/unit/release.bats 使用）
  CM_RELEASE_COVERAGE    改用這個指令量覆蓋率，收一個參數＝寫出 coverage.tsv 的位置（同上）

報表的逐條判定進 release notes，判定寫在第一行；同一份內容也作為附加檔案。
報表寫在目前目錄下的 acceptance-<tag>.txt，notes 寫在 release-notes-<tag>.md。
USAGE
}

# vX.Y.Z 與 vX.Y.Z-rcN 兩種形狀，其餘一律擋下。設成 MILESTONE 與 RC 兩個值：
# 前者是要驗的那一版，後者空的時候代表這是正式 tag。
_parse_tag() {
  local tag="$1"
  local pattern='^(v[0-9]+\.[0-9]+\.[0-9]+)(-rc([1-9][0-9]*))?$'

  if [[ ! "${tag}" =~ ${pattern} ]]; then
    printf 'release: 認不得的 tag 格式：%s\n' "${tag}" >&2
    printf 'release: 只認 vX.Y.Z（正式）與 vX.Y.Z-rcN（候選）兩種形狀\n' >&2
    printf 'release: 下一步：改用 script/release.sh --next <milestone> 印出來的那個 tag 名\n' >&2
    return 2
  fi

  MILESTONE="${BASH_REMATCH[1]}"
  RC="${BASH_REMATCH[3]:-}"
}

# 下一個 rc tag 名。看的是既有 tag 裡最大的編號，不是它們的個數——中間刪掉一個
# tag 之後，用個數會撞號。第二個參數是要排除的 tag（發布時就是被推上來的那一個）。
_next_rc_tag() {
  local milestone="$1" exclude="${2:-}"
  local highest=0 tag number

  while IFS= read -r tag; do
    if [[ -z "${tag}" || "${tag}" == "${exclude}" ]]; then
      continue
    fi
    number="${tag##*-rc}"
    if [[ ! "${number}" =~ ^[1-9][0-9]*$ ]]; then
      continue
    fi
    if ((number > highest)); then
      highest="${number}"
    fi
  done < <(git tag --list "${milestone}-rc*")

  printf '%s-rc%d\n' "${milestone}" "$((highest + 1))"
}

_check_rc_number() {
  local tag="$1" milestone="$2" expected
  expected="$(_next_rc_tag "${milestone}" "${tag}")"

  if [[ "${tag}" == "${expected}" ]]; then
    return 0
  fi
  printf 'release: rc 編號由既有 tag 推導，%s 不是推導出來的那一個\n' "${tag}" >&2
  printf 'release: 由既有 tag 推導出來的下一個是 %s\n' "${expected}" >&2
  printf 'release: 下一步：git push origin :%s 收回這個 tag，改推 %s\n' "${tag}" "${expected}" >&2
  return 2
}

_require_gh() {
  if command -v gh >/dev/null 2>&1; then
    return 0
  fi
  printf 'release: gh 不在 PATH 上，release 建不起來\n' >&2
  printf 'release: 一支建不了 release 卻回 0 的腳本，與一次成功的發布長得一模一樣\n' >&2
  printf 'release: 下一步：安裝 gh（https://cli.github.com），或改在有 gh 的 CI job 上執行\n' >&2
  return 2
}

# 報表檔的表頭。附加檔案會被下載下來單獨閱讀，那時候 release 頁面上的脈絡都不在了
# ——它得自己說得出是哪個 tag、哪個 commit、怎麼重跑。
_report_header() {
  local tag="$1" milestone="$2" commit="$3"
  printf '驗收報表：%s\n' "${tag}"
  printf 'commit：%s\n' "${commit}"
  printf '產生時間：%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf '指令：script/acceptance.sh %s\n' "${milestone}"
  printf '複驗：git checkout %s && ./script/acceptance.sh %s\n' "${tag}" "${milestone}"
  printf '\n'
}

_write_notes() {
  local notes="$1" verdict="$2" tag="$3" milestone="$4" commit="$5" body="$6" badge="$7"

  {
    # 第一行就是判定。一個要往下捲三頁才看得出紅綠的 release，與一個沒有標示的
    # release 沒有差別。
    printf '## 驗收：%s %s\n\n' "${milestone}" "${verdict}"
    printf '%s\n\n' "$(printf '%s\n' "${body}" | grep -F '未涵蓋' | tail -n 1)"
    printf '覆蓋率 badge：%s\n\n' "${badge}"
    printf '這份報表由 CI 在 tag `%s` 指的 commit `%s` 上執行 `script/acceptance.sh %s`\n' \
      "${tag}" "${commit}" "${milestone}"
    printf '產生，不是任何人手動貼上來的。同一份內容也作為附加檔案。\n\n'
    printf '複驗：`git checkout %s && ./script/acceptance.sh %s`\n\n' "${tag}" "${milestone}"
    # 逐條判定原樣進 notes。只寫一句「未通過」的 release notes，讀者還是得自己
    # 去別的地方找是哪一條——而那正是這份報表要取代的東西。
    printf '```text\n%s\n```\n' "${body}"
  } >"${notes}"
}

# 預設的量測：與 CI 相同的幾段（fast、api、web），各跑成一份，最後由 gate 合起來寫出
# coverage.tsv。那是整組 pytest，規格以 CM_RELEASE_COVERAGE 換掉它。ci-parts／與
# coverage/summary/ 是衍生產物，先清掉，免得讀到上一次留下的。
_measure_coverage() {
  local out="$1"
  rm -rf "${REPO_ROOT}/ci-parts" "${REPO_ROOT}/coverage/summary"
  "${REPO_ROOT}/script/test.sh" --part fast || return 1
  "${REPO_ROOT}/script/test.sh" --part api 1/1 || return 1
  "${REPO_ROOT}/script/test.sh" --part web 1/1 || return 1
  # 低於門檻時 gate 以 1 結束，但表格照樣寫出：門檻由 CI 擋，這裡要的是數字。沒寫出就是量不到。
  "${REPO_ROOT}/script/test.sh" --part gate || true
  cp "${REPO_ROOT}/coverage/summary/coverage.tsv" "${out}"
}

# 覆蓋率 badge 的核對（#354）。BADGE_NOTE 是給 release notes 的一句；不一致時差異在 BADGE_DIFF。
# 回 0＝與重新量測畫出來的逐位元組相同。
_check_badge() {
  local milestone="$1" work committed
  work="$(mktemp -d)"
  committed="$(git rev-parse --show-toplevel)/doc/badge/coverage.svg"
  BADGE_DIFF=""
  if ! "${COVERAGE}" "${work}/coverage.tsv" || [[ ! -s "${work}/coverage.tsv" ]]; then
    BADGE_NOTE="這個 commit 的覆蓋率沒有量到（量測失敗，或沒有寫出 coverage.tsv）"
    rm -rf "${work}"
    return 1
  fi
  if ! "${BADGE}" "${milestone}" "${work}/coverage.tsv" >"${work}/measured.svg" 2>"${work}/badge.err"; then
    BADGE_NOTE="這個 commit 的覆蓋率沒有量到（$(head -n 1 "${work}/badge.err")）"
    rm -rf "${work}"
    return 1
  fi
  if [[ -f "${committed}" ]] && cmp -s "${work}/measured.svg" "${committed}"; then
    BADGE_NOTE="與這個 commit 重新量測畫出來的相同"
    rm -rf "${work}"
    return 0
  fi
  BADGE_NOTE="與這個 commit 重新量測畫出來的不同"
  BADGE_DIFF="$(diff "${committed}" "${work}/measured.svg" 2>&1 || true)"
  rm -rf "${work}"
  return 1
}

_release() {
  local tag="$1"
  local MILESTONE RC
  _parse_tag "${tag}"
  _require_gh

  if [[ -n "${RC}" ]]; then
    _check_rc_number "${tag}" "${MILESTONE}"
  fi

  # 報表要說得出它是在哪一份程式碼上跑出來的，否則它與一份本機跑出來的輸出沒有
  # 差別——而那正是這份報表要取代的東西。
  local commit
  commit="$(git rev-parse --short HEAD)"

  local body code=0
  body="$("${ACCEPTANCE}" "${MILESTONE}" 2>&1)" || code=$?

  # 2 說的是「這份報表不能信」，不是「這一版還沒做完」。沒有報表就沒有東西可以放進
  # release，rc 也一樣——rc 記錄的是走到哪，不是「有沒有跑過」。
  if ((code != 0 && code != 1)); then
    printf '%s\n' "${body}" >&2
    printf 'release: %s 的驗收報表產不出來（acceptance.sh 回 %d），%s 不建立 release\n' \
      "${MILESTONE}" "${code}" "${tag}" >&2
    printf 'release: 那不是「未通過」，是根本沒有報表——一個附著壞掉報表的 release 證明不了東西\n' >&2
    printf 'release: 下一步：修好 doc/acceptance-checkpoints.toml 的參照，重推這個 tag\n' >&2
    return 2
  fi

  local verdict='通過'
  if ((code != 0)); then
    verdict='未通過'
  fi

  local -a offenders=()
  mapfile -t offenders < <(printf '%s\n' "${body}" |
    grep -E '檢查點 [0-9]+[[:space:]]+(未通過|未涵蓋)' || true)

  # 正式 tag 的閘門。rc 走不到這裡：它的紅燈是它要記錄的東西。
  if [[ -z "${RC}" && "${verdict}" == '未通過' ]]; then
    # 沒過的檢查點先算成一段文字，再與表頭、下一步以連續 printf 送出：一則訊息的各部分之間
    # 不夾程式行，lint_messages 才把它們當同一則（相鄰規則，#255）。條件段以 %s 帶進去、沒有
    # 時為空，輸出與原本逐位元組相同。
    local offender_note=""
    if ((${#offenders[@]} > 0)); then
      offender_note="$(printf '\nrelease: 沒過的是這幾條檢查點：'; printf '\n%s' "${offenders[@]}")"
    fi
    printf 'release: %s 的驗收報表未通過，正式 tag %s 不建立 release%s\n' \
      "${MILESTONE}" "${tag}" "${offender_note}" >&2
    printf 'release: 下一步：修好上面那幾條，推一個 %s 確認報表轉綠，再打 %s\n' \
      "$(_next_rc_tag "${MILESTONE}")" "${tag}" >&2
    return 1
  fi

  # 覆蓋率 badge。正式 tag 走到這裡代表驗收報表已通過；不一致或量不到就擋下。rc 照常建立。
  local BADGE_NOTE="" BADGE_DIFF=""
  if ! _check_badge "${MILESTONE}" && [[ -z "${RC}" ]]; then
    # 兩種原因各有自己的下一步；先算成文字，再以連續的 printf 送出（lint_messages 的相鄰規則）。
    local problem next
    if [[ -n "${BADGE_DIFF}" ]]; then
      problem="doc/badge/coverage.svg ${BADGE_NOTE}：
${BADGE_DIFF}"
      next="以這個 commit 量到的 coverage.tsv（CI coverage job 的 artifact coverage-summary）執行 ./script/coverage_badge.sh ${MILESTONE} coverage.tsv > doc/badge/coverage.svg 並提交，推一個 $(_next_rc_tag "${MILESTONE}") 確認後再打 ${tag}"
    else
      problem="${BADGE_NOTE}"
      next="看上面的量測輸出修好量測，再重推這個 tag"
    fi
    printf 'release: 正式 tag %s 不建立 release：%s\n' "${tag}" "${problem}" >&2
    printf 'release: 下一步：%s\n' "${next}" >&2
    return 1
  fi

  # notes 是給人在頁面上讀的，附加檔案是給人下載下來比對的。兩者同一份內容，
  # 但被編輯的只會是前者——要拿去比對的是後者。
  local report="${PWD}/acceptance-${tag}.txt"
  {
    _report_header "${tag}" "${MILESTONE}" "${commit}"
    printf '%s\n' "${body}"
  } >"${report}"

  local notes="${PWD}/release-notes-${tag}.md"
  _write_notes "${notes}" "${verdict}" "${tag}" "${MILESTONE}" "${commit}" "${body}" "${BADGE_NOTE}"

  local -a create=(gh release create "${tag}"
    --title "${tag} — 驗收${verdict}"
    --notes-file "${notes}")
  if [[ -n "${RC}" ]]; then
    # rc 不是正式版本。標成正式版本的 rc 會出現在「最新版本」上。
    create+=(--prerelease)
  fi
  create+=("${report}")

  "${create[@]}"
  printf 'release: 已建立 %s（驗收%s），報表 %s\n' "${tag}" "${verdict}" "${report}"
}

main() {
  case "${1:-}" in
    -h | --help)
      usage
      return 0
      ;;
    --next)
      shift
      if [[ $# -ne 1 ]]; then
        usage >&2
        return 2
      fi
      _next_rc_tag "$1"
      return 0
      ;;
  esac

  if [[ $# -ne 1 ]]; then
    usage >&2
    return 2
  fi

  _release "$1"
}

main "$@"
