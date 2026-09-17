#!/usr/bin/env bash
#
# §0.4 例外處理第 1 條的守門：不得捕捉具名例外之後只 `pass` 或只 `log.debug`。
#
# ruff／pylint 已經擋得住兩種：bare `except:`（E722）與 `except Exception:`／`BaseException:`
# （BLE001）——不論 handler 內容。但**指名的具體例外之後只吞掉**沒有任何既有工具看得到（#112
# 逐形狀實測）：`except ValueError: pass`、`except ValueError: log.debug(...)`、
# `contextlib.suppress(...)`、兩個 handler 各自 `pass`。
#
# `SIM105` 不是這條的執行者：它的修法是改寫成 `contextlib.suppress(...)`——同一個吞錯誤、換
# 一個拼法，改寫之後就再也沒有工具看得到；而它只認單一 handler，兩個各自 pass 完全不觸發。把
# `SIM105` 當成「第 1 條有在檢查」，正是這個 repo 抓過七次的「看起來在檢查、其實沒在」。
#
# **只補缺口，不重覆別人擋過的。** bare `except:` 留給 E722、`except Exception:` 留給 BLE001；
# 這支只認**具名**例外（`node.type` 非 None、且不是那兩支的管轄）之後的吞。同一行被兩支各報一次，
# 修的人不知道以誰為準。
#
# **語法形狀清楚，一次 AST 走訪就判得出來**（#112）：`ast.ExceptHandler` 的 body 只有 `ast.Pass`，
# 或只有一個 `*.debug(...)` 的 `ast.Expr` 呼叫，就 fail；`contextlib.suppress(...)` 同理。真正判不出
# 來的是「handler 裡有東西不代表那是策略」（`except X: return None`）——那半句留給 code review，
# 刻意不在這裡。
#
# Python 那邊用 `ast` 而不是 grep：handler 會跨行、例外型別會是 `a.b.C` 或 tuple、`.debug` 可能
# 接在任何運算式後面——這些用正規式判都會漏或誤報。與 `lint_messages.sh` 同一個先例。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT

usage() {
  cat <<'USAGE'
用法：script/lint_exceptions.sh [<path>...]

  <path>  要檢查的 .py 檔或目錄（可多個）。不帶參數時掃 src/config_manager。

  fail  except <具名例外>: 之後只有 pass
  fail  except <具名例外>: 之後只有一個 *.debug(...) 呼叫
  fail  contextlib.suppress(...)（含 from contextlib import suppress 的裸 suppress）

bare except（E722）與 except Exception／BaseException（BLE001）由 ruff／pylint 擋，這支不重覆。
「handler 裡有實質處置」不判——那留給 code review（§0.4）。
USAGE
}

main() {
  case "${1:-}" in -h | --help)
    usage
    return 0
    ;;
  esac

  # python3 缺席時大聲失敗。一支因為直譯器不在而回 0 的 lint，就是不變式 2 禁止的靜默通過
  # ——那個形狀在這個 repo 已經被抓過。
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'lint_exceptions: python3 不在 PATH 上，所以一個 handler 都沒有被檢查\n' >&2
    printf 'lint_exceptions: 下一步：改用 ./script/test.sh，它在帶了工具的映像裡跑\n' >&2
    return 1
  fi

  local -a targets=("$@")
  if ((${#targets[@]} == 0)); then
    targets=("${REPO_ROOT}/src/config_manager")
  fi

  python3 - "${targets[@]}" <<'PY'
import ast
import pathlib
import sys

targets = [pathlib.Path(arg) for arg in sys.argv[1:]]


def files_of(target):
    """一個路徑底下要檢查的 .py 檔。檔案回它自己，目錄回遞迴的 *.py，都不存在則大聲失敗。"""
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(target.rglob("*.py"))
    print(
        f"lint_exceptions: 找不到要檢查的路徑：{target}。"
        f"下一步：給一個存在的 .py 檔或目錄，或不帶參數掃 src/config_manager",
        file=sys.stderr,
    )
    sys.exit(2)


def exception_names(type_node):
    """except 子句的例外名，供訊息指名是哪個。tuple 逐個列出。"""
    if isinstance(type_node, ast.Name):
        return type_node.id
    if isinstance(type_node, ast.Attribute):
        return type_node.attr
    if isinstance(type_node, ast.Tuple):
        return "、".join(exception_names(element) for element in type_node.elts)
    return ast.dump(type_node)


def swallow_kind(body):
    """handler 的 body 是不是「只吞掉」：只有 pass，或只有一個 *.debug(...) 呼叫。都不是回 None。"""
    if len(body) != 1:
        return None
    statement = body[0]
    if isinstance(statement, ast.Pass):
        return "只有 pass"
    if (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and statement.value.func.attr == "debug"
    ):
        return "只有一個 .debug(...) 呼叫"
    return None


def imports_bare_suppress(tree):
    """檔案裡有沒有 `from contextlib import suppress`——有的話裸的 suppress(...) 才算它。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "contextlib":
            if any(alias.name == "suppress" for alias in node.names):
                return True
    return False


def is_suppress_call(call, bare_suppress):
    """這個呼叫是不是 contextlib.suppress(...)。`x.suppress(...)` 一律算；裸 `suppress(...)`
    只在檔案有 `from contextlib import suppress` 時算，避免把某個同名的區域函式誤判。"""
    if isinstance(call.func, ast.Attribute) and call.func.attr == "suppress":
        return True
    return isinstance(call.func, ast.Name) and call.func.id == "suppress" and bare_suppress


violations = []
for target in targets:
    for path in files_of(target):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError) as error:
            print(
                f"lint_exceptions: 讀不了或解析不了 {path}：{error}。"
                f"下一步：確認它是合法的 UTF-8 Python 檔",
                file=sys.stderr,
            )
            sys.exit(2)

        bare_suppress = imports_bare_suppress(tree)
        for node in ast.walk(tree):
            # bare except（type is None）留給 E722；except Exception／BaseException 留給 BLE001。
            # 這支只補「具名例外之後只吞掉」這個既有工具看不到的缺口。
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                if exception_names(node.type) in ("Exception", "BaseException"):
                    continue
                kind = swallow_kind(node.body)
                if kind:
                    names = exception_names(node.type)
                    violations.append(
                        (path, node.lineno, f"except {names} 之後{kind}——吞掉具名例外")
                    )
            if isinstance(node, ast.Call) and is_suppress_call(node, bare_suppress):
                violations.append(
                    (path, node.lineno, "contextlib.suppress(...) 吞掉例外")
                )

for path, lineno, reason in sorted(violations, key=lambda item: (str(item[0]), item[1])):
    print(f"FAIL {path}:{lineno}  {reason}（§0.4 第 1 條）", file=sys.stderr)

if violations:
    print(
        f"lint_exceptions: {len(violations)} 處捕捉具名例外之後只吞掉。"
        f"下一步：改成處置它，或 raise 出去——捕捉即代表有處理策略，否則應向上拋出（§0.4）",
        file=sys.stderr,
    )
    sys.exit(1)
PY
}

main "$@"
