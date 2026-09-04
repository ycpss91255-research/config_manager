#!/usr/bin/env bats
#
# 可部署的映像不得帶測試工具。
#
# 這條規則本來只寫在 `config/pip/requirements-dev.txt` 的註解裡，也就是說它不存在
# ——§0.4：「所有規範必須可由工具檢查，無法自動檢查的規範等同不存在」。而它是**很
# 容易誤觸**的一條：`runtime` 是 `FROM devel-base`，而 `devel-base` 裝的是
# `requirements.txt`，所以往那份檔案加一行 pytest，測試工具就跟著進了現場產物，
# 沒有任何東西會抱怨。
#
# 檢查的是**文字**而不是建好的映像，因為這一層要在 `just test` 裡跑，而那支腳本
# 自己就在容器裡，起不了另一個容器（見 doc/TEST-PLAN.md 的 T9）。
#
# 擋不住的那一件事寫在這裡，免得日後以為它被驗過：**沒有東西在斷言建好的 runtime
# 映像裡真的沒有 pytest。** 那需要對映像本身跑一則規格，而 `runtime-test` 是
# `FROM runtime` 再把測試工具疊上去的，站在那裡問「有沒有 pytest」永遠會得到「有」
# ——要問的位置是 `runtime` 自己，而現在沒有任何階段停在那裡做這件事。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  RUNTIME_DEPS="${REPO_ROOT}/config/pip/requirements.txt"
  DEV_DEPS="${REPO_ROOT}/config/pip/requirements-dev.txt"
}

# 不得出現在現場產物裡的套件名。比下面那份長：`coverage` 是 pytest-cov 帶進來的，
# 沒有人會直接寫它，但寫進 requirements.txt 一樣會上線。
test_tooling() {
  printf '%s\n' pytest pytest-cov coverage ruff mypy pylint playwright
}

# 這個 repo 直接指名的檢查工具。加一支新的時，這裡也要加一行——漏掉的失敗方式
# 是安靜的：它會裝在映像裡、跑得起來，但沒有任何東西保證它留在正確的那一份清單。
declared_tooling() {
  printf '%s\n' pytest pytest-cov ruff mypy pylint playwright
}

@test "現場產物的相依清單裡沒有任何測試工具" {
  local package
  while read -r package; do
    run grep -iqE "^[[:space:]]*${package}([[:space:]<>=!~]|$)" "${RUNTIME_DEPS}"
    if [ "${status}" -eq 0 ]; then
      printf 'requirements.txt 裡有測試工具 %s\n' "${package}" >&2
      printf 'runtime 是 FROM devel-base，而 devel-base 裝的就是這份——' >&2
      printf '所以它會跟著上線。下一步：移到 requirements-dev.txt\n' >&2
      return 1
    fi
  done < <(test_tooling)
}

@test "測試工具在開發相依清單裡，不是誰都沒裝" {
  # 上一則單獨存在時，把 requirements-dev.txt 清空也會綠。兩則一起才說得完整：
  # 那些工具存在，而且只存在於這一邊。
  local package
  while read -r package; do
    run grep -iqE "^[[:space:]]*${package}([[:space:]<>=!~]|$)" "${DEV_DEPS}"
    [ "${status}" -eq 0 ] || {
      printf '%s 不在 requirements-dev.txt 裡\n' "${package}" >&2
      return 1
    }
  done < <(declared_tooling)
}

@test "runtime 階段不安裝開發相依" {
  # `runtime` 與其後的 `-test` 階段共用同一份 Dockerfile。測試工具只能出現在
  # `FROM runtime` 之後的那些階段裡。
  run awk '
    /^FROM .* AS runtime$/        { inside = 1; next }
    /^FROM /                      { inside = 0 }
    inside && /requirements-dev/  { print NR ": " $0 }
  ' "${REPO_ROOT}/Dockerfile"

  [ -z "${output}" ]
}
