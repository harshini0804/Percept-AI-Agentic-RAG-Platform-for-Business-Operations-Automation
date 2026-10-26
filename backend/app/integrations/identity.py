"""
Who and what this platform is willing to listen to.

Two small allow-lists, kept in code on purpose so they are reviewed
like code:

- GITHUB_LOGIN_TO_OWNER maps a GitHub account to the first name used
  in meeting transcripts (action_items.owner). A merge by an account
  that is not listed is recorded but NOT turned into evidence.
- ALLOWED_GITHUB_REPOS: only merges in these repositories count.

In production these would come from an admin screen or the company
directory; the demo uses fixed values that match the demo owners.
"""

import os
from urllib.parse import urlparse

GITHUB_LOGIN_TO_OWNER = {
    "alex-dev": "alex",
    "priya-dev": "priya",
    "morgan-dev": "morgan",
    "jamie-dev": "jamie",
}

ALLOWED_GITHUB_REPOS = {"acme-corp/platform"}


def owner_for_github_login(login: str | None) -> str | None:
    if not login:
        return None
    return GITHUB_LOGIN_TO_OWNER.get(login.strip().lower())


def allowed_download_hosts() -> list[str]:
    raw = os.getenv("ZOOM_DOWNLOAD_HOSTS", "zoom.us,mock-sources")
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def is_allowed_download_url(url: str | None) -> bool:
    """
    The platform downloads the transcript from a URL that arrives inside
    the message. Even a correctly signed message must only be able to
    point at the sender's own file host, never at an arbitrary address
    inside our network.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_download_hosts())
