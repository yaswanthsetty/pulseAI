"""Server-Side Request Forgery (SSRF) protection (spec §23).

Every outbound fetch the platform performs — RSS feeds at poll time and
article pages during processing — is guarded here. The check resolves the
hostname to its current IP addresses and rejects any that fall inside
private/reserved ranges (including cloud metadata endpoints such as
169.254.169.254), so a malicious or hijacked feed cannot reach internal
services.

Note: this is a best-effort guard at fetch time; DNS rebinding between the
check and the request is mitigated by re-checking per fetch.
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}

# Hostnames that never resolve to public internet.
BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.azure.internal",
    "metadata.aws.internal",
}


def _ip_is_safe(ip: str) -> bool:
    """Return True when the IP is routable on the public internet."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv4Address) and addr.is_global is False)
    )


@dataclass(frozen=True)
class UrlSafety:
    safe: bool
    reason: str = ""


def _hostname_is_blocked(hostname: str) -> bool:
    lowered = hostname.lower()
    return lowered in BLOCKED_HOSTNAMES or lowered.endswith(".internal")


def check_url(url: str) -> UrlSafety:
    """Validate that *url* is http(s) and resolves only to public IPs."""
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # pragma: no cover - malformed input
        return UrlSafety(False, f"malformed URL: {exc}")

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return UrlSafety(False, f"scheme '{parts.scheme}' not allowed")

    hostname = parts.hostname
    if not hostname:
        return UrlSafety(False, "URL has no hostname")

    if _hostname_is_blocked(hostname):
        return UrlSafety(False, f"hostname '{hostname}' is blocked")

    try:
        infos = socket.getaddrinfo(hostname, parts.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return UrlSafety(False, f"could not resolve hostname '{hostname}'")

    addresses = {info[4][0] for info in infos}
    for ip in addresses:
        if not _ip_is_safe(ip):
            return UrlSafety(False, f"resolved address {ip} is not public internet")

    return UrlSafety(True, "")


class UnsafeUrlError(Exception):
    """Raised when an outbound fetch target fails the SSRF guard."""
