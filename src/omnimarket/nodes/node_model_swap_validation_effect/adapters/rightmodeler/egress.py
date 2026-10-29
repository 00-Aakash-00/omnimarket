# SPDX-FileCopyrightText: 2026 OmniNode.ai Inc.
# SPDX-License-Identifier: MIT
"""An external network namespace enforces and records the one allowed route."""

from __future__ import annotations

import ipaddress
import re


def firewall_script(gateway_ip: str, gateway_port: int) -> str:
    """The tool has no capabilities to alter these namespace-level rules."""
    ipaddress.IPv4Address(gateway_ip)
    if not 0 < gateway_port < 65536:
        raise ValueError("invalid gateway port")
    return (
        "set -eu; "
        "iptables -F OUTPUT; ip6tables -F OUTPUT; "
        # The tool and all its children are fixed to uid 1000, without setuid
        # capabilities or privilege escalation. Kernel IPv6 neighbor discovery
        # is not an application connection and must not create false failures.
        "iptables -A OUTPUT -m owner --uid-owner 1000 -j NFLOG --nflog-group 1; "
        f"iptables -A OUTPUT -p tcp -d {gateway_ip} --dport {gateway_port} -j ACCEPT; "
        "iptables -A OUTPUT -j REJECT; "
        "ip6tables -A OUTPUT -m owner --uid-owner 1000 -j NFLOG --nflog-group 1; "
        "ip6tables -A OUTPUT -j REJECT; "
        # Internal Docker networks omit a default route. Supply a route only
        # after the deny rules exist, so even otherwise unroutable attempts
        # reach OUTPUT and leave evidence before being rejected. IPv6 has no
        # allowed destination; its documentation address exists only locally.
        f"ip route replace default via {gateway_ip}; "
        "ip -6 address replace 2001:db8::2/128 dev eth0 nodad; "
        "ip -6 route replace default dev eth0"
    )


def destinations(capture: str) -> tuple[str, ...]:
    """Read numeric destination endpoints from kernel NFLOG packet records."""
    return tuple(
        sorted(set(re.findall(r"\b(?:IP|IP6)\s+\S+\s+>\s+(\S+?):\s", capture)))
    )


def violations(capture: str, gateway_ip: str, gateway_port: int) -> tuple[str, ...]:
    allowed = f"{gateway_ip}.{gateway_port}"
    return tuple(
        destination for destination in destinations(capture) if destination != allowed
    )


def capture_complete(capture: str) -> bool:
    """A lost, overflowing or truncated monitor can never support success."""
    if re.search(r"\n0 packets dropped by kernel\s*$", capture) is None:
        return False
    return all(
        not line.startswith("tcpdump:")
        or line.startswith(("tcpdump: verbose output suppressed", "tcpdump: listening"))
        for line in capture.splitlines()
    )
