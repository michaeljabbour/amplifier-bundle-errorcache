"""Credential transport policy; this independently installable module owns its copy."""
from __future__ import annotations

import ipaddress
import urllib.parse
import urllib.request


def authorize_request(request: urllib.request.Request, api_key: str) -> urllib.request.Request:
    """Attach credentials only on a safe initial request, never to a redirect."""
    if not api_key:
        return request
    parsed = urllib.parse.urlsplit(request.full_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme.lower() != "https" and not (parsed.scheme.lower() == "http" and loopback):
        raise ValueError("ErrorCache credentials require HTTPS or HTTP loopback")
    request.add_unredirected_header("Authorization", f"Bearer {api_key}")
    return request
