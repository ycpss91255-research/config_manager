#!/usr/bin/env bash
#
# gh 的快照代理（#407 調整需求第 1 點 a）：merge job 合併前在**同一份現況資料**上重跑讀 PR 或 issue
# 資料的檢查。每一個物件（一個 PR、一張 issue、一條 gh api 的呼叫）只向 GitHub 讀一次，之後每一道
# 檢查讀到的都是那一份——判定途中描述被改了，也不會出現「帳本驗 A、milestone 驗 B」。
#
# 各道檢查不必改：它們照常呼叫 gh，PATH 上的第一個 gh 是這裡放的那一支，由它回答。只回答讀取：
#
#   gh pr view <N> --json <欄位> [--jq .<欄位>]      PR 以 PR_FIELDS 整份讀一次，再挑出要的欄位
#   gh issue view <N> --json <欄位> [--jq .<欄位>]   issue 以 ISSUE_FIELDS 整份讀一次
#   gh api …                                          照整串參數讀一次
#
# 讀不到（gh 非零結束）也照樣記下，之後每一次都回同一個錯誤。要的欄位不在整份裡、--jq 不是單一欄位、
# 或任何會寫入的呼叫，一律失敗——快照只負責「讀過什麼」，寫入由 merge job 直接呼叫真的 gh。
#
# 讀過的每一個物件留在 <快照目錄>/objects/，merge job 從那裡印出這份資料的識別。
set -euo pipefail

usage() {
  cat <<'USAGE'
用法：script/gh_snapshot.sh init <快照目錄>
      script/gh_snapshot.sh run <快照目錄> -- <指令> [<參數>…]

  init  建好快照目錄與代理用的 gh（<快照目錄>/bin/gh）；真的 gh 取 PATH 上現在找到的那一支
  run   需要時先 init，再把 <快照目錄>/bin 放在 PATH 最前面執行 <指令>

  exit 2  用法不對，或 PATH 上找不到真的 gh
USAGE
}

_init() {
  local dir="$1" here real
  here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
  mkdir -p "${dir}/bin" "${dir}/objects"
  [[ -x "${dir}/bin/gh" ]] && return 0
  real="$(command -v gh || true)"
  if [[ -z "${real}" ]]; then
    printf 'gh_snapshot: PATH 上找不到 gh，快照沒有東西可以讀\n' >&2
    printf 'gh_snapshot: 下一步：在有 gh 的 runner 上執行（GitHub-hosted runner 內建 gh）\n' >&2
    exit 2
  fi
  {
    printf '#!/usr/bin/env bash\n'
    printf 'CM_GH_SNAPSHOT=%q CM_GH_REAL=%q exec %q serve "$@"\n' "${dir}" "${real}" "${here}/gh_snapshot.sh"
  } >"${dir}/bin/gh"
  chmod +x "${dir}/bin/gh"
}

_serve() {
  python3 - "$@" <<'PY'
import hashlib
import json
import os
import subprocess
import sys

args = sys.argv[1:]
root = os.path.join(os.environ["CM_GH_SNAPSHOT"], "objects")
real = os.environ["CM_GH_REAL"]
PR_FIELDS = ("number,title,body,state,isDraft,baseRefName,headRefName,headRefOid,isCrossRepository,"
             "closingIssuesReferences,updatedAt,mergeStateStatus,mergedAt,mergedBy,mergeCommit,url")
ISSUE_FIELDS = "number,title,body,state,stateReason,milestone,updatedAt,createdAt,comments,url"


def refuse(message):
    print(f"gh_snapshot: {message}", file=sys.stderr)
    print("gh_snapshot: 下一步：快照只回答 pr view、issue view 與 gh api 的讀取；這道檢查要的東西得加進 script/gh_snapshot.sh", file=sys.stderr)
    sys.exit(64)


def fetch(key, command):
    """讀一次、記下來；之後同一個 key 回同一份（含錯誤）。"""
    path = os.path.join(root, key + ".json")
    if not os.path.exists(path):
        completed = subprocess.run([real, *command], capture_output=True, text=True, check=False)
        record = {"command": command, "code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def option(name):
    if name in args:
        index = args.index(name)
        if index + 1 < len(args):
            return args[index + 1]
        refuse(f"{name} 後面少了值")
    return None


if len(args) >= 3 and args[0] in ("pr", "issue") and args[1] == "view":
    kind, number = args[0], args[2]
    wanted, jq = option("--json"), option("--jq")
    if wanted is None:
        refuse(f"gh {kind} view 沒有帶 --json，快照只回答 JSON 的讀取")
    record = fetch(f"{kind}-{number}", [kind, "view", number, "--json", PR_FIELDS if kind == "pr" else ISSUE_FIELDS])
    if record["code"] != 0:
        sys.stderr.write(record["stderr"])
        sys.exit(record["code"])
    whole = json.loads(record["stdout"])
    missing = [field for field in wanted.split(",") if field not in whole]
    if missing:
        refuse(f"gh {kind} view 要的欄位 {','.join(missing)} 不在快照讀的那一份裡")
    picked = {field: whole[field] for field in wanted.split(",")}
    if jq is None:
        print(json.dumps(picked, ensure_ascii=False))
    elif jq.startswith(".") and jq[1:] in picked:
        value = picked[jq[1:]]
        print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    else:
        refuse(f"--jq {jq} 不是單一欄位，快照不模擬 jq")
elif args and args[0] == "api":
    record = fetch("api-" + hashlib.sha256("\0".join(args).encode("utf-8")).hexdigest()[:16], args)
    sys.stdout.write(record["stdout"])
    sys.stderr.write(record["stderr"])
    sys.exit(record["code"])
else:
    refuse(f"不回答這個呼叫：gh {' '.join(args)}")
PY
}

main() {
  case "${1:-}" in
    -h | --help)
      usage
      return 0
      ;;
    init)
      [[ $# -eq 2 ]] || { usage >&2; return 2; }
      _init "$2"
      ;;
    run)
      if [[ $# -lt 4 || "$3" != -- ]]; then
        usage >&2
        return 2
      fi
      local dir="$2"
      shift 3
      _init "${dir}"
      PATH="${dir}/bin:${PATH}" exec "$@"
      ;;
    serve)
      shift
      _serve "$@"
      ;;
    *)
      usage >&2
      return 2
      ;;
  esac
}

main "$@"
