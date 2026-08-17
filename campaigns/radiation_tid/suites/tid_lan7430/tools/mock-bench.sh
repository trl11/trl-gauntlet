#!/usr/bin/env bash
# A containerised stand-in for the bench, for exercising the real driver path
# without the part.
#
# It reproduces the shape of the real bench rather than simulating the
# measurement: a unit reachable on two networks, one carrying SSH and one
# standing in for the controller, with the data path shapeable. That is what
# makes it worth having — `driver: mock` exercises the analysis, and this
# exercises the SSH, the collector, the address resolution and the server.
#
# Traffic shaping needs NET_ADMIN, which the devcontainer does not hold. The
# unit is therefore a container of its own, started through the host's docker
# daemon, and this devcontainer joins the data network to reach it.
#
#   ./tools/mock-bench.sh up                 build, start, and wire it up
#   ./tools/mock-bench.sh shape 100mbit 2% 5ms   degrade the data path
#   ./tools/mock-bench.sh clear              remove the shaping
#   ./tools/mock-bench.sh run <profile>      run the suite against it
#   ./tools/mock-bench.sh down               remove everything it created
#
# `down` also detaches this devcontainer from the data network, which is the
# only change it makes outside its own containers.

set -euo pipefail

NETWORK=gauntlet-tid-data
UNIT=tid-mock-unit
IMAGE=gauntlet-tid-mock-unit
SUBNET=10.77.0.0/24
UNIT_IP=10.77.0.7
LAB_IP=10.77.0.20
STATE="${TMPDIR:-/tmp}/gauntlet-tid-mock-bench"
KEY="$STATE/id_unit"

here() { cd "$(dirname "${BASH_SOURCE[0]}")/.."; }

build() {
    mkdir -p "$STATE"
    [ -f "$KEY" ] || ssh-keygen -t ed25519 -N "" -f "$KEY" -q -C mock-unit
    cat > "$STATE/Dockerfile" <<'DOCKERFILE'
FROM debian:bookworm-slim
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ethtool iperf3 iproute2 openssh-server python3 && \
    rm -rf /var/lib/apt/lists/*
RUN mkdir -p /run/sshd /root/.ssh && chmod 700 /root/.ssh
COPY id_unit.pub /root/.ssh/authorized_keys
RUN chmod 600 /root/.ssh/authorized_keys && \
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
CMD ["/usr/sbin/sshd", "-D", "-e"]
DOCKERFILE
    docker build -q -t "$IMAGE" "$STATE" > /dev/null
}

up() {
    build
    docker network inspect "$NETWORK" > /dev/null 2>&1 || \
        docker network create --subnet "$SUBNET" "$NETWORK" > /dev/null
    docker rm -f "$UNIT" > /dev/null 2>&1 || true
    # The data network first, so it lands on eth0 and the control path on eth1.
    docker run -d --name "$UNIT" --cap-add=NET_ADMIN \
        --network "$NETWORK" --ip "$UNIT_IP" "$IMAGE" > /dev/null
    docker network connect bridge "$UNIT" > /dev/null

    local self
    self="$(cat /etc/hostname)"
    docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}' \
        | grep -q "$self" || docker network connect --ip "$LAB_IP" "$NETWORK" "$self" > /dev/null

    sleep 2
    echo "unit control: $(control_ip)"
    echo "unit data:    $UNIT_IP   (eth0 — stands in for the controller)"
    echo "lab data:     $LAB_IP"
    echo "key:          $KEY"
}

control_ip() {
    docker inspect "$UNIT" \
        --format '{{range $k, $v := .NetworkSettings.Networks}}{{if eq $k "bridge"}}{{$v.IPAddress}}{{end}}{{end}}'
}

shape() {
    local rate="${1:-100mbit}" loss="${2:-0%}" delay="${3:-0ms}"
    docker exec "$UNIT" tc qdisc replace dev eth0 root netem rate "$rate" loss "$loss" delay "$delay"
    docker exec "$UNIT" tc qdisc show dev eth0
}

clear_shaping() {
    docker exec "$UNIT" tc qdisc del dev eth0 root 2>/dev/null || true
    echo "shaping removed"
}

run_suite() {
    local profile="${1:?usage: mock-bench.sh run <profile.yaml> [run-dir]}"
    local run_dir="${2:-$STATE/run-$(date +%s)}"
    here
    GAUNTLET_SSH_USER=root GAUNTLET_SSH_KEY="$KEY" \
        python -m suite.cli --profile "$profile" --target "$(control_ip)" --run-dir "$run_dir"
}

down() {
    local self
    self="$(cat /etc/hostname)"
    docker network disconnect "$NETWORK" "$self" > /dev/null 2>&1 || true
    docker rm -f "$UNIT" > /dev/null 2>&1 || true
    docker network rm "$NETWORK" > /dev/null 2>&1 || true
    echo "mock bench removed, devcontainer detached from $NETWORK"
}

case "${1:-}" in
    up) up ;;
    shape) shift; shape "$@" ;;
    clear) clear_shaping ;;
    run) shift; run_suite "$@" ;;
    down) down ;;
    *) sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
