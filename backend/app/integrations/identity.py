"""
Who and what this platform is willing to listen to.

Two small allow-lists:

- GitHub account -> owner: maps a GitHub login to the first name used in
  meeting transcripts (action_items.owner). Activity from an account that
  is not listed is recorded in the Live Feed but NOT turned into evidence.
- Allowed repositories: only activity in these repositories counts.

Built-in demo values always apply. Settings ADD to them (they never
remove them), so the pretend senders keep working next to a real
GitHub connection:

    GITHUB_LOGIN_MAP="harshini0825=morgan,other-login=alex"
    GITHUB_ALLOWED_REPOS="harshini0825/morgan-percept-ai-demo"

In production these would come from an admin screen or the company
directory.
"""

import os
from urllib.parse import urlparse

DEFAULT_GITHUB_LOGIN_TO_OWNER = {
    "alex-dev": "alex",
    "priya-dev": "priya",
    "morgan-dev": "morgan",
    "jamie-dev": "jamie",
}

DEFAULT_ALLOWED_GITHUB_REPOS = {"acme-corp/platform"}


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def github_login_map() -> dict[str, str]:
    mapping = dict(DEFAULT_GITHUB_LOGIN_TO_OWNER)
    for pair in _split(os.getenv("GITHUB_LOGIN_MAP", "")):
        login, sep, owner = pair.partition("=")
        if sep and login.strip() and owner.strip():
            mapping[login.strip().lower()] = owner.strip().lower()
    return mapping


def allowed_github_repos() -> set[str]:
    repos = {r.lower() for r in DEFAULT_ALLOWED_GITHUB_REPOS}
    repos.update(r.lower() for r in _split(os.getenv("GITHUB_ALLOWED_REPOS", "")))
    return repos


def owner_for_github_login(login: str | None) -> str | None:
    if not login:
        return None
    return github_login_map().get(login.strip().lower())


def is_allowed_repo(repo: str | None) -> bool:
    return bool(repo) and repo.strip().lower() in allowed_github_repos()


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
