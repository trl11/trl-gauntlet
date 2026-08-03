ARG PYTHON_IMAGE=mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

FROM ${PYTHON_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive

# System packages the suites' transports need. The list lives at the repository
# root because it describes the project, not the container: a bare host running
# the same suites needs the same packages.
#
# The base image carries a yarn repository whose signing key it no longer
# ships, which fails `apt-get update` outright. Node comes from NodeSource
# below and nothing here uses yarn, so drop the list rather than work around
# the verification error.
COPY dependencies.txt /tmp/dependencies.txt
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/sources.list.d/yarn.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        $(sed -e 's/#.*//' /tmp/dependencies.txt | xargs) && \
    rm -f /tmp/dependencies.txt

# Node, for the npm-distributed coding agents below. Bookworm ships a version
# too old for Claude Code, so it comes from NodeSource.
ARG NODE_MAJOR=22
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - && \
    apt-get install -y --no-install-recommends nodejs

# uv builds .venv and installs both packages. `make` calls it unconditionally,
# so it is part of the image rather than something a create hook adds.
RUN pip install --no-cache-dir --upgrade pip uv

# The repository is bind-mounted, so anything the container writes into it —
# .venv, build/, output/, scaffolded suites — lands on the host owned by
# whichever uid wrote it. Matching the caller keeps a single owner on both
# sides and avoids needing sudo to clean up afterwards.
#
# The base image already carries a non-root user at 1000:1000. Renaming it
# keeps its group memberships (nvm, pipx) and its sudoers entry, where adding a
# second user would leave two candidate owners. Hosts that are not 1000:1000
# override these, and the devcontainer CLI's updateRemoteUserUID does the same
# adjustment at create time.
ARG USERNAME=dev
ARG OLD_USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

RUN set -eux; \
    old_uid="$(id -u ${OLD_USERNAME})"; \
    old_gid="$(id -g ${OLD_USERNAME})"; \
    groupmod -n ${USERNAME} ${OLD_USERNAME}; \
    usermod -l ${USERNAME} -d /home/${USERNAME} -m ${OLD_USERNAME}; \
    if [ "${USER_GID}" != "$old_gid" ]; then groupmod -g "${USER_GID}" ${USERNAME}; fi; \
    if [ "${USER_UID}" != "$old_uid" ]; then usermod -u "${USER_UID}" ${USERNAME}; fi; \
    if [ -f /etc/sudoers.d/${OLD_USERNAME} ]; then \
        mv /etc/sudoers.d/${OLD_USERNAME} /etc/sudoers.d/${USERNAME}; \
        sed -i "s/\b${OLD_USERNAME}\b/${USERNAME}/g" /etc/sudoers.d/${USERNAME}; \
    fi; \
    chown -R ${USER_UID}:${USER_GID} /home/${USERNAME}; \
    for d in /usr/local/py-utils /usr/local/share/nvm; do \
        if [ -d "$d" ]; then find "$d" -uid "$old_uid" -exec chown -h ${USER_UID} {} +; fi; \
    done

# Coding agents. The npm prefix belongs to dev rather than root so both can
# apply their own updates without sudo. Versions default to the current
# release; pin them through build args for a reproducible image.
ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest
ENV NPM_CONFIG_PREFIX=/opt/npm-global
ENV PATH=/opt/npm-global/bin:${PATH}
RUN mkdir -p /opt/npm-global && chown -R ${USER_UID}:${USER_GID} /opt/npm-global

ENV HOME=/home/${USERNAME}

USER ${USERNAME}

RUN npm install -g \
        @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION} \
        @openai/codex@${CODEX_VERSION} && \
    npm cache clean --force
