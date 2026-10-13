#!/usr/bin/env bats
#
# script/backup.sh 與 script/restore.sh——備份與還原的主機端入口（#52）。
#
# 它們把事情交給容器裡的 CLI（`config_manager.api.cli backup|restore`，T10）；被觀察的是**接線**
# 與**守衛**：發出了什麼 docker 呼叫、掛了什麼進去、以及還原前「服務已停止」那一道確認——那是
# 唯一一件 CLI 自己做不到的事（容器裡看不見 compose）。
#
# **docker 以 PATH 上的假指令替換**，與 release.bats 的 gh 同一個手法：假的 docker 把每個參數
# 各記一行；`compose ps` 回的內容由環境變數決定，模擬「服務還在跑」與「已停」。

setup() {
  REPO_ROOT="$(cd "$(dirname "${BATS_TEST_FILENAME}")/../../.." && pwd)"
  WORK="$(mktemp -d)"
  STUB="${WORK}/bin"
  mkdir -p "${STUB}"
  PATH="${STUB}:${PATH}"
  export PATH

  DOCKER_CALLS="${WORK}/docker-calls"
  export DOCKER_CALLS
  # FAKE_RUNNING 非空＝`docker compose ps --status running -q backend` 印出一個容器 id。
  export FAKE_RUNNING=""
  cat >"${STUB}/docker" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >>"${DOCKER_CALLS}"
printf -- '--\n' >>"${DOCKER_CALLS}"
if [[ "$*" == *"compose ps"* ]]; then
  printf '%s' "${FAKE_RUNNING}"
fi
STUB
  chmod +x "${STUB}/docker"

  # 給一個暫存的目標根目錄：規格看的是腳本發出的呼叫，不是這台機器上有沒有 /opt/robot。
  export CM_TARGET_ROOT="${WORK}/targets"
  mkdir -p "${CM_TARGET_ROOT}"

  BUNDLE="${WORK}/config-repo-20261012T000000Z-abcdef0.bundle"
  printf 'not really a bundle\n' >"${BUNDLE}"
}

teardown() {
  rm -rf "${WORK}"
}

# 假 docker 記下的最後一次呼叫（參數一行一個）。
last_call() {
  awk 'BEGIN{RS="--\n"} {last=$0} END{printf "%s", last}' "${DOCKER_CALLS}"
}

@test "backup.sh 在服務的映像裡跑 CLI 的 backup，把主機上的目的地掛進去" {
  run "${REPO_ROOT}/script/backup.sh" "${WORK}/backups"

  [ "${status}" -eq 0 ]
  call="$(last_call)"
  [[ "${call}" == *$'compose\nrun\n'* ]]
  [[ "${call}" == *$'--volume\n'"${WORK}/backups:/backup"$'\n'* ]]
  [[ "${call}" == *$'--entrypoint\npython\n'* ]]
  [[ "${call}" == *$'backend\n-m\nconfig_manager.api.cli\nbackup\n/backup'* ]]
}

@test "backup.sh 以呼叫者的身分跑，備份檔在主機上才是呼叫者的" {
  # 容器的服務使用者是 uid 1000；以它寫進 bind mount，主機上會多出一個別人擁有的檔案。
  run "${REPO_ROOT}/script/backup.sh" "${WORK}/backups"

  [ "${status}" -eq 0 ]
  [[ "$(last_call)" == *$'--user\n'"$(id -u):$(id -g)"$'\n'* ]]
}

@test "backup.sh 先把目的地建起來，再交給 docker 掛" {
  # docker 自己建的掛載點是 root 的；先建好，掛進去的才是呼叫者可寫的目錄。
  run "${REPO_ROOT}/script/backup.sh" "${WORK}/deeper/backups"

  [ "${status}" -eq 0 ]
  [ -d "${WORK}/deeper/backups" ]
}

@test "backup.sh 沒給目的地就說用法，不去碰 docker" {
  run "${REPO_ROOT}/script/backup.sh"

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"下一步："* ]]
  [ ! -e "${DOCKER_CALLS}" ]
}

@test "restore.sh 在 backend 還在跑的時候拒絕，說要先停、不發出任何 run" {
  export FAKE_RUNNING="0123456789ab"

  run "${REPO_ROOT}/script/restore.sh" "${BUNDLE}"

  [ "${status}" -eq 1 ]
  [[ "${output}" == *"還在跑"* ]]
  [[ "${output}" == *"下一步："*"stop"* ]]
  ! grep -qx 'run' "${DOCKER_CALLS}"
}

@test "restore.sh 在服務已停的時候，把備份檔唯讀掛進容器、跑 CLI 的 restore" {
  run "${REPO_ROOT}/script/restore.sh" "${BUNDLE}"

  [ "${status}" -eq 0 ]
  call="$(last_call)"
  name="$(basename "${BUNDLE}")"
  [[ "${call}" == *$'compose\nrun\n'* ]]
  [[ "${call}" == *$'--volume\n'"${BUNDLE}:/restore/${name}:ro"$'\n'* ]]
  [[ "${call}" == *$'--entrypoint\npython\n'* ]]
  [[ "${call}" == *$'backend\n-m\nconfig_manager.api.cli\nrestore\n'"/restore/${name}"* ]]
}

@test "restore.sh 的 run 不帶 --user：還原出來的設定庫要屬於服務的使用者" {
  run "${REPO_ROOT}/script/restore.sh" "${BUNDLE}"

  [ "${status}" -eq 0 ]
  [[ "$(last_call)" != *$'--user\n'* ]]
}

@test "restore.sh 還原成功後說下一步是把服務起起來" {
  run "${REPO_ROOT}/script/restore.sh" "${BUNDLE}"

  [ "${status}" -eq 0 ]
  [[ "${output}" == *"下一步："*"up"* ]]
}

@test "restore.sh 給的檔案不存在就指名它，不去碰 docker" {
  run "${REPO_ROOT}/script/restore.sh" "${WORK}/nowhere.bundle"

  [ "${status}" -eq 2 ]
  [[ "${output}" == *"${WORK}/nowhere.bundle"* ]]
  [ ! -e "${DOCKER_CALLS}" ]
}

@test "restore.sh 不會自己停服務，也不會自己清空設定庫" {
  # 停服務與移走既有資料都是人的決定（#52 定案 2、3）；腳本只確認，不代做。
  export FAKE_RUNNING="0123456789ab"

  run "${REPO_ROOT}/script/restore.sh" "${BUNDLE}"

  # 先確定它真的跑了、問過 compose——否則「沒有發出 stop」只是因為什麼都沒發生。
  [ "${status}" -eq 1 ]
  grep -qx 'ps' "${DOCKER_CALLS}"
  ! grep -qxE 'stop|down|rm' "${DOCKER_CALLS}"
}
