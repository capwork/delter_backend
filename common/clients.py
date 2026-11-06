"""Service-to-service REST calls. Base URLs come from settings, so moving a service to another
host (or putting a load balancer in front of N instances) is a configuration change.

Tests inject Starlette TestClients (which are httpx.Client subclasses) instead of real sockets.
"""
from __future__ import annotations

from typing import Any

import httpx

from common.errors import NotFound, UpstreamError
from configuration.settings import Settings


class ServiceRegistry:
    def __init__(self, settings: Settings, clients: dict[str, httpx.Client] | None = None) -> None:
        self._settings = settings
        self._clients: dict[str, httpx.Client] = dict(clients or {})

    def client(self, name: str) -> httpx.Client:
        if name not in self._clients:
            self._clients[name] = httpx.Client(
                base_url=self._settings.service_url(name), timeout=self._settings.upstream_timeout_s
            )
        return self._clients[name]

    def request(self, name: str, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self.client(name).request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{name} is unreachable: {exc}") from exc
        if resp.status_code == 404:
            raise NotFound(_detail(resp))
        if resp.status_code >= 400:
            raise UpstreamError(f"{name} answered {resp.status_code}: {_detail(resp)}")
        return resp

    def json(self, name: str, method: str, path: str, **kwargs: Any) -> Any:
        return self.request(name, method, path, **kwargs).json()

    def paged(self, name: str, path: str, page_size: int = 500, **params: Any) -> list[dict]:
        """Read every item of a `{total, items}` list endpoint."""
        items: list[dict] = []
        offset = 0
        while True:
            body = self.json(name, "GET", path, params={**params, "limit": page_size, "offset": offset})
            items.extend(body["items"])
            offset += page_size
            if offset >= body["total"] or not body["items"]:
                return items

    def close(self) -> None:
        for c in self._clients.values():
            c.close()


def _detail(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except Exception:  # noqa: BLE001
        return resp.text[:200]
