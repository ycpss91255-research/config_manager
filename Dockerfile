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
# Empty unless the caller pins a digest. Recorded as-is rather than derived,
# because the expression that strips a digest from a reference returns the
# whole reference when there is none to strip -- which would put a tag in the
# field OCI reserves for a digest. Empty is a truthful "not recorded".
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

# Provenance, and the reason .hadolint.yaml can ignore DL3008 without the
# ignore naming nothing. Package versions are NOT pinned -- an image that
# cannot take a security update without a repo release is worse for its
# audience than one that drifts -- so the drift is recorded instead of
# prevented: base-image.env says what this was built FROM, packages.txt says
# exactly which versions landed, rewritten after every apt layer. Two images
# that behave differently can then be diffed down to the version that changed.
#
# This is what the tag alone cannot give: python:3.11-slim-bookworm is a
# moving tag, so "the base is pinned" is not a control at all.
RUN mkdir -p /usr/local/share/config_manager \
    && { \
      echo "base_image_ref=${BASE_IMAGE}"; \
      echo "base_image_pin=$([[ -n "${BASE_IMAGE_DIGEST}" ]] && echo digest || echo none)"; \
      echo "base_image_digest=${BASE_IMAGE_DIGEST}"; \
      sed -n 's/^PRETTY_NAME=/base_os=/p' /etc/os-release; \
    } > /usr/local/share/config_manager/base-image.env \
    && dpkg-query -W > /usr/local/share/config_manager/packages.txt

# Readable with `docker inspect`, without unpacking the image.
LABEL org.opencontainers.image.base.name="${BASE_IMAGE}" \
      org.opencontainers.image.base.digest="${BASE_IMAGE_DIGEST}"

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

# Both files: requirements-dev.txt opens with `-r requirements.txt`, so pip
# resolves that path relative to it and needs the sibling present. Copying only
# the dev file failed with "Could not open requirements file" -- and did so
# unnoticed, because CI builds runtime-test, whose chain runs devel-base ->
# runtime and never enters this stage. The stage developers use every day was
# the one stage nothing built.
COPY config/pip/requirements.txt config/pip/requirements-dev.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dev.txt \
    && rm -f /tmp/requirements-dev.txt /tmp/requirements.txt

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
