"""
Signature checks for incoming webhooks.

A webhook endpoint is a public door: anything on the internet can send
it a request. The sender and this platform therefore share a secret,
and the sender signs every message with it. A message whose signature
does not match is rejected without being read any further. This is the
integration-side answer to "anyone can submit unrelated documents".

Schemes (both verified against the providers' documentation):

- GitHub: header X-Hub-Signature-256 = "sha256=" + hex HMAC-SHA256 of
  the raw request body, keyed with the webhook secret.
- Zoom: header x-zm-signature = "v0=" + hex HMAC-SHA256 of the string
  "v0:{x-zm-request-timestamp}:{raw body}", keyed with the secret
  token. Zoom also sends an `endpoint.url_validation` event when a
  webhook URL is first registered; the receiver proves it holds the
  secret by returning HMAC-SHA256(plainToken).

Comparison is always constant-time (hmac.compare_digest). Zoom
messages older than a few minutes are refused so a captured message
cannot be replayed later.
"""

import hashlib
import hmac
import time

ZOOM_TIMESTAMP_TOLERANCE_SECONDS = 300


def _hex_hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def github_signature(secret: str, raw_body: bytes) -> str:
    """The header value GitHub would send (also used by the pretend sender)."""
    return "sha256=" + _hex_hmac(secret, raw_body)


def verify_github_signature(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    if not secret or not header_value or not header_value.startswith("sha256="):
        return False
    return hmac.compare_digest(github_signature(secret, raw_body), header_value)


def zoom_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    """The header value Zoom would send (also used by the pretend sender)."""
    message = f"v0:{timestamp}:".encode("utf-8") + raw_body
    return "v0=" + _hex_hmac(secret, message)


def verify_zoom_signature(
    secret: str,
    raw_body: bytes,
    signature: str | None,
    timestamp: str | None,
    now: float | None = None,
) -> bool:
    if not secret or not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - sent_at) > ZOOM_TIMESTAMP_TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(zoom_signature(secret, timestamp, raw_body), signature)


def zoom_url_validation_response(secret: str, plain_token: str) -> dict:
    return {
        "plainToken": plain_token,
        "encryptedToken": _hex_hmac(secret, plain_token.encode("utf-8")),
    }
