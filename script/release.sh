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
# ## 從 v0.9.0 起往後發版（#353）
#
# v0.2.0～v0.8.0 做完時沒有推過 tag，GitHub Releases 停在 v0.1.0。維護者 11/14 在 #353 定案：
# **不回頭在歷史 commit 上補推那幾版的 tag**，從 v0.9.0 起照這支腳本的流程（推 tag → CI 的
# release.yaml → 這支）往後發版；那幾個舊 milestone 在檢查點對照補齊、驗收報表通過之後關閉。
# 所以 v0.1.0 之後的下一個 release 是 v0.9.0 的候選，中間沒有版本不是漏發。
#
# ### 打 tag 之前：先在 main 手動觸發 CI（#407，推論、未實測）
#
# 打 tag 前，先在 main 手動觸發一次 CI：`gh workflow run ci.yaml --ref main`。等那一輪的
# `ci-rollup` 通過，tag 打在那一輪測的 commit 上（`gh run view <run> --json headSha`）。
#
# 這一段是**推論、未實測**：
#
# - 事實：在 sim_asset 查到一個 bot 的合併 commit（`5356b74`）沒有任何檢查（check run 0、
#   status 0）；#164 的 tag ruleset 要求 `ci-rollup`。config_manager 這邊手動 CI 與推 tag 的
#   實際結果，等第一次發版驗證。
# - 推論：直接在 bot 的 merge commit 上打 tag 會被 tag ruleset 拒絕；手動觸發的那一輪會在
#   同一個 commit 上留下 `ci-rollup`，tag 才推得上去。
# - 第一次發版時實測，把結果補進 `AGENTS.md`。
#
# release.yaml 跑的是 **tag 指的那個 commit 裡的** release.sh：#407 合併後的第一次發版，tag 要
# 打在含 #407 合併的 commit 上，否則跑的是舊的發版邏輯。
#
# ### tag 不刪、不移（#407 定案 14）
#
# tag ruleset 擋刪除與 force push，所以打錯或發壞的 tag 留著：rc 改推下一個 rc 編號，正式 tag
# 改發下一個 patch 版。這裡的每一則下一步都指向推導出來的下一個名字，沒有一則要人刪 tag、
# 移 tag，或把同一個名字再推一次。
#
# ## rc 的紅燈是它要記錄的東西
#
# rc 的用途是記錄「現在走到哪」：rc1 可以是紅的，rc2 轉綠。**擋下紅色的 rc 等於讓 N
# 無法遞增**，那就失去 rc 的意義了。所以報表未通過時 release 仍然建立，紅綠寫在標題
# 上，一眼看得到。
#
# 也因為如此，「rc 的 release 建起來了」不代表可以打正式 tag（#407）。rc 建好之後印出的
# 下一步只在整體判定（`--gate overall`）通過、覆蓋率 badge 一致時，才寫出在同一個 SHA 上打
# 正式 tag；少一樣就指向下一個 rc。每一則叫人打正式 tag 的下一步都帶同一段「正式 tag 前的
# 核對」（`_formal_check`）。
#
# ## 報表未通過與報表壞掉是兩件事
#
# acceptance.sh 的 1 是「這一版還沒做完」，2 是「這份報表本身不能信」（對照表指到
# 不存在的規格、milestone 不存在、對照表解析不了）。前者正是 rc 要記錄的東西；
# 後者連 rc 都不建——一個附著壞掉報表的 release，與一份手寫的「已通過」表格一樣
# 沒有證據力。兩者混成同一種處置，就分不出「還沒做完」與「沒有人在驗」。
#
# 結束碼本身不夠分辨（#377）。acceptance.sh 在 set -e 底下建映像，建不起來就以 docker 的
# 結束碼結束——那也是 1，輸出卻只有一段建置錯誤、一條判定都沒有。所以 0 與 1 只在輸出真的
# 是一份報表時才算數：一份跑完的報表最後一定有摘要行（各種判定的條數）與判定行，判定行要與
# 結束碼一致。少了任何一行，就與 2 同一個處置。核對的是現行 acceptance.sh 印出的那兩行，
# 不是寬鬆的比對：它的格式改了，這裡會擋下、大聲說報表不能信，而不是把它當成未通過發出去。
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
# ## rc 與 patch 的編號由既有 tag 推導（#407）
#
# 人工指定會撞號或跳號。`--next <版號>` 印出那個版號的下一個 rc；而**推上來的 tag 若跳號就
# 擋下**——少了後面這一半，「由工具推導」就只是一句建議，而建議與規範的差別正是這個 repo
# 反覆付過代價的地方（§0.4）。rc 與 patch 用同一套規則，分成兩件事：
#
# - **驗證這次收到的 tag**。release.yaml 跑的時候這個 tag 已經存在，所以把它自己排除在外，
#   只驗「比它小的號碼都已經有 tag」：rc 是同一個版號的 rc1～rc(N-1)；patch 版 vX.Y.Z（Z 大於
#   0，正式或 rc）另外要 vX.Y.0～vX.Y.(Z-1) 都有正式 tag。不用「它是最大號加 1」：之後已經有
#   更新的 tag 時，重跑舊 tag 的 release 仍然要通過編號檢查。
# - **推導下一號**（`--next` 與各則下一步）。既有 tag 全部算進去，包括打錯、被擋下而保留的
#   那幾個（tag 不能刪）；取最小的缺號，沒有缺號就是最大號加 1。rc1、rc2 之後誤推了 rc5：
#   下一號是 rc3，再來 rc4，再來 rc6。下一個 patch 同理，只看同一個 vX.Y 的正式 tag：v0.9.0
#   發壞是 v0.9.1；已有誤推而保留的 v0.9.5 也還是 v0.9.1；v0.9.1 已被占用就是 v0.9.2。
#
# 號碼只認沒有前導 0 的十進位：v0.9.01 與 v0.9.1 是同一個號碼，兩種寫法都認的話，「比它小的
# 號碼在不在」就說不清。
#
# ## patch 版用 vX.Y.0 的檢查點（#407）
#
# Z 大於 0 的 tag 是已交付行為的修正（AGENTS.md：已交付行為的 bug → fix 加 patch 版號；一個
# milestone 的功能是 minor），不加功能，該驗的就是那個 minor 的檢查點。所以驗收用 vX.Y.0 那一版
# 的檢查點（交給 acceptance.sh 的是 vX.Y.0），覆蓋率 badge 也用 vX.Y.0（badge 隨 milestone 收尾
# 更新）；rc 編號、release 標題與 tag 用 patch 版號，同一個 patch 版的 rc 從 rc1 起算、與 vX.Y.0
# 的 rc 分開編號；release notes 寫「驗收：vX.Y.0」。對照表不另開 patch 版的 [[milestone]]：那只會
# 複製一份同樣的檢查點。vX.Y.0 不在對照表時 acceptance.sh 照樣回 2——打錯的版號不會看起來像通過。
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
      script/release.sh --next <版號>

  <tag>   vX.Y.Z-rcN（候選）或 vX.Y.Z（正式）
  <版號>  tag 的版號 vX.Y.Z，例如 v0.9.0、v0.9.1

在 <tag> 指的這份程式碼上執行 script/acceptance.sh，並以那份報表建立 GitHub release。

  rc tag    不論報表綠紅都建立 release，紅綠寫在標題上；印出的下一步只在整體判定通過、
            覆蓋率 badge 一致時才寫出打正式 tag
  正式 tag  只有報表全數通過才建立；未通過時擋下，並指名是哪幾條檢查點
  兩者皆是  報表本身壞掉（對照表對不上），或輸出不是一份完整的報表（例如建映像失敗）
            時不建立 release
  patch 版  vX.Y.Z（Z 大於 0）與它的 rc：驗收與覆蓋率 badge 用 vX.Y.0 那一版，
            rc 編號、release 標題與 tag 用 patch 版號

  --next <版號>  印出那個版號的下一個 rc tag 名，由既有 tag 推導：最小的缺號，沒有缺號
                 就是最大號加 1，打錯而留下的 tag 也算在內。推上來的 tag 若比它小的
                 號碼有缺（跳號）會被擋下

發版前（#407，推論、未實測）：先在 main 手動觸發 CI（gh workflow run ci.yaml --ref main），
等那一輪的 ci-rollup 通過，tag 打在那一輪測的 commit 上（gh run view <run> --json headSha）。
tag 不刪、不移：打錯或發壞時，rc 改推下一個 rc，正式 tag 改發推導出的下一個 patch 版。

  exit 0   release 建好了（或 --next 印完了）
  exit 1   正式 tag 的報表未通過，release 沒有建立
  exit 2   用法錯誤、tag 格式認不得、編號跳號（rc 或 patch）、缺 gh，或報表本身壞掉
           （acceptance.sh 回 0、1 以外的碼，或輸出裡沒有摘要行、沒有與結束碼相符的判定行）

  覆蓋率 badge：在 <tag> 指的 commit 上重新量測，畫出這一版的 SVG，與 doc/badge/coverage.svg
  逐位元組比對。正式 tag 不一致或量不到就不建立 release；rc 照常建立，notes 寫明

  CM_RELEASE_ACCEPTANCE  改用這個指令產生報表（供 test/bats/unit/release.bats 使用）
  CM_RELEASE_COVERAGE    改用這個指令量覆蓋率，收一個參數＝寫出 coverage.tsv 的位置（同上）

報表的逐條判定進 release notes，判定寫在第一行；同一份內容也作為附加檔案。
報表寫在目前目錄下的 acceptance-<tag>.txt，notes 寫在 release-notes-<tag>.md。
USAGE
}

# tag 的版號 vX.Y.Z。號碼不帶前導 0（見檔頭「rc 與 patch 的編號由既有 tag 推導」）。
readonly VERSION_PATTERN='v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'

# vX.Y.Z 與 vX.Y.Z-rcN 兩種形狀，其餘一律擋下。設成 VERSION、MILESTONE 與 RC 三個值：
# VERSION 是 tag 自己的版號；MILESTONE 是要驗的那一版（patch 版是 vX.Y.0，#407）；RC 空的
# 時候代表這是正式 tag。
_parse_tag() {
  local tag="$1"
  local pattern='^('"${VERSION_PATTERN}"')(-rc([1-9][0-9]*))?$'

  if [[ ! "${tag}" =~ ${pattern} ]]; then
    printf 'release: 認不得的 tag 格式：%s\n' "${tag}" >&2
    printf 'release: 只認 vX.Y.Z（正式）與 vX.Y.Z-rcN（候選）兩種形狀，號碼不帶前導 0\n' >&2
    printf 'release: 下一步：這個 tag 留著（tag 不能刪，#407），改推 script/release.sh --next <版號> 印出的那個 tag（<版號> 是要發的 vX.Y.Z，例如 v0.9.0）\n' >&2
    return 2
  fi

  VERSION="${BASH_REMATCH[1]}"
  MILESTONE="v${BASH_REMATCH[2]}.${BASH_REMATCH[3]}.0"
  RC="${BASH_REMATCH[6]:-}"
}

# --next 收的是版號（#407），不是 rc tag 名，也不是對照表的 id。
_parse_version() {
  local pattern='^'"${VERSION_PATTERN}"'$'
  if [[ "$1" =~ ${pattern} ]]; then
    return 0
  fi
  printf 'release: --next 收的是 tag 的版號 vX.Y.Z，%s 不是\n' "$1" >&2
  printf 'release: 下一步：改用 script/release.sh --next vX.Y.Z，例如 --next v0.9.1 印出 v0.9.1-rcN\n' >&2
  return 2
}

# 既有 tag 裡，<前綴> 後面緊接一個號碼的那些號碼，一行一個。第二個參數起是另外也算進去的
# tag 名：發布時被推上來的那一個由這裡帶進來，不靠它已經在 git tag 裡。前綴後面不是一個純
# 號碼的（v0.9. 後面接 1-rc1）不算：rc tag 不佔 patch 號。
_numbers_after() {
  local prefix="$1" tag rest
  shift
  while IFS= read -r tag; do
    if [[ "${tag}" != "${prefix}"* ]]; then
      continue
    fi
    rest="${tag#"${prefix}"}"
    if [[ "${rest}" =~ ^(0|[1-9][0-9]*)$ ]]; then
      printf '%s\n' "${rest}"
    fi
  done < <(git tag --list "${prefix}*"; printf '%s\n' "$@")
}

# 從 <起點> 起第一個不在標準輸入裡的號碼：最小的缺號，沒有缺號就是最大號加 1。
_smallest_missing() {
  local candidate="$1" number
  local -A taken=()
  while IFS= read -r number; do
    taken["${number}"]=1
  done
  while [[ -n "${taken[${candidate}]:-}" ]]; do
    candidate=$((candidate + 1))
  done
  printf '%d\n' "${candidate}"
}

# <前綴> 的號碼從 <起點> 到 <上限> 之前（不含上限）還沒有 tag 的那幾個，各印成完整的 tag 名。
# 只印前 6 個就停：一個誤推的大號碼（rc900）不該讓這裡數上幾百個缺號。
_missing_below() {
  local prefix="$1" number="$2" limit="$3" found=0 taken_number
  local -A taken=()
  while IFS= read -r taken_number; do
    taken["${taken_number}"]=1
  done < <(_numbers_after "${prefix}")
  while ((number < limit && found < 6)); do
    if [[ -z "${taken[${number}]:-}" ]]; then
      printf '%s%d\n' "${prefix}" "${number}"
      found=$((found + 1))
    fi
    number=$((number + 1))
  done
}

# 下一個 rc tag 名（#407）：那個版號的既有 rc 全部算進去——包括跳號被擋下而保留的——取最小的
# 缺號。找的是 tag 自己的版號（v0.9.1 的 rc 從 v0.9.1-rc1 起算），不是驗收用的那一版。第二個
# 參數起是另外也算進去的 tag 名（發布時就是被推上來的那一個）。
_next_rc_tag() {
  local version="$1"
  shift
  printf '%s-rc%d\n' "${version}" "$(_numbers_after "${version}-rc" "$@" | _smallest_missing 1)"
}

# 下一個 patch 版號（#407）：同一個 vX.Y 的既有正式 tag 全部算進去——包括發壞、沒有 release
# 而保留的——取最小的缺號。第二個參數起是另外也算進去的 tag 名，同上。
_next_patch() {
  local version="$1"
  shift
  local series="${version%.*}."
  printf '%s%d\n' "${series}" "$(_numbers_after "${series}" "$@" | _smallest_missing 0)"
}

# 正式 tag 前的核對（#407）。每一則叫人打正式 tag 的下一步都帶這同一段：rc 不論整體判定、
# 不論 badge 一不一致都建得起 release，所以「rc 的 workflow 成功」或「標題寫通過」都不代表
# 可以打正式 tag。
_formal_check() {
  local version="$1"
  printf '正式 tag 前的核對：先推 script/release.sh --next %s 印出的 rc，打在 main 手動觸發 CI（gh workflow run ci.yaml --ref main）通過的那個 SHA（有修正就在修正合併之後）；在同一個 SHA 上確認那個 rc 的 release notes 裡 --gate overall 的判定是通過、覆蓋率 badge 一致，兩者都成立才打正式 tag %s。rc 的 workflow 成功、或標題寫通過都不夠：rc 在整體未通過或 badge 不一致時照樣建立 release' \
    "${version}" "${version}"
}

# 這個 tag 發壞之後改推哪一個（#407 定案 14：tag 不刪、不移）。<修好什麼> 是要先做的那一段。
# rc 指向推導出的下一個 rc；正式 tag 指向推導出的下一個 patch 版，並帶正式 tag 前的核對。
_after_failure() {
  local tag="$1" version="$2" rc="$3" fix="$4" patch
  if [[ -n "${rc}" ]]; then
    printf '這個 tag 留著（tag 不能刪），%s之後，推 script/release.sh --next %s 印出的下一個 rc（現在是 %s）' \
      "${fix}" "${version}" "$(_next_rc_tag "${version}" "${tag}")"
    return 0
  fi
  patch="$(_next_patch "${version}" "${tag}")"
  printf '這個 tag 留著（tag 不能刪），%s之後，改發下一個 patch 版 %s（它的 rc 現在推導出的是 %s）。%s' \
    "${fix}" "${patch}" "$(_next_rc_tag "${patch}")" "$(_formal_check "${patch}")"
}

# 驗證這次收到的 tag 的編號（#407，見檔頭）。排除它自己，只驗比它小的號碼都已經有 tag：patch 版
# 先看 vX.Y.0～vX.Y.(Z-1) 的正式 tag，再看同一個版號的 rc1～rc(N-1)。有缺就是跳號，擋下；這個 tag
# 留著，下一步指向推導出來的那一個。
_check_number() {
  local tag="$1" version="$2" rc="$3"
  local series="${version%.*}." patch="${version##*.}" target=""
  local -a missing=()

  if ((patch > 0)); then
    mapfile -t missing < <(_missing_below "${series}" 0 "${patch}")
  fi
  if ((${#missing[@]} > 0)); then
    # 比它小的 patch 號有缺：該發的是那個缺號，不是這一版。
    target="$(_next_patch "${version}" "${tag}")"
  elif [[ -n "${rc}" ]]; then
    mapfile -t missing < <(_missing_below "${version}-rc" 1 "${rc}")
    target="${version}"
  fi
  if ((${#missing[@]} == 0)); then
    return 0
  fi

  local listed rc_name next
  listed="$(printf '%s、' "${missing[@]:0:5}")"
  listed="${listed%、}"
  if ((${#missing[@]} > 5)); then
    listed="${listed}……"
  fi
  rc_name="$(_next_rc_tag "${target}" "${tag}")"
  if [[ -n "${rc}" ]]; then
    next="這個 tag 留著（tag 不能刪），改推 script/release.sh --next ${target} 印出的那一個（現在是 ${rc_name}）"
  else
    next="這個 tag 留著（tag 不能刪），改推 script/release.sh --next ${target} 印出的那一個 rc（現在是 ${rc_name}），正式版改發 ${target}。$(_formal_check "${target}")"
  fi
  printf 'release: %s 跳號：比它小的 %s 還沒有 tag（編號由既有 tag 推導，比它小的號碼都要已經有 tag）\n' \
    "${tag}" "${listed}" >&2
  printf 'release: 由既有 tag 推導出來的是 %s\n' "${rc_name}" >&2
  printf 'release: 下一步：%s\n' "${next}" >&2
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

# acceptance.sh 的輸出是不是一份跑完的報表：有摘要行，也有與結束碼相符的判定行（#377）。
# 以 here-string 餵給 grep，不用管線：grep -q 一找到就結束，pipefail 底下前面的 printf
# 可能因 SIGPIPE 失敗，一份夠長的完整報表就會被誤判成不完整。
_is_report() {
  local body="$1" milestone="$2" verdict="$3"
  local name="${milestone//./\\.}"
  grep -qxE "acceptance: ${name} —— [0-9]+ 通過／[0-9]+ 未通過／[0-9]+ 未涵蓋／[0-9]+ 人工待驗／[0-9]+ 已簽核" <<<"${body}" &&
    grep -qxF "acceptance: ${milestone} ${verdict}（--gate overall）" <<<"${body}"
}

_write_notes() {
  local notes="$1" verdict="$2" tag="$3" milestone="$4" commit="$5" body="$6" badge="$7" version="$8"

  {
    # 第一行就是判定。一個要往下捲三頁才看得出紅綠的 release，與一個沒有標示的
    # release 沒有差別。判定標題寫的是驗收的那一版：patch 版寫 vX.Y.0（#407）。
    printf '## 驗收：%s %s\n\n' "${milestone}" "${verdict}"
    if [[ "${version}" != "${milestone}" ]]; then
      printf '這是 patch 版 %s：驗收用 %s 那一版的檢查點，覆蓋率 badge 也用 %s（patch 版不加功能，#407）。\n\n' \
        "${version}" "${milestone}" "${milestone}"
    fi
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

# 覆蓋率 badge 不一致或量不到時，要先做的那一段（#354、#407）。<狀態> 是 differs 或 unmeasured。
_badge_fix() {
  local milestone="$1" state="$2"
  if [[ "${state}" == differs ]]; then
    printf '以這個 commit 量到的 coverage.tsv（CI coverage job 的 artifact coverage-summary）執行 ./script/coverage_badge.sh %s coverage.tsv > doc/badge/coverage.svg 並提交' \
      "${milestone}"
    return 0
  fi
  printf '看上面的量測輸出修好量測'
}

# rc 的 release 建好之後的下一步（#407）。整體判定（--gate overall）通過、覆蓋率 badge 一致，兩者
# 都成立才寫出在同一個 SHA 上打正式 tag，並帶正式 tag 前的核對；少一樣就寫出要先修什麼，指向
# 下一個 rc。rc 不論綠紅都建得起來，「rc 發出去了」不等於「可以打正式 tag」。
_after_rc() {
  local tag="$1" version="$2" milestone="$3" verdict="$4" badge_state="$5" sha="$6"
  if [[ "${verdict}" == '通過' && "${badge_state}" == same ]]; then
    printf '%s 的 --gate overall 通過、覆蓋率 badge 一致；它若打在 main 手動觸發 CI 通過的那個 SHA，就在同一個 SHA %s 上打正式 tag %s：git tag %s %s && git push origin %s。%s' \
      "${tag}" "${sha}" "${version}" "${version}" "${sha}" "${version}" "$(_formal_check "${version}")"
    return 0
  fi

  local problem="" fix=""
  if [[ "${verdict}" != '通過' ]]; then
    problem="--gate overall 未通過"
    fix="修好報表裡沒過的檢查點（人工待驗的要照 doc/ACCEPTANCE-SIGNOFF.md 補上簽核）"
  fi
  if [[ "${badge_state}" == differs ]]; then
    problem="${problem:+${problem}、}覆蓋率 badge 與這個 commit 重新量測畫出來的不同"
  elif [[ "${badge_state}" == unmeasured ]]; then
    problem="${problem:+${problem}、}覆蓋率沒有量到"
  fi
  if [[ "${badge_state}" != same ]]; then
    fix="${fix:+${fix}，並}$(_badge_fix "${milestone}" "${badge_state}")"
  fi
  printf '%s：%s，這一版還不能發正式版；%s之後，推 script/release.sh --next %s 印出的下一個 rc（現在是 %s）' \
    "${tag}" "${problem}" "${fix}" "${version}" "$(_next_rc_tag "${version}" "${tag}")"
}

_release() {
  local tag="$1"
  local VERSION MILESTONE RC
  _parse_tag "${tag}"
  _require_gh
  _check_number "${tag}" "${VERSION}" "${RC}"

  # 報表要說得出它是在哪一份程式碼上跑出來的，否則它與一份本機跑出來的輸出沒有
  # 差別——而那正是這份報表要取代的東西。完整的 SHA 給 rc 建好之後的下一步用：正式 tag
  # 要打在同一個 SHA 上（#407）。
  local commit sha
  sha="$(git rev-parse HEAD)"
  commit="$(git rev-parse --short HEAD)"

  local body code=0
  body="$("${ACCEPTANCE}" "${MILESTONE}" 2>&1)" || code=$?

  # 2 說的是「這份報表不能信」，不是「這一版還沒做完」。沒有報表就沒有東西可以放進
  # release，rc 也一樣——rc 記錄的是走到哪，不是「有沒有跑過」。下一步先算成文字，再與
  # 其餘幾行以連續 printf 送出（lint_messages 的相鄰規則，#255）。
  local next
  if ((code != 0 && code != 1)); then
    next="$(_after_failure "${tag}" "${VERSION}" "${RC}" "修好 doc/acceptance-checkpoints.toml 的參照（驗收查的是 ${MILESTONE} 那一版）")"
    printf '%s\n' "${body}" >&2
    printf 'release: %s 的驗收報表產不出來（acceptance.sh 回 %d），%s 不建立 release\n' \
      "${MILESTONE}" "${code}" "${tag}" >&2
    printf 'release: 那不是「未通過」，是根本沒有報表——一個附著壞掉報表的 release 證明不了東西\n' >&2
    printf 'release: 下一步：%s\n' "${next}" >&2
    return 2
  fi

  local verdict='通過'
  if ((code != 0)); then
    verdict='未通過'
  fi

  # 0 與 1 只在輸出是一份報表時才算數（#377，見檔頭）：建映像失敗也回 1，那時一條判定都沒有。
  if ! _is_report "${body}" "${MILESTONE}" "${verdict}"; then
    next="$(_after_failure "${tag}" "${VERSION}" "${RC}" '看上面的輸出修好建置或執行（例如建映像失敗）')"
    printf '%s\n' "${body}" >&2
    printf 'release: %s 的驗收輸出不是一份完整的報表（acceptance.sh 回 %d，輸出裡卻沒有摘要行，或沒有「%s」的判定行），%s 不建立 release\n' \
      "${MILESTONE}" "${code}" "${verdict}" "${tag}" >&2
    printf 'release: 那不是「%s」——建置或執行在印出判定之前就停了，沒有一條判定可以放進 release\n' "${verdict}" >&2
    printf 'release: 下一步：%s\n' "${next}" >&2
    return 2
  fi

  # 人工待驗也列（#355）：自動檢查全綠、只差人工簽核時，沒有未通過或未涵蓋可列，不列它，
  # 擋下的訊息就說不出還差什麼。
  local -a offenders=()
  mapfile -t offenders < <(printf '%s\n' "${body}" |
    grep -E '檢查點 [0-9]+[[:space:]]+(未通過|未涵蓋|人工待驗)' || true)

  # 正式 tag 的閘門。rc 走不到這裡：它的紅燈是它要記錄的東西。
  if [[ -z "${RC}" && "${verdict}" == '未通過' ]]; then
    # 沒過的檢查點先算成一段文字，再與表頭、下一步以連續 printf 送出：一則訊息的各部分之間
    # 不夾程式行，lint_messages 才把它們當同一則（相鄰規則，#255）。條件段以 %s 帶進去、沒有
    # 時為空。
    local offender_note=""
    if ((${#offenders[@]} > 0)); then
      offender_note="$(printf '\nrelease: 沒過的是這幾條檢查點：'; printf '\n%s' "${offenders[@]}")"
    fi
    next="$(_after_failure "${tag}" "${VERSION}" "" '修好上面那幾條（人工待驗的要照 doc/ACCEPTANCE-SIGNOFF.md 補上簽核）')"
    printf 'release: %s 的驗收報表未通過，正式 tag %s 不建立 release%s\n' \
      "${MILESTONE}" "${tag}" "${offender_note}" >&2
    printf 'release: 下一步：%s\n' "${next}" >&2
    return 1
  fi

  # 覆蓋率 badge。正式 tag 走到這裡代表驗收報表已通過；不一致或量不到就擋下。rc 照常建立，
  # 建好之後的下一步照 badge_state 寫（_after_rc）。
  local BADGE_NOTE="" BADGE_DIFF="" badge_state=same
  if ! _check_badge "${MILESTONE}"; then
    badge_state=unmeasured
    if [[ -n "${BADGE_DIFF}" ]]; then
      badge_state=differs
    fi
  fi
  if [[ "${badge_state}" != same && -z "${RC}" ]]; then
    # 兩種原因各有自己要先做的事；先算成文字，再以連續的 printf 送出（lint_messages 的相鄰規則）。
    local problem="${BADGE_NOTE}"
    if [[ "${badge_state}" == differs ]]; then
      problem="doc/badge/coverage.svg ${BADGE_NOTE}：
${BADGE_DIFF}"
    fi
    next="$(_after_failure "${tag}" "${VERSION}" "" "$(_badge_fix "${MILESTONE}" "${badge_state}")")"
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
  _write_notes "${notes}" "${verdict}" "${tag}" "${MILESTONE}" "${commit}" "${body}" "${BADGE_NOTE}" "${VERSION}"

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
  if [[ -n "${RC}" ]]; then
    printf 'release: 下一步：%s\n' "$(_after_rc "${tag}" "${VERSION}" "${MILESTONE}" "${verdict}" "${badge_state}" "${sha}")"
  fi
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
      _parse_version "$1"
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
