# Gauntlet as a server: the API, the built frontend, and the built-in suites.
#
# The app in app/ ships the same two packages inside an Electron bundle. What
# differs is only who starts the server and where the hardware is.

ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

# ---------------------------------------------------------------------------
# The frontend bundle, which the wheel carries as package data.
# ---------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend

WORKDIR /build

# The ui-kit is consumed as source through the @trl11 alias, so it has to be
# present before vite runs. It installs nothing of its own.
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund

COPY extras/trl-ui-kit/ ./extras/trl-ui-kit/
COPY frontend/ ./frontend/
RUN cd frontend && npm run build

# ---------------------------------------------------------------------------
# The server.
# ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System packages the suites' transports need. This image has its own list,
# beside it: the one at the repository root describes a development machine and
# carries Electron's runtime, an X server and FUSE, none of which a container
# that serves an API has any use for.
COPY docker/dependencies.txt /tmp/dependencies.txt
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        $(sed -e 's/#.*//' /tmp/dependencies.txt | xargs) && \
    rm -rf /var/lib/apt/lists/* /tmp/dependencies.txt

WORKDIR /opt/gauntlet

COPY packages/ ./packages/
COPY --from=frontend /build/packages/gauntlet/src/gauntlet/web_dist/ \
     ./packages/gauntlet/src/gauntlet/web_dist/
RUN pip install --no-cache-dir ./packages/gauntlet-sdk ./packages/gauntlet

# The built-in campaigns ship with the image, and every built-in suite with
# them: a campaign carries its own suites. A deployment adds more by mounting
# them at /campaigns, or a bare suite at /suites, which discovery skips when
# they are absent.
COPY campaigns/ ./campaigns/

# Runs, the run index and config are the only state. Everything else in the
# image is read-only to the server.
ENV GAUNTLET_DATA_DIR=/data \
    GAUNTLET_SUITE_PATH=/suites \
    GAUNTLET_CAMPAIGN_PATH=/opt/gauntlet/campaigns:/campaigns

RUN useradd --create-home --uid 1000 gauntlet && \
    mkdir -p /data && chown gauntlet:gauntlet /data
USER gauntlet
VOLUME ["/data"]

EXPOSE 7100

# Every interface: the container's own loopback would be reachable by nothing.
CMD ["gauntlet", "serve", "--host", "0.0.0.0", "--port", "7100"]
