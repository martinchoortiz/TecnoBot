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


def _error_text(data: Any) -> str | None:
    if not isinstance(data, dict) or not data.get("error"):
        return None
    err = data["error"]
    if isinstance(err, dict):
        text = err.get("message") or err.get("error")
        return str(text) if text else None
    return str(err)


def raise_for_external_response(provider: str, response: httpx.Response, action: str) -> None:
    url = str(response.request.url) if response.request else None
    data = response_body(response)
    detail = _error_text(data)
    if response.is_error:
        message = f"{action} HTTP {response.status_code}"
        if detail:
            message = f"{message}: {detail}"
        raise ExternalServiceError(
            provider,
            message,
            status_code=response.status_code,
            body=data,
            url=url,
        )
    if detail:
        raise ExternalServiceError(
            provider,
            f"{action}: {detail}",
            status_code=response.status_code,
            body=data,
            url=url,
        )


async def external_request(
    method: str,
    provider: str,
    action: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str],
    timeout: float = 20.0,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, json=json, headers=headers)
    except httpx.RequestError as exc:
        raise ExternalServiceError(provider, f"{action} red: {exc}", url=url) from exc
    raise_for_external_response(provider, response, action)
    return response


async def external_post(
    provider: str,
    action: str,
    url: str,
    *,
    json: dict[str, Any],
    headers: dict[str, str],
    timeout: float = 20.0,
) -> httpx.Response:
    return await external_request(
        "POST", provider, action, url, json=json, headers=headers, timeout=timeout
    )


async def external_get(
    provider: str,
    action: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float = 20.0,
) -> httpx.Response:
    return await external_request(
        "GET", provider, action, url, headers=headers, timeout=timeout
    )


async def external_patch(
    provider: str,
    action: str,
    url: str,
    *,
    json: dict[str, Any],
    headers: dict[str, str],
    timeout: float = 20.0,
) -> httpx.Response:
    return await external_request(
        "PATCH", provider, action, url, json=json, headers=headers, timeout=timeout
    )
