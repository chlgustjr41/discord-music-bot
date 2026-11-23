#!/bin/sh
# Keep the jacky-music containers off the home network.
#
# What this does
#   Containers on the compose networks (all inside 172.28.0.0/16, see
#   deploy/docker-compose.yml) may talk to each other and to the public
#   internet. They may NOT open connections to private address space: the
#   LAN, the router, other Docker networks, or the host's own services.
#
# Why it exists
#   The stack fetches audio from the internet on behalf of anyone who can
#   queue a track, and one sidecar executes JavaScript served by YouTube.
#   On a cloud VM there was nothing next door worth reaching. On a home
#   server there is: the router's admin page, the NAS's own management
#   interface, every other device on the LAN. The application already
#   refuses non-YouTube/SoundCloud URLs and Lavalink's generic http source is
#   off; this rule is the layer that still holds if either of those is wrong
#   or a container is compromised.
#
# How it works
#   Two dedicated chains, so nothing else on the host is touched and the
#   script can be re-run safely:
#     JACKY-EGRESS  hooked from DOCKER-USER (forwarded traffic leaving the
#                   compose subnets)
#     JACKY-HOST    hooked from INPUT (traffic from the compose subnets to
#                   the host itself, e.g. via the bridge gateway address)
#   REJECT (not DROP) so a blocked fetch fails immediately instead of hanging
#   an audio thread until its timeout, and so "host is up" and "host is
#   down" look identical from inside a container.
#
# DNS
#   Recent Docker engines forward external lookups from the container's own
#   network namespace, so a resolver on the LAN (the usual home-router
#   setup) would be blocked by this script. The compose file therefore gives
#   every service public resolvers (JACKY_DNS_1/2 in deploy/.env).
#   If your network does not let public resolvers through, point those two
#   settings at the LAN resolver and name it here, space-separated:
#     JACKY_DNS_ALLOW="192.168.1.1" egress-firewall.sh apply
#   Only port 53 to exactly those addresses is opened. The systemd unit reads
#   the variable from /etc/default/jacky-egress-firewall when that file exists.
#
# Usage (as root):
#   egress-firewall.sh apply    install or refresh the rules (idempotent)
#   egress-firewall.sh status   exit 0 if the rules are in place, 1 if not
#   egress-firewall.sh remove   take the rules out again
#
# Rules do not survive a reboot on their own. Install the systemd unit and
# timer that sit next to this file (see docs/operations/NAS-SETUP.md).
set -eu

SUBNET="${JACKY_SUBNET:-172.28.0.0/16}"
EGRESS_CHAIN="JACKY-EGRESS"
HOST_CHAIN="JACKY-HOST"
# Private, link-local and carrier-grade-NAT space. 100.64.0.0/10 also covers
# Tailscale-style overlay addresses, which a home server may well have.
PRIVATE_NETS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10"
# Optional: private resolver addresses the containers may query (port 53 only).
DNS_ALLOW="${JACKY_DNS_ALLOW:-}"

ipt() { iptables -w 5 "$@"; }

require() {
    command -v iptables >/dev/null 2>&1 || { echo "iptables not found" >&2; exit 2; }
    [ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 2; }
    # DOCKER-USER is created by the Docker daemon. Its absence means Docker
    # is not running yet, or is using a firewall backend this script does not
    # know (the script must not pretend to protect anything in that case).
    ipt -nL DOCKER-USER >/dev/null 2>&1 || {
        echo "DOCKER-USER chain not found: is Docker running with the iptables backend?" >&2
        exit 2
    }
}

apply() {
    # (Re)build the forward chain from scratch so edits to this file take
    # effect on the next run.
    ipt -N "$EGRESS_CHAIN" 2>/dev/null || ipt -F "$EGRESS_CHAIN"
    # Container-to-container traffic inside the stack stays allowed. This
    # must come first: the compose subnets are themselves inside 172.16/12.
    ipt -A "$EGRESS_CHAIN" -d "$SUBNET" -j RETURN
    for resolver in $DNS_ALLOW; do
        ipt -A "$EGRESS_CHAIN" -d "$resolver" -p udp --dport 53 -j RETURN
        ipt -A "$EGRESS_CHAIN" -d "$resolver" -p tcp --dport 53 -j RETURN
    done
    for net in $PRIVATE_NETS; do
        ipt -A "$EGRESS_CHAIN" -d "$net" -j REJECT --reject-with icmp-admin-prohibited
    done
    ipt -A "$EGRESS_CHAIN" -j RETURN

    # Traffic from the containers to the host itself (management UI, SSH,
    # file shares). Replies to anything the host started are let through.
    ipt -N "$HOST_CHAIN" 2>/dev/null || ipt -F "$HOST_CHAIN"
    ipt -A "$HOST_CHAIN" -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    ipt -A "$HOST_CHAIN" -j REJECT --reject-with icmp-admin-prohibited

    # Hook both chains exactly once, at the top.
    ipt -C DOCKER-USER -s "$SUBNET" -j "$EGRESS_CHAIN" 2>/dev/null \
        || ipt -I DOCKER-USER 1 -s "$SUBNET" -j "$EGRESS_CHAIN"
    ipt -C INPUT -s "$SUBNET" -j "$HOST_CHAIN" 2>/dev/null \
        || ipt -I INPUT 1 -s "$SUBNET" -j "$HOST_CHAIN"
    echo "egress firewall applied for $SUBNET${DNS_ALLOW:+ (DNS allowed to: $DNS_ALLOW)}"
}

status() {
    ok=0
    ipt -C DOCKER-USER -s "$SUBNET" -j "$EGRESS_CHAIN" 2>/dev/null || ok=1
    ipt -C INPUT -s "$SUBNET" -j "$HOST_CHAIN" 2>/dev/null || ok=1
    for net in $PRIVATE_NETS; do
        ipt -C "$EGRESS_CHAIN" -d "$net" -j REJECT --reject-with icmp-admin-prohibited 2>/dev/null || ok=1
    done
    if [ "$ok" -eq 0 ]; then
        echo "egress firewall: in place for $SUBNET"
    else
        echo "egress firewall: MISSING or incomplete for $SUBNET" >&2
    fi
    return "$ok"
}

remove() {
    ipt -D DOCKER-USER -s "$SUBNET" -j "$EGRESS_CHAIN" 2>/dev/null || true
    ipt -D INPUT -s "$SUBNET" -j "$HOST_CHAIN" 2>/dev/null || true
    for chain in "$EGRESS_CHAIN" "$HOST_CHAIN"; do
        ipt -F "$chain" 2>/dev/null || true
        ipt -X "$chain" 2>/dev/null || true
    done
    echo "egress firewall removed"
}

case "${1:-}" in
    apply)  require; apply ;;
    status) require; status ;;
    remove) require; remove ;;
    *) echo "usage: $0 apply|status|remove" >&2; exit 64 ;;
esac
