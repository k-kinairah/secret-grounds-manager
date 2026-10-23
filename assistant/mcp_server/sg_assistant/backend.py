"""Thin read-only HTTP client for the Secret Grounds backend.

Only GET is exposed on purpose: phase 1 of the assistant is read-only, and
the backend independently rejects any non-GET request made with the
assistant bearer token (403).
"""

from typing import Any

import httpx


class BackendError(Exception):
    """A backend call failed. `str(error)` is safe to show to the user/LLM."""


class BackendClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        # Injected only by tests (httpx.MockTransport); None = real network.
        self._transport = transport

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise BackendError(
                "The assistant is not configured: SG_ASSISTANT_TOKEN is not set for the MCP server."
            )
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.get(path, params=params, headers=headers)
        except httpx.TimeoutException:
            raise BackendError(f"The Secret Grounds backend at {self.base_url} did not respond in time.") from None
        except httpx.HTTPError:
            raise BackendError(
                f"Could not reach the Secret Grounds backend at {self.base_url}. Is it running?"
            ) from None

        if response.status_code == 401:
            raise BackendError(
                "The backend rejected the assistant token (401). Check that SG_ASSISTANT_TOKEN matches "
                "on both the backend and the MCP server, and that SG_ASSISTANT_USERNAME is an active staff user."
            )
        if response.status_code == 403:
            raise BackendError(
                "The assistant's staff account is not allowed to read this (403). "
                "It may need a higher role, or the request was not read-only."
            )
        if response.status_code == 404:
            raise BackendError(f"Not found: {_detail(response) or path}")
        if response.status_code >= 400:
            detail = _detail(response)
            suffix = f": {detail}" if detail else ""
            raise BackendError(f"The backend returned an error ({response.status_code}){suffix}")

        try:
            return response.json()
        except ValueError:
            raise BackendError("The backend returned a response that was not valid JSON.") from None


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return ""
