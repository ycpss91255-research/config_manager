#!/usr/bin/env bats
#
# compose 的接線（T25、#51）——設定解析那一半。
#
# 被觀察的是 **compose 自己解出來的最終設定**（`docker-compose … config`），不是 YAML 的文字：
# 變數的預設值、疊加檔的合併、`${VAR:?…}` 的失敗都是 compose 算的。`config` 只做解析，不需要
# docker daemon，所以這一檔在工具映像裡跑，本機與 CI 是同一組。
#
# **這一檔說得出的是「compose 會照這樣起服務」。** 「這樣起的服務真的只有本機連得到」「重啟後
# 資料還在」要有真的在跑的容器，那一半在 script/compose_smoke.sh（CI 的 Linux runner）。兩種
# 結果分開報，這一檔綠了不代表那一半過了。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  RESOLVED="${BATS_TEST_TMPDIR}/resolved.json"
}

# resolve <compose 檔>… —— 解析這個組合，最終設定寫到 ${RESOLVED}。
#
# 環境是空的（`env -i`）：這台機器上碰巧設了的 CM_* 或 COMPOSE_FILE 不該決定規格的答案。要給
# 變數的規格用 `VARS=(名=值 …)`。`--env-file /dev/null` 同一個理由：開發用的簽出可能有一份
# 不在版控裡的 `.env`，讀了它，測到的是那台機器，不是這個 repo。
resolve() {
  local -a files=()
  local file
  for file in "$@"; do files+=(-f "${REPO_ROOT}/${file}"); done
  run env -i PATH="${PATH}" HOME="${BATS_TEST_TMPDIR}" "${VARS[@]}" \
    docker-compose --project-directory "${REPO_ROOT}" --env-file /dev/null \
    "${files[@]}" config --format json
  [ "${status}" -eq 0 ] && printf '%s\n' "${output}" >"${RESOLVED}"
  return 0
}

# ask <Python 運算式> —— 對最終設定問一個問題。`s` 是 services，`d` 是整份。
ask() {
  python3 -c '
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(eval(sys.argv[2], {"d": d, "s": d["services"]}))
' "${RESOLVED}" "$1"
}

VARS=()

# ── 預設：現場部署（定案 2）──────────────────────────────────────────────────

@test "預設解得出來，剛好兩個服務：backend 與 frontend" {
  resolve compose.yaml

  [ "${status}" -eq 0 ]
  [ "$(ask 'sorted(s)')" = "['backend', 'frontend']" ]
}

@test "預設用的是正式映像與部署模式" {
  resolve compose.yaml

  [ "$(ask '(s["backend"]["build"]["target"], s["frontend"]["build"]["target"], s["backend"]["image"], s["backend"]["environment"]["CM_MODE"])')" \
    = "('runtime', 'runtime', 'config_manager:runtime', 'deployment')" ]
}

@test "預設指到的映像階段在 Dockerfile 裡真的存在" {
  resolve compose.yaml

  grep -qE "^FROM .* AS $(ask 's["backend"]["build"]["target"]')\$" "${REPO_ROOT}/Dockerfile"
}

@test "backend 的 config-repo 掛載點與 CM_CONFIG_REPO 是同一個路徑，而且是具名 volume" {
  resolve compose.yaml

  [ "$(ask '[(v["type"], v["target"] == s["backend"]["environment"]["CM_CONFIG_REPO"]) for v in s["backend"]["volumes"] if v["source"] == "config_repo"]')" \
    = "[('volume', True)]" ]
}

@test "backend 掛進來的目標根目錄就是白名單放行的那一個" {
  VARS=(CM_TARGET_ROOT=/srv/site/config)
  resolve compose.yaml

  [ "$(ask '([v["target"] for v in s["backend"]["volumes"] if v["type"] == "bind"], s["backend"]["environment"]["CM_ALLOWED_ROOTS"])')" \
    = "(['/srv/site/config'], '/srv/site/config')" ]
}

@test "frontend 沒有任何掛載" {
  resolve compose.yaml

  [ "$(ask 's["frontend"].get("volumes", [])')" = "[]" ]
}

# ── 開發：疊上 compose.dev.yaml（定案 2）─────────────────────────────────────

@test "疊上開發檔之後是開發映像與開發模式" {
  resolve compose.yaml compose.dev.yaml

  [ "$(ask '(s["backend"]["build"]["target"], s["frontend"]["build"]["target"], s["backend"]["image"], s["backend"]["environment"]["CM_MODE"])')" \
    = "('devel', 'devel', 'config_manager:devel', 'development')" ]
}

@test "開發映像不烤原始碼，所以開發檔把主機的 src 掛進兩個服務，並讓後端的直譯器找得到它" {
  resolve compose.yaml compose.dev.yaml

  [ "$(ask '[(name, [v["target"] for v in s[name]["volumes"] if v["type"] == "bind" and v["source"].endswith("/src")]) for name in sorted(s)]')" \
    = "[('backend', ['/opt/config_manager/src']), ('frontend', ['/opt/config_manager/src'])]" ]
  [ "$(ask 's["backend"]["environment"]["PYTHONPATH"]')" = "/opt/config_manager/src" ]
}
