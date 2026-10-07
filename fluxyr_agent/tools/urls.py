"""Shared SSRF (Server-Side Request Forgery) protection utilities.

Used by any tool provider that fetches user-supplied URLs (e.g.
WebBrowserToolProvider, TechDocToolProvider) to prevent access to private or
loopback addresses.
"""

import ipaddress
import socket
import urllib.parse

# IPv4-mapped IPv6 addresses (::ffff:0:0/96) encode private IPv4 addresses in
# IPv6 form and must be checked separately by extracting the embedded IPv4
# address.
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_private_ip(ip.ipv4_mapped)
    return any(ip in network for network in _PRIVATE_NETWORKS)


def validate_url(url: str | None) -> str | None:
    """Validate a URL for safety. Returns an error string, or None if valid.

    Security note (SSRF TOCTOU): hostname is resolved here for validation, then
    re-resolved independently at fetch time. DNS rebinding could return a public
    IP on the first lookup and a private IP on the second. This residual risk is
    accepted because this tool is available only to authenticated users.
    """
    if not url or not isinstance(url, str):
        return "URL is required."
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return f"Invalid URL scheme '{parsed.scheme}': only http and https are allowed."
    hostname = parsed.hostname
    if not hostname:
        return "URL must include a hostname."
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return f"Could not resolve hostname: {exc}"
    for addr_info in addr_infos:
        ip_str = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_private_ip(ip):
            return f"Access to private/loopback addresses is not allowed (resolved to {ip_str})."
    return None
