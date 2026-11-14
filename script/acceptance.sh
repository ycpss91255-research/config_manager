#!/usr/bin/env bash
#
# 一個版本的驗收檢查點，逐條**跑出來**的判定。
#
# 「v0.1.0 通過了」在這支腳本之前只存在於一段散文裡，而**無法複驗，就等於無法證明**
# （#148）。一份手寫的「已通過」表格，與這個 repo 已經抓到八次的「宣稱有檢查其實沒
# 檢查」是同一種東西：兩者在紙上長得一模一樣。
#
# 所以這裡沒有任何一條判定是寫下來的。對照表（doc/acceptance-checkpoints.toml）只說
# 「設計 §8.2 的哪一句該由哪些規格驗」；每一條檢查點的通過與否，是把那些規格真的跑
# 一次得到的。
#
# ## 三種說謊的方式，三種都要擋
#
#   1. **把「沒有人在驗」報成通過。** 一條對不到任何規格的檢查點判定為**未涵蓋**，
#      而未涵蓋讓整份報表非零結束。這是整支腳本存在的理由：沒有人在驗的檢查點，
#      在散文裡與驗過的檢查點長得完全一樣。
#   2. **把「指到的規格不存在」安靜跳過。** 跑任何東西之前先逐條解析：檔案要在、
#      測試名要收得到、至少要收到一條。有一條對不上就整份非零結束，一條判定都不印
#      ——一份對照表已經對不上的報表，它印出來的「通過」沒有意義。
#      （`bats --filter` 對比不到任何測試的樣式回 0 並跑零條測試，那正是靜默通過。）
#   3. **一條規格轉紅時把不相干的檢查點一起拖下水。** 規格逐檢查點分開執行，
#      被報成未通過的只有真的轉紅的那一條——與 coverage_gate 的「指名的只有 core」
#      同一個保證。說不出是哪一條壞了的報表，修的時候只能靠猜。
#
# ## 未涵蓋與未通過分得開，但兩者都不是通過
#
# 要做的事不同：一個是補規格，一個是修程式。所以輸出上分開。結束碼上合併，因為
# 把「沒有人在驗」讀成「驗過了」正是這份報表要防的東西。
#
# ## 這支不由 script/test.sh 執行，也還沒進 CI 的必要檢查
#
# 現在跑 v0.1.0 一定是紅的：檢查點 3（修改清單檔後寫出）沒有規格對得上，因為
# core/config_list.dump 尚不支援改動與移除既有條目（#149 在修）。把一個**已知會紅**
# 的閘門接成必要檢查，得到的是一個所有人都學會忽略的紅燈。等 #149 落地、v0.1.0 真的
# 轉綠之後再接線，那時候它的紅燈才有意義。
#
# 它仍然是「可接進 CI」的：一個參數、零或非零結束碼、逐條的判定印在 stdout。
#
# ## 為什麼自己轉進容器
#
# 規格要跑，而跑得動規格的東西（pytest、bats、Chromium）只在檢查映像裡（ADR-00000027）。
# 主機不是這個專案的證據——它在這裡已經給過四個錯誤答案。轉進的方式與 script/test.sh
# 逐項相同：同一份映像、同一組 --user 與 HOME、同一個逃生口。
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly REPO_ROOT
readonly TEST_IMAGE="config_manager-test-tools:local"
readonly TEST_DOCKERFILE="docker/Dockerfile.test-tools"

# 兩份輸入的位置可以被覆寫，這樣這支腳本自己也測得到：規格餵給它一份手寫的對照表與
# 一組替身規格，看它判定成什麼、指名了哪一條。少了這個，唯一能驗證這份報表的方法是
# 把真的檢查點弄壞一次——而它的規格會在別人修好 dump 的那天無故轉紅。
# 與 coverage_gate.sh 的 CM_COVERAGE_JSON 同一個先例。
readonly TABLE="${CM_ACCEPTANCE_MAP:-${REPO_ROOT}/doc/acceptance-checkpoints.toml}"
readonly ROOT="${CM_ACCEPTANCE_ROOT:-${REPO_ROOT}}"

usage() {
  cat <<'USAGE'
用法：script/acceptance.sh [--gate overall|automated] <milestone>
      script/acceptance.sh [--gate overall|automated] --target

  <milestone>  對照表裡的一個 id，例如 v0.1.0、v0.5.0、core-flow
  --target     跑對照表頂層 target 指的那一版（目前的目標版本）

逐條執行 doc/acceptance-checkpoints.toml 為該版本列出的規格，印出每條驗收檢查點
的判定。報表每次都分三段：自動檢查、人工待驗清單、整體。

  通過      該條檢查點對到的規格全數通過
  未通過    對到的規格至少有一條轉紅
  未涵蓋    對不到任何規格，也不是人工檢查點——**不是通過**
  人工待驗  manual = true 的檢查點，還沒有簽核——**不是通過**
  已簽核    manual = true 的檢查點，有格式合格的簽核（格式見 doc/ACCEPTANCE-SIGNOFF.md）

  --gate overall    （預設）整體通過才以 0 結束：自動檢查全部通過、沒有未涵蓋、
                    人工檢查點全部已簽核。script/release.sh 用這個
  --gate automated  自動檢查全部通過、沒有未涵蓋就以 0 結束，人工待驗不影響結束碼。
                    main 上的 acceptance job 用這個；兩種閘門印出的報表相同

  exit 0   照所選的閘門通過
  exit 1   照所選的閘門未通過
  exit 2   對照表本身有問題（找不到、解析不了、指到不存在的規格、milestone 不存在、
           簽核格式不對、target 沒寫或不在對照表裡），或 --target 的目標已整體通過、
           對照表裡之後還有版本（目標該推進了）

判定是跑出來的，不是寫下來的。對照表只說「§8.2 的哪一句該由誰驗」。

  CM_ACCEPTANCE_MAP    改讀這份對照表
  CM_ACCEPTANCE_ROOT   規格參照以這個目錄為根
兩者供 test/bats/unit/acceptance.bats 使用，且只在映像內（或 CM_TEST_LOCAL=1）有效。
USAGE
}

# 轉進裝了工具的映像裡重跑，除非已經在裡面了。與 script/test.sh 同一份映像、同一條
# 路徑：一項檢查不可能在一邊過、在另一邊掛。
dispatch_to_container() {
  if [[ -n "${CM_ACCEPTANCE_MAP:-}${CM_ACCEPTANCE_ROOT:-}" ]]; then
    printf 'acceptance: CM_ACCEPTANCE_MAP／CM_ACCEPTANCE_ROOT 指的是主機上的路徑，容器裡沒有那些檔案\n' >&2
    printf 'acceptance: 下一步：加上 CM_TEST_LOCAL=1 就地執行，或改用映像內的路徑\n' >&2
    exit 2
  fi

  if ! command -v docker >/dev/null 2>&1; then
    printf 'acceptance: docker 不在 PATH 上，規格沒有地方可以跑\n' >&2
    printf 'acceptance: 下一步：安裝 docker，或設 CM_TEST_LOCAL=1 以主機上現有的工具執行\n' >&2
    exit 2
  fi

  # 進度往 stdout：fd 2 是錯誤的出口，而建置進度不是錯誤（#108，同 test.sh）。
  printf 'acceptance: 建置 %s\n' "${TEST_IMAGE}"
  local -a build=(docker build --quiet -f "${REPO_ROOT}/${TEST_DOCKERFILE}" -t "${TEST_IMAGE}")
  [[ -n "${CM_APT_MIRROR:-}" ]] && build+=(--build-arg "APT_MIRROR=${CM_APT_MIRROR}")
  "${build[@]}" "${REPO_ROOT}" >/dev/null

  exec docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --env GIT_CONFIG_COUNT=1 \
    --env GIT_CONFIG_KEY_0=safe.directory \
    --env GIT_CONFIG_VALUE_0='*' \
    --volume "${REPO_ROOT}:/repo" \
    --workdir /repo \
    "${TEST_IMAGE}" \
    ./script/acceptance.sh "$@"
}

main() {
  local -a original=("$@")
  local gate="overall" target=0 milestone=""
  while (($# > 0)); do
    case "$1" in
      -h | --help)
        usage
        return 0
        ;;
      --gate)
        if [[ $# -lt 2 || ( "$2" != overall && "$2" != automated ) ]]; then
          printf 'acceptance: --gate 只認 overall 與 automated，收到的是 %s\n' "${2:-（沒有值）}" >&2
          printf 'acceptance: 下一步：發版用 --gate overall（預設），main 上的 job 用 --gate automated\n' >&2
          return 2
        fi
        gate="$2"
        shift 2
        ;;
      --target)
        target=1
        shift
        ;;
      -*)
        usage >&2
        return 2
        ;;
      *)
        if [[ -n "${milestone}" ]]; then
          usage >&2
          return 2
        fi
        milestone="$1"
        shift
        ;;
    esac
  done

  # 不預設跑某一版。一份不知道自己在報哪一版的報表，讀者無從複驗。--target 不是預設：
  # 它讀的是對照表裡明寫的那一版。兩者都給，說不清楚要跑哪一版。
  if [[ -n "${milestone}" && ${target} -eq 1 ]] || [[ -z "${milestone}" && ${target} -eq 0 ]]; then
    usage >&2
    return 2
  fi

  if [[ "${CM_IN_TEST_IMAGE:-}" != "1" && "${CM_TEST_LOCAL:-}" != "1" ]]; then
    dispatch_to_container "${original[@]}"
  fi

  if ! command -v python3 >/dev/null 2>&1; then
    printf 'acceptance: python3 不在 PATH 上，對照表沒有東西讀得了\n' >&2
    printf 'acceptance: 下一步：改用 ./script/acceptance.sh 的預設路徑，它在帶了工具的映像裡跑\n' >&2
    return 2
  fi

  python3 - "${milestone}" "${TABLE}" "${ROOT}" "${gate}" "${target}" <<'PY'
import datetime
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

MILESTONE, TABLE, ROOT = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
GATE, USE_TARGET = sys.argv[4], sys.argv[5] == "1"

PASSED, FAILED, UNCOVERED = "通過  ", "未通過", "未涵蓋"
MANUAL_PENDING, SIGNED = "人工待驗", "已簽核"

# 簽核的證據要指到這個 repo 的 issue 或 PR（可以帶 #issuecomment-… 之類的錨點）。只核對形狀，
# 不核對那個網址的內容真的是這一條的證據——那一半要人看（doc/ACCEPTANCE-SIGNOFF.md）。
EVIDENCE = re.compile(
    r"^https://github\.com/ycpss91255-research/config_manager/(issues|pull)/[1-9][0-9]*(#[A-Za-z0-9_-]+)?$"
)
VERSION = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")

# 對照表壞掉與檢查點沒過是兩種不同的失敗，結束碼也分開：前者代表這份報表本身不能信，
# 後者代表這一版還沒做完。混成同一個碼，CI 上就分不出「規格指錯了」與「功能沒做完」。
BROKEN_TABLE = 2
NOT_PASSED = 1


def die(*lines):
    for line in lines:
        print(line, file=sys.stderr)
    sys.exit(BROKEN_TABLE)


def load_table():
    if not TABLE.exists():
        die(
            f"acceptance: 找不到對照表 {TABLE}",
            "acceptance: 沒有對照表不是「零條檢查點全部通過」，是這份報表無從產生",
            "acceptance: 下一步：確認 doc/acceptance-checkpoints.toml 在，或以 CM_ACCEPTANCE_MAP 指到別份",
        )
    try:
        return tomllib.loads(TABLE.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        die(
            f"acceptance: 對照表解析不了：{TABLE}",
            f"acceptance: {error}",
            "acceptance: 下一步：修正該檔的 TOML 語法。解析不了的對照表不會被當成空的",
        )
    return None


def pick_target(table):
    """--target：對照表頂層 target 指的那一版。來源只有這一個，不查 GitHub 的 milestone。"""
    target = table.get("target")
    if not isinstance(target, str) or not target:
        die(
            f"acceptance: 對照表 {TABLE} 沒有寫頂層的 target，--target 不知道要跑哪一版",
            "acceptance: 下一步：在對照表第一個 [[milestone]] 之前寫 target = 'vX.Y.Z'，"
            "或改成直接給 milestone 的 id",
        )
    return target


def later_versions(table):
    """對照表裡版號比目前這一版大的 milestone，由小到大。core-flow 這種不是版本，不算。"""
    current = VERSION.match(MILESTONE)
    if current is None:
        return []
    key = tuple(int(part) for part in current.groups())
    found = []
    for entry in table.get("milestone", []):
        match = VERSION.match(str(entry.get("id", "")))
        if match is not None and tuple(int(part) for part in match.groups()) > key:
            found.append((tuple(int(part) for part in match.groups()), entry["id"]))
    return [name for _, name in sorted(found)]


def is_manual(item):
    return item.get("manual") is True


def signoff_problems(signoff):
    """簽核的每一欄對不對。回傳說得出是哪一欄、要怎麼寫的句子；空的就是合格。"""
    if not isinstance(signoff, dict):
        return ["signoff：要是一張表（[milestone.checkpoint.signoff]）"]
    problems = []
    for field in ("by", "summary"):
        value = signoff.get(field)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{field}：缺少或是空的")
    # datetime 是 date 的子類別，所以比的是確切型別：簽核記的是驗收完成的那一天。
    if type(signoff.get("date")) is not datetime.date:
        problems.append("date：要寫 TOML 的日期，例如 2026-12-01（不加引號、不帶時間）")
    evidence = signoff.get("evidence")
    if not isinstance(evidence, str) or EVIDENCE.match(evidence) is None:
        problems.append(
            "evidence：要是這個 repo 的 issue 或 PR 網址，例如 "
            "https://github.com/ycpss91255-research/config_manager/issues/123#issuecomment-456"
        )
    return problems


def pick_milestone(table):
    milestones = table.get("milestone", [])
    for entry in milestones:
        if entry.get("id") == MILESTONE:
            return entry
    known = "、".join(str(entry.get("id")) for entry in milestones) or "（一個都沒有）"
    die(
        f"acceptance: 對照表 {TABLE} 裡沒有 milestone {MILESTONE}",
        f"acceptance: 對照表裡有這些：{known}",
        "acceptance: 下一步：改用其中一個，或在對照表裡補上這一版的檢查點。"
        "一個打錯的版號不會得到一份綠色的空報表",
    )
    return None


def checkpoints_of(milestone):
    items = milestone.get("checkpoint", [])
    if not items:
        die(
            f"acceptance: milestone {MILESTONE} 在對照表 {TABLE} 裡一條檢查點都沒有",
            "acceptance: 下一步：補上 [[milestone.checkpoint]]。零條檢查點不是全部通過",
        )
    for item in items:
        number = item.get("number")
        if item.get("specs") and item.get("uncovered"):
            die(
                f"acceptance: 檢查點 {number} 同時寫了 specs 與 uncovered，"
                f"對照表 {TABLE} 說不清楚它到底有沒有人驗",
                "acceptance: 下一步：有規格就拿掉 uncovered，沒有就把 specs 清空",
            )
        if item.get("specs") and is_manual(item):
            die(
                f"acceptance: 檢查點 {number} 同時寫了 specs 與 manual = true，"
                f"對照表 {TABLE} 說不清楚它由規格驗還是由人驗",
                "acceptance: 下一步：由規格驗就拿掉 manual，只能由真人驗收就把 specs 清空",
            )
        if "signoff" not in item:
            continue
        if not is_manual(item):
            die(
                f"acceptance: 檢查點 {number} 不是人工檢查點，卻寫了簽核（對照表 {TABLE}）",
                "acceptance: 簽核只用在 manual = true 的檢查點：它不能把規格變成通過，也不能取代沒做的驗收",
                "acceptance: 下一步：拿掉那段 [milestone.checkpoint.signoff]；"
                "真的只能由人驗收的，改成 manual = true 並把 specs 清空",
            )
        problems = signoff_problems(item["signoff"])
        if problems:
            die(
                f"acceptance: 檢查點 {number} 的簽核有 {len(problems)} 處不對（對照表 {TABLE}）：",
                *(f"  {problem}" for problem in problems),
                "acceptance: 下一步：照 doc/ACCEPTANCE-SIGNOFF.md 的格式補齊 by、date、evidence、summary 四欄",
            )
    return items


def split_ref(ref):
    path, separator, name = ref.partition("::")
    return path, (name if separator else None)


# ERE 的中繼字元，逐個跳脫。不用 re.escape：它跳脫的是 Python 的正規式方言，
# 而 bats --filter 交給的是 bash 的 =~，兩者對「跳脫一個普通字元」的處置不同。
_ERE_META = set(r".[]\()*+?{}|^$")


def as_exact_ere(name):
    return "^" + "".join("\\" + ch if ch in _ERE_META else ch for ch in name) + "$"


def run(command):
    return subprocess.run(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )


_collected = {}


def pytest_node_ids(relative):
    """一個 pytest 規格檔收得到的全部 node id。每個檔只問一次。"""
    if relative not in _collected:
        result = run(["pytest", "--collect-only", "-q", "-p", "no:cacheprovider", relative])
        ids = [line.strip() for line in result.stdout.splitlines() if "::" in line]
        _collected[relative] = (result.returncode, ids, result.stdout)
    return _collected[relative]


def resolve(ref):
    """這條參照指得到東西嗎。指不到就回一句說得出下一步的話。"""
    relative, name = split_ref(ref)
    if not (ROOT / relative).is_file():
        return f"{ref} —— 檔案不存在（{ROOT / relative}）"

    if relative.endswith(".py"):
        if shutil.which("pytest") is None:
            return f"{ref} —— pytest 不在 PATH 上，這條規格沒有東西跑得動它"
        code, ids, output = pytest_node_ids(relative)
        if code != 0 and not ids:
            return f"{ref} —— pytest 收不了這個檔：\n{indent(output)}"
        if name is None:
            return None if ids else f"{ref} —— 這個檔一條規格都收不到"
        wanted = f"{relative}::{name}"
        if any(one == wanted or one.startswith(wanted + "[") for one in ids):
            return None
        return f"{ref} —— pytest 收不到這條規格名"

    if relative.endswith(".bats"):
        if shutil.which("bats") is None:
            return f"{ref} —— bats 不在 PATH 上，這條規格沒有東西跑得動它"
        command = ["bats", "--count"]
        if name is not None:
            command += ["--filter", as_exact_ere(name)]
        result = run(command + [relative])
        count = result.stdout.strip().splitlines()[-1:] or ["0"]
        if result.returncode != 0 or not count[0].isdigit() or int(count[0]) == 0:
            return f"{ref} —— bats 比不到任何測試（比不到不是通過）"
        return None

    return f"{ref} —— 認不得的規格參照，副檔名只認 .py 與 .bats"


def indent(text):
    return "\n".join("      " + line for line in text.rstrip().splitlines())


def execute(refs):
    """一條檢查點的全部規格。回 (過了沒有, 輸出)。"""
    node_ids = [ref for ref in refs if split_ref(ref)[0].endswith(".py")]
    passed, chunks = True, []

    if node_ids:
        result = run(["pytest", "-p", "no:cacheprovider", *node_ids])
        passed = passed and result.returncode == 0
        if result.returncode != 0:
            chunks.append(result.stdout)

    for ref in refs:
        relative, name = split_ref(ref)
        if not relative.endswith(".bats"):
            continue
        command = ["bats"]
        if name is not None:
            command += ["--filter", as_exact_ere(name)]
        result = run(command + [relative])
        passed = passed and result.returncode == 0
        if result.returncode != 0:
            chunks.append(result.stdout)

    return passed, "\n".join(chunks)


def main():
    global MILESTONE
    table = load_table()
    if USE_TARGET:
        MILESTONE = pick_target(table)
    milestone = pick_milestone(table)
    checkpoints = checkpoints_of(milestone)

    # 解析先於執行，而且一條不過就整份停下。理由不是效率：一份對照表已經對不上的
    # 報表，它印出來的「通過」沒有意義，而印出來的東西會被人拿去用。
    unresolved = []
    for checkpoint in checkpoints:
        for ref in checkpoint.get("specs", []):
            problem = resolve(ref)
            if problem is not None:
                unresolved.append(problem)
    if unresolved:
        print(f"acceptance: 對照表 {TABLE} 有 {len(unresolved)} 條規格參照指不到東西：", file=sys.stderr)
        for problem in unresolved:
            print(f"  {problem}", file=sys.stderr)
        print(
            "acceptance: 一條判定都沒有印——對照表對不上時，「通過」沒有意義。"
            "下一步：修正上面那些參照，或把規格補上",
            file=sys.stderr,
        )
        sys.exit(BROKEN_TABLE)

    print(
        f"acceptance: {MILESTONE} {milestone.get('title', '')} —— "
        f"{len(checkpoints)} 條檢查點，對照表 {TABLE}，閘門 --gate {GATE}"
    )

    automated = [checkpoint for checkpoint in checkpoints if not is_manual(checkpoint)]
    manual = [checkpoint for checkpoint in checkpoints if is_manual(checkpoint)]
    tally = {PASSED: 0, FAILED: 0, UNCOVERED: 0, MANUAL_PENDING: 0, SIGNED: 0}

    # 三段每次都印，即使某一段是空的：少了一段的報表，讀者分不出「沒有」與「沒印」。
    print("【自動檢查】")
    if not automated:
        print("  （這一版沒有自動檢查點）")
    for checkpoint in automated:
        number = checkpoint.get("number")
        refs = checkpoint.get("specs", [])

        if not refs:
            verdict, output = UNCOVERED, ""
        else:
            ok, output = execute(refs)
            verdict = PASSED if ok else FAILED
        tally[verdict] += 1

        print(f"  檢查點 {number}  {verdict}  {checkpoint.get('text', '')}")
        for ref in refs:
            print(f"      {ref}")
        if verdict == UNCOVERED:
            print("      沒有任何規格對到這條檢查點。未涵蓋不是通過")
            reason = checkpoint.get("uncovered")
            if reason:
                print(indent("理由：" + reason))
            else:
                print("      對照表也沒有寫下理由——沒寫理由的空格就是漏掉")
        if output:
            print(indent(output))

    print("【人工待驗清單】")
    if not manual:
        print("  （這一版沒有人工檢查點）")
    for checkpoint in manual:
        signoff = checkpoint.get("signoff")
        verdict = SIGNED if signoff is not None else MANUAL_PENDING
        tally[verdict] += 1
        print(f"  檢查點 {checkpoint.get('number')}  {verdict}  {checkpoint.get('text', '')}")
        if signoff is None:
            print("      等真人驗收；簽核格式見 doc/ACCEPTANCE-SIGNOFF.md。人工待驗不是通過")
            continue
        print(f"      簽核：{signoff['by']}，{signoff['date'].isoformat()}")
        print(f"      證據：{signoff['evidence']}")
        print(indent(signoff["summary"]))

    automated_ok = tally[FAILED] == 0 and tally[UNCOVERED] == 0
    overall_ok = automated_ok and tally[MANUAL_PENDING] == 0

    print("【整體】")
    print(
        f"acceptance: {MILESTONE} 自動檢查{'通過' if automated_ok else '未通過'}，"
        f"人工待驗 {tally[MANUAL_PENDING]} 條——整體{'通過' if overall_ok else '未通過'}"
    )
    # 這一行是報表裡最後一行帶「未涵蓋」的：script/release.sh 把它放進 release notes 的開頭。
    print(
        f"acceptance: {MILESTONE} —— "
        f"{tally[PASSED]} 通過／{tally[FAILED]} 未通過／{tally[UNCOVERED]} 未涵蓋／"
        f"{tally[MANUAL_PENDING]} 人工待驗／{tally[SIGNED]} 已簽核"
    )

    # 防呆：目標版本已經整體通過，而它之後還有版本——留在已完成的版本上，這個 job 會永遠
    # 報綠，與原本寫死 v0.1.0 是同一個問題。看的是整體，不是閘門：自動檢查綠了但人工還沒
    # 簽，這一版就還沒做完，不該被推走。
    if USE_TARGET and overall_ok:
        later = later_versions(table)
        if later:
            print(
                f"acceptance: 目標版本 {MILESTONE} 已整體通過，對照表裡之後還有 {'、'.join(later)}",
                file=sys.stderr,
            )
            print(
                f"acceptance: 下一步：把對照表頂層的 target 改成 {later[0]}",
                file=sys.stderr,
            )
            sys.exit(BROKEN_TABLE)

    passed = automated_ok if GATE == "automated" else overall_ok
    if not passed:
        print(f"acceptance: {MILESTONE} 未通過（--gate {GATE}）")
        sys.exit(NOT_PASSED)
    print(f"acceptance: {MILESTONE} 通過（--gate {GATE}）")


main()
PY
}

main "$@"
