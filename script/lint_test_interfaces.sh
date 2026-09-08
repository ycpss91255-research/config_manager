#!/usr/bin/env bash
#
# 新增一個測試介面的程序，由工具擋：**介面與它的第一批測試不得落在同一個 commit**。
#
# ADR-00000018：沒有經過確認的測試介面，不寫測試。#123 記下的實況是這條退化成自我
# 確認——`T19`／`T20`／`T21` 三個介面與它們的第一批規格在同一個 commit 落地
# （`d2ab095`／`5915043`／`4d283b2`），於是「事先議定」在 git 歷史上看不出發生過：
# 同一次提交裡寫下介面，然後立刻宣告它已確認。
#
# 兩條規則，都機器檢查得到：
#
#   規則 A（order）——順序，不需要 GitHub API。
#     `origin/main..HEAD` 的任何一個 commit，若它在 doc/TEST-PLAN.md 新增一個
#     `### T<N> — ` 或 `### A<N> — ` 標題，那個 commit **不得同時**新增或修改 test/
#     底下的任何檔案。只認**新增標題**，不認既有段落的修改：在既有介面上加一條行為
#     不受約束（設計 §3.7 管的是介面，不是每一條行為）。接進 script/test.sh 的
#     run_lint，因為它只需要 git。
#
#   規則 B（agreed）——「事先」，需要 GitHub API。
#     新增的 `### T<N>` 段落裡必須有一個 `#<issue>` 回指，且該 issue 的 createdAt
#     早於加入這個標題的那個 commit 的 author date。這一條讓「事先議定」從一個形容詞
#     變成一個可比對的時間關係。它與 lint_checkpoints 同一種形狀——CI 的獨立 job，
#     因為容器裡沒有 token。
#
# **它擋不住什麼，寫在這裡免得日後以為它被驗過：**
# 同一個作者、同一個 PR、五分鐘前才開的 issue，兩條規則都過。工具擋得住的是**順序
# 與可追溯性**，不是**獨立的確認**。後者需要人，沒有工具做得到。即便如此它仍然值得
# 做：#123 觀察到的三次全部是順序問題（確認的步驟真的走過了，只是在歷史上不可見），
# 不是有人惡意跳過——工具擋住的正是實際發生過的那一種。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/lint_test_interfaces.sh order  [<base ref>]
      script/lint_test_interfaces.sh agreed [<base ref>]

  order   規則 A：某個 commit 新增 `### T<N> — `／`### A<N> — ` 標題，就不得同時
          動到 test/ 底下的檔案。不需要 GitHub API，接進 script/test.sh。
  agreed  規則 B：新增的介面段落要有 `#<issue>` 回指，且該 issue 早於加入標題的
          那個 commit。需要 gh 與 token，跑成 CI 的獨立 job。

  <base ref>  檢查這個 ref 之後的 commit（預設 origin/main）。

  fail  一個 commit 同時新增了測試介面標題與 test/ 底下的檔案（order）
  fail  新增的介面段落沒有 `#<issue>` 回指（agreed）
  fail  回指的 issue 在加入該介面的 commit 之後才建立（agreed）

只認**新增標題**：在既有介面上加一條行為不受約束（設計 §3.7）。

擋不住的：同一個 PR 裡五分鐘前才開的 issue，兩條規則都過。工具擋得住順序與
可追溯性，擋不住獨立的確認——那需要人。
USAGE
}

# 這個 commit 在 doc/TEST-PLAN.md 新增的 T/A 介面編號（一行一個）。只看被加進去的
# `### T<N> — ` 標題行；既有段落的修改不算——它的標題行在 diff 裡是不變的脈絡。
_added_interfaces() {
  local commit="$1"
  git show --format='' "${commit}" -- doc/TEST-PLAN.md 2>/dev/null |
    grep -E '^\+### [TA][0-9]+ — ' |
    sed -E 's/^\+### ([TA][0-9]+) — .*/\1/' || true
}

# 這個 commit 有沒有動到 test/ 底下的任何檔案。
_touches_tests() {
  local commit="$1"
  git diff-tree --no-commit-id --name-only -r "${commit}" | grep -qE '^test/'
}

# 這個 commit 新增到 doc/TEST-PLAN.md 的內容裡，第一個 `#<issue>` 回指的編號。
_added_issue() {
  local commit="$1"
  git show --format='' "${commit}" -- doc/TEST-PLAN.md 2>/dev/null |
    grep -E '^\+' | grep -vE '^\+\+\+' |
    grep -oE '#[0-9]+' | head -n1 | tr -d '#' || true
}

# 這個 commit 的 author date，正規化成 UTC 的 ISO（結尾 Z），供逐字比較。
_commit_utc() {
  local commit="$1"
  TZ=UTC git show -s --date=format-local:'%Y-%m-%dT%H:%M:%SZ' --format=%ad "${commit}"
}

_commits() {
  local base="$1"
  git rev-list "${base}..HEAD"
}

run_order() {
  local base="$1" failures=0 commit ifaces short subject
  while IFS= read -r commit; do
    [[ -n "${commit}" ]] || continue
    ifaces="$(_added_interfaces "${commit}")"
    [[ -n "${ifaces}" ]] || continue
    if _touches_tests "${commit}"; then
      short="$(git rev-parse --short "${commit}")"
      subject="$(git show -s --format=%s "${commit}")"
      printf 'FAIL commit %s（%s）同時新增了測試介面 %s 與 test/ 底下的檔案\n' \
        "${short}" "${subject}" "$(printf '%s' "${ifaces}" | tr '\n' ' ')" >&2
      printf '     下一步：拆成兩個 commit——doc/TEST-PLAN.md 的介面段落單獨一個、\n' >&2
      printf '             test/ 底下的規格另一個。介面要在它的測試之前、可被獨立確認\n' >&2
      failures=$((failures + 1))
    fi
  done < <(_commits "${base}")

  printf 'lint_test_interfaces(order): %s..HEAD -- %d 個問題\n' "${base}" "${failures}"
  ((failures == 0))
}

run_agreed() {
  local base="$1" failures=0 commit ifaces issue created commit_utc short
  while IFS= read -r commit; do
    [[ -n "${commit}" ]] || continue
    ifaces="$(_added_interfaces "${commit}")"
    [[ -n "${ifaces}" ]] || continue
    short="$(git rev-parse --short "${commit}")"
    ifaces="$(printf '%s' "${ifaces}" | tr '\n' ' ')"

    issue="$(_added_issue "${commit}")"
    if [[ -z "${issue}" ]]; then
      printf 'FAIL commit %s 新增了測試介面 %s，但段落裡沒有 #<issue> 回指\n' \
        "${short}" "${ifaces}" >&2
      printf '     下一步：在新介面段落寫下它的來由 issue（#<編號>），且該 issue 要早於這個 commit\n' >&2
      failures=$((failures + 1))
      continue
    fi

    created="$(gh issue view "${issue}" --json createdAt --jq '.createdAt')"
    commit_utc="$(_commit_utc "${commit}")"
    if [[ "${created}" < "${commit_utc}" ]]; then
      continue
    fi
    printf 'FAIL 測試介面 %s 回指的 #%s 在 commit %s 之後才建立（issue %s ≥ commit %s）\n' \
      "${ifaces}" "${issue}" "${short}" "${created}" "${commit_utc}" >&2
    printf '     下一步：介面要事先議定——先開 issue 取得確認，再落地介面\n' >&2
    failures=$((failures + 1))
  done < <(_commits "${base}")

  printf 'lint_test_interfaces(agreed): %s..HEAD -- %d 個問題\n' "${base}" "${failures}"
  ((failures == 0))
}

main() {
  local mode="${1:-}"
  case "${mode}" in
    -h | --help)
      usage
      return 0
      ;;
    order)
      shift
      run_order "${1:-origin/main}"
      ;;
    agreed)
      shift
      run_agreed "${1:-origin/main}"
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

main "$@"
