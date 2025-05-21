from contextlib import asynccontextmanager
from typing import Any

import httpx


class HttpxClientSessionResponseAdapter:
    def __init__(self, response: httpx.Response):
        self._response = response

    @property
    def status(self) -> int:
        return self._response.status_code

    @property
    def headers(self) -> httpx.Headers:
        return self._response.headers

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    async def json(self) -> Any:
        return self._response.json()

    async def text(self) -> str:
        return self._response.text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self._response.aclose()


class HttpxClientSessionAdapter:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def close(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def get(self, url: str, **kwargs) -> HttpxClientSessionResponseAdapter:
        response = await self._client.get(url, **kwargs)
        try:
            yield HttpxClientSessionResponseAdapter(response)
        finally:
            await response.aclose()

    async def post(self, url: str, **kwargs) -> HttpxClientSessionResponseAdapter:
        response = await self._client.post(url, **kwargs)
        return HttpxClientSessionResponseAdapter(response)

    async def delete(self, url: str, **kwargs) -> HttpxClientSessionResponseAdapter:
        response = await self._client.delete(url, **kwargs)
        return HttpxClientSessionResponseAdapter(response)

    async def put(self, url: str, **kwargs) -> HttpxClientSessionResponseAdapter:
        response = await self._client.put(url, **kwargs)
        return HttpxClientSessionResponseAdapter(response)
