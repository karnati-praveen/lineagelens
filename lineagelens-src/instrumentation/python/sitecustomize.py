import logging
import os
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("lineagelens.auto-instrumentation")

LLM_DOMAINS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
}
PROXY_URL = os.environ.get("LINEAGELENS_LLM_PROXY_URL", "http://127.0.0.1:8080").rstrip("/")
ENABLED = os.environ.get("LINEAGELENS_AUTO_INSTRUMENT", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}


def _is_llm_url(value) -> bool:
    try:
        return urlparse(str(value)).hostname in LLM_DOMAINS
    except Exception:
        return False


def _proxy_url_for(value) -> str:
    target = urlparse(str(value))
    proxy = urlparse(PROXY_URL)
    return urlunparse((proxy.scheme, proxy.netloc, target.path or "/", "", target.query, ""))


def _proxy_headers(headers, target_url: str) -> dict:
    target = urlparse(str(target_url))
    merged = dict(headers or {})
    merged["Host"] = target.netloc
    merged["X-Forwarded-Proto"] = target.scheme or "https"
    return merged


if ENABLED:
    try:
        import httpx

        _original_httpx_request = httpx.Client.request
        _original_httpx_async_request = httpx.AsyncClient.request

        def _absolute_httpx_url(client, request_url):
            url = httpx.URL(request_url)
            if url.is_relative_url:
                url = client.base_url.join(url)
            return str(url)

        def _patched_client_request(self, method, url, **kwargs):
            target_url = _absolute_httpx_url(self, url)
            if not _is_llm_url(target_url):
                return _original_httpx_request(self, method, url, **kwargs)

            kwargs["headers"] = _proxy_headers(kwargs.get("headers"), target_url)
            try:
                return _original_httpx_request(self, method, _proxy_url_for(target_url), **kwargs)
            except httpx.TransportError as exc:
                logger.debug("LineageLens proxy unavailable for httpx request; failing open: %s", exc)
                return _original_httpx_request(self, method, url, **kwargs)

        async def _patched_async_client_request(self, method, url, **kwargs):
            target_url = _absolute_httpx_url(self, url)
            if not _is_llm_url(target_url):
                return await _original_httpx_async_request(self, method, url, **kwargs)

            kwargs["headers"] = _proxy_headers(kwargs.get("headers"), target_url)
            try:
                return await _original_httpx_async_request(self, method, _proxy_url_for(target_url), **kwargs)
            except httpx.TransportError as exc:
                logger.debug("LineageLens proxy unavailable for async httpx request; failing open: %s", exc)
                return await _original_httpx_async_request(self, method, url, **kwargs)

        httpx.Client.request = _patched_client_request
        httpx.AsyncClient.request = _patched_async_client_request
    except ImportError:
        pass

    try:
        import requests

        _original_session_send = requests.Session.send

        def _patched_session_send(self, request, **kwargs):
            original_url = request.url
            if not _is_llm_url(original_url):
                return _original_session_send(self, request, **kwargs)

            request.url = _proxy_url_for(original_url)
            request.headers.update(_proxy_headers(request.headers, original_url))
            try:
                return _original_session_send(self, request, **kwargs)
            except requests.exceptions.RequestException as exc:
                logger.debug("LineageLens proxy unavailable for requests call; failing open: %s", exc)
                request.url = original_url
                request.headers.pop("X-Forwarded-Proto", None)
                return _original_session_send(self, request, **kwargs)
            finally:
                request.url = original_url

        requests.Session.send = _patched_session_send
    except ImportError:
        pass
