"""
Public-exposure guard.

To let GitHub reach a laptop, a tunnel (Cloudflare Tunnel, ngrok) gives
it a public web address. That address would expose the WHOLE backend —
submissions, escalations, everything — to anyone who learns the URL.

This guard closes that: a request that did not arrive through a local
address (localhost, the Docker service names, ...) is only allowed to
reach the two webhook endpoints, which are protected by their own
signature check. Everything else answers "not found". Requests from the
browser, the other containers and the tests all use local addresses and
are unaffected.

The check is on the request's Host header, which a tunnel leaves as the
public hostname. LOCAL_HOSTS can extend the list of local names.
"""

import os

from starlette.responses import JSONResponse

WEBHOOK_PATHS = {"/integrations/github", "/integrations/zoom"}
_DEFAULT_LOCAL_HOSTS = {
    "localhost", "127.0.0.1", "[::1]", "backend", "rag_backend", "host.docker.internal", "testserver",
}


def local_hosts() -> set[str]:
    extra = {h.strip().lower() for h in os.getenv("LOCAL_HOSTS", "").split(",") if h.strip()}
    return _DEFAULT_LOCAL_HOSTS | extra


def _request_host(scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"host":
            host = value.decode("latin-1").strip().lower()
            if host.startswith("["):  # IPv6 literal, e.g. [::1]:8000
                return host.split("]")[0] + "]"
            return host.split(":")[0]
    return ""


class PublicExposureGuard:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            host = _request_host(scope)
            is_webhook = scope["method"] == "POST" and scope["path"] in WEBHOOK_PATHS
            if host not in local_hosts() and not is_webhook:
                await JSONResponse({"detail": "Not found."}, status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)
