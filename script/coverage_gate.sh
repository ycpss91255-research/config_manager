#!/usr/bin/env bash
#
# 四個資料夾的覆蓋率門檻，**分開計算、分開擋**。
#
# 為什麼不是一個總數：一個門檻同時守兩個標準不同的區域，結果是守住比較鬆的那個。
# `core/` 97%、`api/` 0% 的那個當下，總覆蓋率是 77%——下限訂在 77，`core/` 掉到 78
# 也還是綠燈；訂在 85，則是 `core/` 一個人扛著另外三層。這不是把數字調對就能解決
# 的事，它是「一個數字」這個形狀本身的問題（#97）。
#
# 為什麼也不是四條不同的線：讓每一層各自訂一個「現況擋得住」的值，等於把現況封成
# 標準，而那些值之間的差距沒有理由，只有歷史。四個資料夾同一條線，各自算、各自擋。
#
# 閾值住在 pyproject.toml 的 [tool.config_manager.coverage]，不寫在這支腳本裡：
# §0.4 的執行方式那段要求「調整需修改設定檔並在 PR 中說明理由——使放寬本身成為
# 可見的決策」。設定檔的改動在 diff 上看得見，藏在腳本裡的一個數字不會。
#
# 數字從兩個來源進來，因為那是兩種語言：
#
#   core / io / api   pytest-cov 留下的 .coverage，經 `coverage json` 取出逐檔數字
#   web               test/pytest/system/test_web.py 寫出的 .coverage-web.json，
#                     內容是 Chromium 的 V8 精確覆蓋率翻成的行覆蓋率
#
# **「報告不見了」與「覆蓋率是 0」是兩種不同的失敗。** 前者代表那一層根本沒有被量到
# ——量測本身壞掉，而它看起來會很像「還沒寫測試」。兩者的訊息分開寫，因為要做的事
# 不一樣。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
# 兩份輸入的位置可以被覆寫，這樣這支腳本自己也測得到：規格餵給它一組手寫的
# 數字，看它擋不擋、指名的是哪一層。少了這個，唯一能驗證閘門的方法是把真的
# 覆蓋率弄壞一次——而那正是「設定存在不等於它在守」被抓到七次的那個位置。
readonly WEB_REPORT="${CM_WEB_COVERAGE:-${REPO_ROOT}/.coverage-web.json}"
readonly DATA_FILE="${REPO_ROOT}/.coverage"
readonly COVERAGE_JSON="${CM_COVERAGE_JSON:-}"

usage() {
  cat <<'USAGE'
用法：script/coverage_gate.sh [--summary <目錄>]

  fail  core / io / api / web 之中任何一個低於它自己的下限
  fail  某一層的覆蓋率報告不存在——那一層從頭到尾沒有被量到

  --summary <目錄>  判完之後把四層的數字寫進這個目錄，過不過都寫：
                    coverage.md   給人看的表格（CI 放進 Job Summary）
                    coverage.tsv  給 script/coverage_badge.sh 讀，每層一行：
                                  層名、執行過的行、總行數、下限、判定（pass／below／unmeasured）
                    判定用原始數值，表格上的兩位小數只是顯示。

閾值在 pyproject.toml 的 [tool.config_manager.coverage]。

這支腳本只讀上一次測試留下來的東西，自己不跑測試：
  pytest test/pytest --cov=src/config_manager

  CM_COVERAGE_JSON   改讀這份已經產好的 coverage json，不自己跑 coverage
  CM_WEB_COVERAGE    改讀這份 web 覆蓋率報告
兩者供 test/bats/unit/coverage_gate.bats 使用。
USAGE
}

main() {
  local summary_dir=""
  while (($# > 0)); do
    case "$1" in
      -h | --help)
        usage
        return 0
        ;;
      --summary)
        if [[ $# -lt 2 || -z "$2" ]]; then
          printf 'coverage_gate: --summary 後面要接一個目錄\n' >&2
          printf 'coverage_gate: 下一步：改成 --summary <目錄>，例如 --summary coverage/summary\n' >&2
          return 2
        fi
        summary_dir="$2"
        shift 2
        ;;
      *)
        printf 'coverage_gate: 不認得的參數 %s\n' "$1" >&2
        printf 'coverage_gate: 下一步：只接受 --summary <目錄>；用 --help 看用法\n' >&2
        return 2
        ;;
    esac
  done

  cd "${REPO_ROOT}"

  # 工具缺席時大聲失敗。一支因為工具不在而回 0 的閘門，就是不變式 2 禁止的靜默
  # 通過——那個形狀在這個 repo 已經讓 hadolint 連綠六次、CI 連紅六次。
  local python_report
  if [[ -n "${COVERAGE_JSON}" ]]; then
    python_report="${COVERAGE_JSON}"
  else
    local tool
    for tool in python3 coverage; do
      if ! command -v "${tool}" >/dev/null 2>&1; then
        printf 'coverage_gate: %s 不在 PATH 上，四個門檻一個都沒有被檢查。\n' "${tool}" >&2
        printf 'coverage_gate: 下一步：改用 ./script/test.sh，它在帶了工具的映像裡跑\n' >&2
        return 1
      fi
    done

    if [[ ! -f "${DATA_FILE}" ]]; then
      printf 'coverage_gate: 找不到 .coverage，core／io／api 三層沒有被量到。\n' >&2
      printf 'coverage_gate: 下一步：先跑 pytest test/pytest --cov=src/config_manager\n' >&2
      return 1
    fi

    python_report="$(mktemp)"
    trap 'rm -f "${python_report}"' RETURN
    coverage json -o "${python_report}" --quiet
  fi

  python3 - "${python_report}" "${WEB_REPORT}" "${summary_dir}" <<'PY'
import json
import pathlib
import sys
import tomllib

PACKAGE = "src/config_manager"
AREAS = ("core", "io", "api", "web")

python_report = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
web_report = pathlib.Path(sys.argv[2])
summary_dir = sys.argv[3]

settings = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
thresholds = settings["tool"]["config_manager"]["coverage"]


def python_area(name):
    """一個資料夾的 (執行過的行, 陳述句總數)。

    逐檔加總，不讀 coverage 自己的 totals——那份是整包的，而這裡要的正是把它拆開。
    """
    prefix = f"{PACKAGE}/{name}/"
    covered = statements = 0
    for path, entry in python_report["files"].items():
        if not path.replace("\\", "/").startswith(prefix):
            continue
        covered += entry["summary"]["covered_lines"]
        statements += entry["summary"]["num_statements"]
    return covered, statements


def web_area():
    if not web_report.exists():
        return None
    report = json.loads(web_report.read_text(encoding="utf-8"))
    return report["covered_lines"], report["code_lines"]


rows, below, unmeasured = [], [], []
# summary 用的同一份判定：每層 (層名, 執行過的行, 總行數, 下限, 判定)。表格與 tsv 都從這裡寫，
# 不另外算一次——另算一次的數字遲早會與結束碼說的不是同一件事。
records = []
for name in AREAS:
    floor = float(thresholds[name])
    measured = web_area() if name == "web" else python_area(name)

    if measured is None:
        unmeasured.append((name, "報告不存在"))
        rows.append((name, "報告不存在", floor, "沒量到"))
        records.append((name, None, None, floor, "unmeasured"))
        continue
    if measured[1] == 0:
        unmeasured.append((name, "報告裡一行可量的都沒有"))
        rows.append((name, "報告裡沒有可量的行", floor, "沒量到"))
        records.append((name, None, None, floor, "unmeasured"))
        continue

    covered, total = measured
    value = 100.0 * covered / total
    # 用原始數值判，不用顯示出來的兩位小數：84.996% 顯示成 85.00%，但它沒有達標。
    passed = value >= floor
    if not passed:
        below.append(name)
    rows.append((name, f"{value:.2f}%  ({covered}/{total})", floor, "通過" if passed else "未達"))
    records.append((name, covered, total, floor, "pass" if passed else "below"))

width = max(len(row[1]) for row in rows)
print("coverage_gate: 四個資料夾各自計算、各自擋")
for name, value, floor, verdict in rows:
    print(f"  {name:<5} {value:<{width}}  下限 {floor:g}%  {verdict}")

for name, reason in unmeasured:
    print(
        f"coverage_gate: {name}/ {reason}，那一層從頭到尾沒有被量到。"
        f"這與「覆蓋率是 0」不同——是量測本身沒有跑。"
        f"下一步：確認 test/pytest 底下該層的規格真的執行了",
        file=sys.stderr,
    )
for name in below:
    print(
        f"coverage_gate: {name}/ 低於它自己的下限 {thresholds[name]}%。"
        f"下一步：補上沒被執行到的分支，或改程式碼讓它可測"
        f"——不要調低 pyproject.toml 裡的閾值，那條線是刻意拉的",
        file=sys.stderr,
    )

if summary_dir:
    VERDICT = {"pass": "通過", "below": "未達", "unmeasured": "沒量到"}
    out = pathlib.Path(summary_dir)
    out.mkdir(parents=True, exist_ok=True)
    table = [
        "### 覆蓋率：四層各自計算、各自擋",
        "",
        "| 層 | 覆蓋率 | 執行過／總行數 | 下限 | 判定 |",
        "|---|---|---|---|---|",
    ]
    tsv = []
    for name, covered, total, floor, state in records:
        if state == "unmeasured":
            table.append(f"| {name} | 沒量到 | - | {floor:g}% | {VERDICT[state]} |")
            tsv.append(f"{name}\t-\t-\t{floor:g}\t{state}")
            continue
        table.append(
            f"| {name} | {100.0 * covered / total:.2f}% | {covered}/{total} | {floor:g}% | {VERDICT[state]} |"
        )
        tsv.append(f"{name}\t{covered}\t{total}\t{floor:g}\t{state}")
    failed = [name for name, *_, state in records if state != "pass"]
    table.append("")
    table.append(
        f"未達或沒量到：{'、'.join(failed)}。" if failed else "四層都達標。"
    )
    table.append("判定用原始數值，表格上的兩位小數只是顯示。")
    (out / "coverage.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    (out / "coverage.tsv").write_text("\n".join(tsv) + "\n", encoding="utf-8")

sys.exit(1 if (unmeasured or below) else 0)
PY
}

main "$@"
