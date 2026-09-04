# syntax=docker/dockerfile:1
#
# config_manager -- one image, two services (§3.2 "每個容器一個服務").
# backend runs the API server, frontend serves the static page; both are the
# same artifact started with a different command, so there is exactly one
# thing to build, scan and version.
#
# Stage names are the ycpss91255-docker/base baseline (ADR-00000016). They are
# adopted NOW, while nothing depends on them, because renaming stages later
# means rewriting every CI reference and every deploy script that names one:
#
#   sys           user, group, locale, timezone        build intermediate
#   devel-base    interpreter + build tooling          build intermediate
#   devel         interactive shell, source bind-mounted   session
#   runtime       field artifact, source baked in      deployable
#   runtime-test  build-time smoke, discarded          session
#
# "Deployable" is a single predicate, decided here and nowhere else: `runtime`
# is, the other four are not. Two places deciding it separately disagree
# sooner or later, and the direction of the disagreement is "something got
# deployed that should not have been".
#
# Artifacts bake under /opt, never $HOME. $HOME resolves at build time, so
# anything placed under it is welded to the build-time user name and breaks on
# a machine whose operator has a different one.

ARG BASE_IMAGE="python:3.11-slim-bookworm"

# ── sys ─────────────────────────────────────────────────────────────────────
FROM ${BASE_IMAGE} AS sys

ARG USER_NAME="user"
ARG USER_UID=1000
ARG USER_GROUP="user"
ARG USER_GID=1000
ARG TZ="Asia/Taipei"
ARG DEBIAN_FRONTEND=noninteractive

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

# A non-root service account. The backend writes to the config-repo checkout
# and to target paths; it must never do so as root (PRD invariant 4 -- when
# safety and convenience pull apart, the default lands on safety). Privileged
# targets go through an explicit sudoers allowlist, which is out of v0.10.0
# scope (appendix B.3) and deliberately absent here rather than approximated.
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

# git is a runtime dependency, not a build one: io/git.py wraps the git CLI by
# subprocess (§3.2). It is installed here so devel and runtime share one
# version, rather than each stage picking its own.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

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
    && rm -rf /var/lib/apt/lists/*

COPY config/pip/requirements-dev.txt /tmp/requirements-dev.txt
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt \
    && rm -f /tmp/requirements-dev.txt

COPY config/shell/bashrc "/home/${USER_NAME}/.bashrc"
COPY script/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && chown "${USER_NAME}:${USER_NAME}" "/home/${USER_NAME}/.bashrc"

# devel takes the source by bind mount, not COPY: the edit-run loop is the
# whole point of the stage, and a baked copy would go stale on the first edit.
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

# The deployable stage bakes the source; nothing is bind-mounted in the field
# except the config-repo volume, which is data rather than code.
COPY --chown=${USER_NAME}:${USER_NAME} src/ ${APP_ROOT}/src/
COPY --chown=${USER_NAME}:${USER_NAME} pyproject.toml ${APP_ROOT}/pyproject.toml

ENV PYTHONPATH="${APP_ROOT}/src"
WORKDIR ${APP_ROOT}

USER ${USER_NAME}
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "api.cli", "serve"]

# ── runtime-test ────────────────────────────────────────────────────────────
# FROM the stage under test, then layer the tools on top -- never the inverse.
# A test stage built FROM a tools image tests the tools image's environment,
# which is exactly the environment that is not going to be deployed.
FROM runtime AS runtime-test

USER root

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bats \
    && rm -rf /var/lib/apt/lists/*

COPY test/bats/smoke/ /opt/smoke/
RUN bats /opt/smoke

ARG USER_NAME="user"
USER ${USER_NAME}
