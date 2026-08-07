# Jenkins CI environment. It is deliberately separate from the runtime image:
# it contains the desktop-packaging and test toolchain, while docker/Dockerfile
# remains the small image shipped to bench hosts.
ARG NODE_IMAGE=node:22-bookworm
FROM ${NODE_IMAGE}

ARG APT_PROXY_URL
ARG DEBIAN_FRONTEND=noninteractive

# Agents on the LAN may use the apt cache, but a developer's local CI image
# must remain usable without it.
RUN if [ -n "${APT_PROXY_URL}" ]; then \
        hostport="${APT_PROXY_URL#http://}"; hostport="${hostport#https://}"; hostport="${hostport%%/*}"; \
        if timeout 2 bash -c "echo >/dev/tcp/${hostport%:*}/${hostport#*:}" 2>/dev/null; then \
            printf 'Acquire::http::Proxy "%s";\n' "${APT_PROXY_URL}" > /etc/apt/apt.conf.d/01proxy; \
            printf 'Acquire::https::Proxy "DIRECT";\n' >> /etc/apt/apt.conf.d/01proxy; \
        fi; \
    fi

# dependencies.txt owns the desktop runtime requirements. The additional tools
# here build the Python environment, native npm dependencies, release packages,
# and Git submodules inside a disposable CI container.
COPY dependencies.txt /tmp/dependencies.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        $(sed -e 's/#.*//' /tmp/dependencies.txt | xargs) \
        build-essential \
        ca-certificates \
        fakeroot \
        git \
        git-lfs \
        make \
        openssh-client \
        python3 \
        python3-pip \
    && python3 -m pip install --no-cache-dir --break-system-packages uv \
    && rm -rf /var/lib/apt/lists/* /tmp/dependencies.txt

# Jenkins workspaces are mounted from agents as uid 1000. The node image's
# `node` user has that uid, so artifacts remain removable by the Jenkins agent.
# Jenkins supplies the fleet Docker socket group at runtime through --group-add.
RUN git config --system --add safe.directory '*'

USER node
WORKDIR /workspace
