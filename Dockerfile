# syntax=docker/dockerfile:1
#
# config_manager —— 一份映像，兩個服務（§3.2「每個容器一個服務」）。
# backend 跑 API 伺服器，frontend 供靜態頁面；兩者是同一個產物、以不同指令啟動，
# 所以要建置、掃描、標版本的東西剛好只有一個。
#
# 階段名稱取自 ycpss91255-docker/base 的基準（ADR-00000016）。**現在**就採用，趁還
# 沒有東西依賴它們，因為之後才改階段名，等於要改寫每一處 CI 參照與每一支指名階段
# 的部署腳本：
#
#   sys           使用者、群組、locale、時區          建置中間層
#   devel-base    直譯器 + 建置工具                   建置中間層
#   devel         互動 shell，原始碼以 bind mount 掛入    工作階段
#   runtime       現場產物，原始碼烤進去              可部署
#   runtime-test  建置期 smoke，用完即丟              工作階段
#
# 「可部署」是單一的判定，只在這裡決定、不在別處：`runtime` 是，其餘四個都不是。
# 兩個地方各自決定這件事，遲早會不一致，而不一致的方向會是「有個不該被部署的東西
# 被部署了」。
#
# 產物烤在 /opt 底下，絕不放 $HOME。$HOME 在建置期就解析掉，所以放在它底下的東西
# 會被焊死在建置期的使用者名稱上，換一台操作者名稱不同的機器就壞掉。

ARG BASE_IMAGE="python:3.11-slim-bookworm"
# 除非呼叫端釘住 digest，否則為空。原樣記錄而不是推導出來，因為「從參照裡去掉
# digest」的那個運算式在沒有 digest 可去的時候會回傳整個參照——那會把一個 tag 塞進
# OCI 保留給 digest 的欄位。空值是誠實的「沒有記錄」。
ARG BASE_IMAGE_DIGEST=""

# ── sys ─────────────────────────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS sys

ARG USER_NAME="user"
ARG USER_UID=1000
ARG USER_GROUP="user"
ARG USER_GID=1000
ARG TZ="Asia/Taipei"
ARG DEBIAN_FRONTEND=noninteractive
ARG BASE_IMAGE
ARG BASE_IMAGE_DIGEST

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        locales \
        tzdata \
    && sed -i '/en_US.UTF-8/s/^# //' /etc/locale.gen \
    && locale-gen \
    && ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime \
    && echo "${TZ}" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 來源證跡，也是 .hadolint.yaml 能夠 ignore DL3008、而那個 ignore 不是在指涉空氣的
# 理由。套件版本**不釘**——一份映像若沒有 repo 發版就吃不到安全更新，對它的使用者
# 而言比會漂移更糟——所以漂移是被記錄下來而不是被阻止：base-image.env 說這是 FROM
# 什麼建的，packages.txt 說實際落地的是哪些版本，每一層 apt 之後重寫一次。兩份行為
# 不同的映像，於是可以一路 diff 到那個變掉的版本。
#
# 這是光靠 tag 給不了的：python:3.11-slim-bookworm 是會移動的 tag，所以「base 已經
# 釘住了」根本不構成一個控制措施。
RUN mkdir -p /usr/local/share/config_manager \
    && { \
      echo "base_image_ref=${BASE_IMAGE}"; \
      echo "base_image_pin=$([[ -n "${BASE_IMAGE_DIGEST}" ]] && echo digest || echo none)"; \
      echo "base_image_digest=${BASE_IMAGE_DIGEST}"; \
      sed -n 's/^PRETTY_NAME=/base_os=/p' /etc/os-release; \
    } > /usr/local/share/config_manager/base-image.env \
    && dpkg-query -W > /usr/local/share/config_manager/packages.txt

# 用 `docker inspect` 就讀得到，不必把映像解開。
LABEL org.opencontainers.image.base.name="${BASE_IMAGE}" \
      org.opencontainers.image.base.digest="${BASE_IMAGE_DIGEST}"

# 一個非 root 的服務帳號。backend 會寫入 config-repo 的簽出內容與目標位置；它絕不
# 可以用 root 做這件事（PRD 不變式 4——安全與方便拉扯時，預設落向安全）。需要特權的
# 目標走一份明確的 sudoers 白名單，那不在 v0.10.0 範圍內（附錄 B.3），所以這裡刻意
# 讓它缺席，而不是隨便做一個近似品。
RUN groupadd --gid "${USER_GID}" "${USER_GROUP}" \
    && useradd --uid "${USER_UID}" --gid "${USER_GID}" \
        --create-home --shell /bin/bash "${USER_NAME}"

ENV LANG="en_US.UTF-8" \
    LANGUAGE="en_US:en" \
    LC_ALL="en_US.UTF-8" \
    TZ="${TZ}"

# ── devel-base ──────────────────────────────────────────────────────────────
FROM sys AS devel-base

ARG DEBIAN_FRONTEND=noninteractive

# git 是執行期相依，不是建置期相依：io/git.py 以 subprocess 包裝 git CLI（§3.2）。
# 裝在這裡，是為了讓 devel 與 runtime 共用同一個版本，而不是各階段各挑各的。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W > /usr/local/share/config_manager/packages.txt

ENV APP_ROOT="/opt/config_manager" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY config/pip/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# ── devel ───────────────────────────────────────────────────────────────────
FROM devel-base AS devel

ARG USER_NAME="user"
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        less \
        vim-tiny \
    && rm -rf /var/lib/apt/lists/* \
    && dpkg-query -W > /usr/local/share/config_manager/packages.txt

# 兩個檔案都要：requirements-dev.txt 開頭是 `-r requirements.txt`，pip 會相對於它
# 解析那個路徑，所以同層的那份必須在。只複製 dev 那一份會以 "Could not open
# requirements file" 失敗——而且是沒有人察覺地失敗，因為 CI 建的是 runtime-test，
# 它的鏈是 devel-base -> runtime，從來不會進到這個階段。開發者每天在用的階段，
# 正好是唯一沒有東西會去建的階段。
COPY config/pip/requirements.txt config/pip/requirements-dev.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt \
    && rm -f /tmp/requirements-dev.txt /tmp/requirements.txt

COPY config/shell/bashrc "/home/${USER_NAME}/.bashrc"
COPY script/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && chown "${USER_NAME}:${USER_NAME}" "/home/${USER_NAME}/.bashrc"

# devel 以 bind mount 取得原始碼，不用 COPY：改一行跑一次的循環正是這個階段存在的
# 理由，而烤進去的副本在第一次修改時就過期了。
RUN mkdir -p "${APP_ROOT}" \
    && chown "${USER_NAME}:${USER_NAME}" "${APP_ROOT}"
WORKDIR ${APP_ROOT}

USER ${USER_NAME}
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# ── runtime ─────────────────────────────────────────────────────────────────
FROM devel-base AS runtime

ARG USER_NAME="user"

COPY script/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 可部署的階段把原始碼烤進去；現場除了 config-repo volume 之外不掛任何東西，
# 而那個 volume 裝的是資料，不是程式碼。
COPY --chown=${USER_NAME}:${USER_NAME} src/ ${APP_ROOT}/src/
COPY --chown=${USER_NAME}:${USER_NAME} pyproject.toml ${APP_ROOT}/pyproject.toml

ENV PYTHONPATH="${APP_ROOT}/src"
WORKDIR ${APP_ROOT}

USER ${USER_NAME}
ENTRYPOINT ["/entrypoint.sh"]
# 是 config_manager.api.cli，不是 api.cli：所有東西都住在單一頂層套件底下，因為
# 頂層的 io/ 會遮蔽標準函式庫的同名模組（ADR-00000026、#56）。這一行在改名之後很久
# 都還留著改名前的路徑，而且沒有東西會執行到它——當時 api/cli 還不存在（#90）。
CMD ["python", "-m", "config_manager.api.cli", "serve"]

# ── runtime-test ────────────────────────────────────────────────────────────
# FROM 被測的那個階段，再把工具疊上去——絕不反過來。以工具映像為 FROM 建出來的
# 測試階段，測的是工具映像的環境，而那正好是不會被部署的那個環境。
FROM runtime AS runtime-test

# 真正的 docker 會沿著 FROM 繼承 sys 設的 SHELL，所以這一行對建置行為是零影響；
# hadolint 不繼承，它逐個 stage 看，於是底下那個帶 pipe 的 RUN 在它眼裡是跑在
# 沒有 pipefail 的 sh 上（DL4006）。寫在這裡，讓「這個 stage 依賴 pipefail」
# 這件事在這個 stage 裡就看得到——那正好也是它該被寫下來的地方。
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

USER root

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bats \
    && rm -rf /var/lib/apt/lists/*

# 測試工具只裝在這一層。runtime 不得含它們，而 runtime 是 FROM devel-base，
# 所以 pytest 絕不能進 config/pip/requirements.txt——那份是 devel-base 裝的。
RUN pip install --no-cache-dir pytest

# 這裡是這組規格唯一該執行的地方，所以這裡也是唯一擋得住它們「永遠在跳過」的地方。
# 它們在別的環境會自己跳過（那裡沒有被測的映像可看），而一組每次都跳過的規格與
# 沒有規格的差別只有輸出多幾行字。旗標與守衛要一起看：旗標讓它們在這裡真的跑，
# 守衛保證旗標被漏掉時建置紅掉，而不是安靜地全部跳過。
COPY test/bats/system/ /opt/system-bats/
RUN CM_SYSTEM_IMAGE=1 bats --formatter tap /opt/system-bats | tee /tmp/system-bats.tap \
    && if grep -q '# skip' /tmp/system-bats.tap; then \
         echo 'Dockerfile: 系統層的 bats 規格在它唯一該執行的地方跳過了' >&2; \
         echo 'Dockerfile: 那不是通過。檢查 CM_SYSTEM_IMAGE 有沒有傳進去' >&2; \
         exit 1; \
       fi \
    && rm -f /tmp/system-bats.tap

# 系統層級（T9／T10）：服務在**這份映像**裡起在 loopback 上，以真實 HTTP 回話。
# 同一份規格也由 script/test.sh 在工具映像裡就地執行一次——那一次的覆蓋率算得進
# 報告，這一次量的是保真度。兩者跑同一份檔案，所以不會分歧（#116、#97）。
COPY test/pytest/system/ /opt/system/
RUN mkdir -p /tmp/config-repo \
    && ( CM_CONFIG_REPO=/tmp/config-repo CM_ROLE=backend \
         /entrypoint.sh python -m config_manager.api.cli serve \
           --host 127.0.0.1 --port 8080 & ) \
    && CM_SYSTEM_BASE_URL=http://127.0.0.1:8080 \
       CM_SYSTEM_CONFIG_REPO=/tmp/config-repo \
       pytest /opt/system -q

ARG USER_NAME="user"
USER ${USER_NAME}
