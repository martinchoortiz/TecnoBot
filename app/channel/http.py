from __future__ import annotations

from typing import Any

import httpx

from app.channel.errors import ExternalServiceError

_MAX_BODY = 4000


def response_body(response: httpx.Response) -> Any:
    try:
        data = response.json()
    except Exception:
        return (response.text or "")[:_MAX_BODY]
    if isinstance(data, str):
        return data[:_MAX_BODY]
    return data


def raise_for_external_response(provider: str, response: httpx.Response, action: str) -> None:
    url = str(response.request.url) if response.request else None
    if response.is_error:
        raise ExternalServiceError(
            provider,
            f"{action} HTTP {response.status_code}",
            status_code=response.status_code,
            body=response_body(response),
            url=url,
        )
    data = response_body(response)
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ExternalServiceError(
            provider,
            f"{action}: {msg}",
            status_code=response.status_code,
            body=data,
            url=url,
        )


async def external_post(
    provider: str,
    action: str,
    url: str,
    *,
    json: dict[str, Any],
    headers: dict[str, str],
    timeout: float = 20.0,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=json, headers=headers)
    except httpx.RequestError as exc:
        raise ExternalServiceError(provider, f"{action} red: {exc}", url=url) from exc
    raise_for_external_response(provider, response, action)
    return response
