#!/usr/bin/env bats
#
# script/lint_exceptions.sh — §0.4 例外處理第 1 條：不得捕捉具名例外之後只 pass 或只 log.debug。
#
# ruff／pylint 擋得住 bare `except:`（E722）與 `except Exception:`（BLE001），但**指名的
# 具體例外之後只吞掉**——`except ValueError: pass`、`except ValueError: log.debug(...)`、
# `contextlib.suppress(...)`、多個 handler 各自 pass——沒有任何既有工具看得到（#112 實測）。
# SIM105 不算：它的修法是改寫成 contextlib.suppress，換個拼法同一個吞，改寫後就再也沒工具看得到。
#
# 觀察位置是命令列：餵一個 .py 檔進去，看結束碼與訊息。與 T19 其餘守門腳本相同。
#
# ## 突變檢查（每條規則各拿掉一次，#112）
#
# | 突變 | 轉紅的規格 |
# |---|---|
# | M1 拿掉「只 pass」判定 | 「except 具名: pass 被擋」「多 handler 各自 pass 被擋」 |
# | M2 拿掉「只 *.debug()」判定 | 「except 具名: log.debug 被擋」 |
# | M3 拿掉 contextlib.suppress 判定 | 「contextlib.suppress 被擋」 |
# | M4 把「具名才擋、bare 留給 E722」改成也擋 bare | 「bare except 不由這支擋（留給 E722）」 |
# | M5 檔案不在時改成安靜通過 | 「python3 缺席或檔案不存在時大聲失敗」對應的那半 |
#
# 每個突變各套用一次再還原，還原後與突變前逐位元組相同。沒有一個突變是靜默通過的。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  LINT="${REPO_ROOT}/script/lint_exceptions.sh"
  WORK="$(mktemp -d)"
  SRC="${WORK}/sample.py"
}

teardown() {
  rm -rf "${WORK}"
}

_py() {
  cat >"${SRC}"
}


@test "except 具名: pass 被擋，訊息指名檔案與行號" {
  _py <<'PY'
def f():
    try:
        risky()
    except ValueError:
        pass
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"sample.py"* ]]
  [[ "${output}" == *"ValueError"* || "${output}" == *"pass"* ]]
}


@test "except 具名: log.debug 被擋" {
  _py <<'PY'
def f(logger):
    try:
        risky()
    except KeyError:
        logger.debug("忽略了 %s", "KeyError")
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"sample.py"* ]]
}


@test "contextlib.suppress 被擋" {
  _py <<'PY'
import contextlib


def f():
    with contextlib.suppress(OSError):
        risky()
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"suppress"* ]]
}


@test "from contextlib import suppress 的裸 suppress 也被擋" {
  _py <<'PY'
from contextlib import suppress


def f():
    with suppress(OSError):
        risky()
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -ne 0 ]
}


@test "多個 handler 各自 pass 被擋（SIM105 看不到的那個形狀）" {
  _py <<'PY'
def f():
    try:
        risky()
    except ValueError:
        pass
    except KeyError:
        pass
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -ne 0 ]
}


@test "有實質處置的 handler 不被擋（零誤報）" {
  _py <<'PY'
import logging

logger = logging.getLogger(__name__)


def f():
    try:
        risky()
    except ValueError as error:
        raise RuntimeError("包起來再拋") from error
    try:
        risky()
    except KeyError:
        logger.warning("這是警告、不是 debug，且後面有處置")
        cleanup()
    try:
        risky()
    except OSError:
        return None
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -eq 0 ]
}


@test "bare except 不由這支擋（留給 E722／ruff）" {
  # 這支只補 ruff／pylint 的缺口（具名例外的吞）。bare except 已由 E722 擋，
  # 這支不重覆擋它——否則兩支對同一行各報一次，修的人不知道以誰為準。
  _py <<'PY'
def f():
    try:
        risky()
    except:  # noqa: E722
        pass
PY
  run "${LINT}" "${SRC}"

  [ "${status}" -eq 0 ]
}


@test "python3 缺席或檔案不存在時大聲失敗，不靜默通過" {
  run "${LINT}" "${WORK}/does-not-exist.py"

  [ "${status}" -ne 0 ]
  [[ "${output}" == *"下一步"* ]]
}


@test "現有的 src/config_manager 零誤報" {
  # 不帶參數＝掃預設標的 src/config_manager。實例先前已被人工改掉（#121/D8），
  # 這支上線即為全綠——它擋的是回流，不是清理一批既有違規。
  run "${LINT}"

  [ "${status}" -eq 0 ]
}
