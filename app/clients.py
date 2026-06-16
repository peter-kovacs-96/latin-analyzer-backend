import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx
from curl_cffi.requests import AsyncSession as _CffiSession
from curl_cffi.requests import exceptions as _cffi_exc

from app.cache import TTLCache, UpstashCache
from app.config import Settings
from app.models import DownstreamDiagnostic, DownstreamResult, DownstreamStatus
from app import span as _span

_LIS_IMPERSONATE = "chrome110"

_DEFAULT_UDPIPE_MODEL = "latin"
_log = logging.getLogger(__name__)


class DownstreamClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(settings.downstream_concurrency)

    async def start(self) -> None:
        timeout = httpx.Timeout(
            timeout=self.settings.downstream_timeout_seconds,
            connect=self.settings.downstream_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            timeout=timeout,
            verify=self.settings.verify_tls,
            headers={"Accept": "application/json", "User-Agent": self.settings.user_agent},
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_json(self, service: str, url: str, extra_headers: dict[str, str] | None = None) -> DownstreamResult:
        if self._client is None:
            return DownstreamResult(
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.UNAVAILABLE,
                    reason="CLIENT_NOT_STARTED",
                    message=f"{service} client is not started",
                )
            )

        start = time.perf_counter()
        attempts = self.settings.downstream_retries + 1
        last_result: DownstreamResult | None = None

        async with self._semaphore:
            for attempt in range(attempts):
                try:
                    response = await self._client.get(url, headers=extra_headers)
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    if response.status_code == 429:
                        last_result = DownstreamResult(
                            diagnostic=DownstreamDiagnostic(
                                status=DownstreamStatus.RATE_LIMITED,
                                reason="DOWNSTREAM_RATE_LIMITED",
                                message=f"{service} returned HTTP 429",
                                retryable=True,
                                http_status=429,
                                latency_ms=latency_ms,
                            )
                        )
                    elif response.status_code >= 400:
                        retryable = response.status_code in {408, 500, 502, 503, 504}
                        last_result = DownstreamResult(
                            diagnostic=DownstreamDiagnostic(
                                status=DownstreamStatus.HTTP_ERROR,
                                reason="DOWNSTREAM_HTTP_ERROR",
                                message=f"{service} returned HTTP {response.status_code}",
                                retryable=retryable,
                                http_status=response.status_code,
                                latency_ms=latency_ms,
                            )
                        )
                    else:
                        try:
                            return DownstreamResult(
                                data=response.json(),
                                diagnostic=DownstreamDiagnostic(
                                    status=DownstreamStatus.OK,
                                    latency_ms=latency_ms,
                                ),
                            )
                        except ValueError:
                            return DownstreamResult(
                                diagnostic=DownstreamDiagnostic(
                                    status=DownstreamStatus.INVALID_RESPONSE,
                                    reason="INVALID_JSON",
                                    message=f"{service} returned invalid JSON",
                                    latency_ms=latency_ms,
                                )
                            )
                except httpx.TimeoutException:
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    last_result = DownstreamResult(
                        diagnostic=DownstreamDiagnostic(
                            status=DownstreamStatus.TIMEOUT,
                            reason="DOWNSTREAM_TIMEOUT",
                            message=f"{service} timed out",
                            retryable=True,
                            latency_ms=latency_ms,
                        )
                    )
                except httpx.NetworkError as exc:
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    last_result = DownstreamResult(
                        diagnostic=DownstreamDiagnostic(
                            status=DownstreamStatus.NETWORK_ERROR,
                            reason="DOWNSTREAM_NETWORK_ERROR",
                            message=str(exc),
                            retryable=True,
                            latency_ms=latency_ms,
                        )
                    )
                except Exception as exc:
                    latency_ms = int((time.perf_counter() - start) * 1000)
                    return DownstreamResult(
                        diagnostic=DownstreamDiagnostic(
                            status=DownstreamStatus.UNKNOWN_ERROR,
                            reason="UNKNOWN_DOWNSTREAM_ERROR",
                            message=str(exc),
                            latency_ms=latency_ms,
                        )
                    )

                if last_result and not last_result.diagnostic.retryable:
                    return last_result
                if attempt < attempts - 1:
                    await asyncio.sleep(0.1 * (attempt + 1))

        return last_result or DownstreamResult(
            diagnostic=DownstreamDiagnostic(status=DownstreamStatus.UNKNOWN_ERROR, reason="NO_RESULT")
        )


class LatinWordNetClient:
    service_name = "latin_wordnet"

    def __init__(self, http: DownstreamClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self.cache: TTLCache[str, Any] = TTLCache(settings.cache_max_items, settings.cache_ttl_seconds)

    async def lemmatize(self, form: str) -> DownstreamResult:
        cached = self.cache.get(form)
        if cached is not None:
            _log.info("span=%s wordnet form=%r status=ok cached=L1", _span.current(), form)
            return DownstreamResult(
                data=cached,
                diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, cached=True),
            )
        base_url = self.settings.latin_wordnet_base_url.rstrip("/")
        result = await self.http.get_json(self.service_name, f"{base_url}/lemmas/{quote(form)}/")
        if result.diagnostic.status == DownstreamStatus.OK:
            if isinstance(result.data, list):
                data = result.data
            elif isinstance(result.data, dict):
                data = result.data.get("results", [])
            else:
                _log.info("span=%s wordnet form=%r status=invalid_response", _span.current(), form)
                return DownstreamResult(
                    diagnostic=DownstreamDiagnostic(
                        status=DownstreamStatus.INVALID_RESPONSE,
                        reason="INVALID_WORDNET_LEMMA_RESPONSE",
                        message=f"{self.service_name} returned an unexpected lemma response shape",
                    )
                )
            if not data:
                _log.info("span=%s wordnet form=%r status=not_found ms=%s", _span.current(), form, result.diagnostic.latency_ms)
                return DownstreamResult(
                    data=[],
                    diagnostic=DownstreamDiagnostic(
                        status=DownstreamStatus.NOT_FOUND,
                        reason="WORDNET_LEMMA_NOT_FOUND",
                        message=f"{self.service_name} has no entry for this lemma",
                        latency_ms=result.diagnostic.latency_ms,
                    ),
                )
            self.cache.set(form, data)
            _log.info("span=%s wordnet form=%r status=ok hits=%d ms=%s", _span.current(), form, len(data), result.diagnostic.latency_ms)
            return DownstreamResult(data=data, diagnostic=result.diagnostic)
        _log.info("span=%s wordnet form=%r status=%s ms=%s", _span.current(), form, result.diagnostic.status, result.diagnostic.latency_ms)
        return result


class UDPipeClient:
    service_name = "udpipe"

    def __init__(self, http: DownstreamClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self.cache: TTLCache[str, Any] = TTLCache(settings.cache_max_items, settings.cache_ttl_seconds)

    async def process(self, text: str) -> DownstreamResult:
        cached = self.cache.get(text)
        if cached is not None:
            _log.info("span=%s udpipe status=ok cached=L1 text_len=%d", _span.current(), len(text))
            return DownstreamResult(
                data=cached,
                diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, cached=True),
            )

        model = _DEFAULT_UDPIPE_MODEL
        models = await self.http.get_json(self.service_name, f"{self.settings.udpipe_base_url}/models")
        if models.diagnostic.status == DownstreamStatus.OK and isinstance(models.data, dict):
            available = models.data.get("models", {})
            if isinstance(available, dict):
                model = next(
                    (name for name in available if name.lower().startswith("latin")),
                    _DEFAULT_UDPIPE_MODEL,
                )

        params = {
            "tokenizer": "",
            "tagger": "",
            "parser": "",
            "model": model,
            "data": text,
        }
        result = await self.http.get_json(
            self.service_name,
            f"{self.settings.udpipe_base_url}/process?{httpx.QueryParams(params)}",
        )
        if result.diagnostic.status == DownstreamStatus.OK and isinstance(result.data, dict):
            conllu = result.data.get("result", "")
            result.data = conllu
            self.cache.set(text, conllu)
            _log.info("span=%s udpipe status=ok model=%s ms=%s text_len=%d", _span.current(), model, result.diagnostic.latency_ms, len(text))
        else:
            _log.info("span=%s udpipe status=%s ms=%s", _span.current(), result.diagnostic.status, result.diagnostic.latency_ms)
        return result


def _parse_morpheus_response(data: Any) -> list[str]:
    """Extract unique lemmata from Morpheus RDF JSON response."""
    if not isinstance(data, dict):
        return []
    body = data.get("RDF", {}).get("Annotation", {}).get("Body", {})
    bodies = body if isinstance(body, list) else ([body] if body else [])
    lemmata: list[str] = []
    for b in bodies:
        if not isinstance(b, dict):
            continue
        hdwd = b.get("rest", {}).get("entry", {}).get("dict", {}).get("hdwd", {})
        if isinstance(hdwd, dict):
            lemma = hdwd.get("$", "").rstrip("0123456789")  # strip disambiguation suffixes e.g. opus1 → opus
            if lemma and lemma not in lemmata:
                lemmata.append(lemma)
    return lemmata


class MorpheusClient:
    """Perseids Morpheus morphological analysis — used to cross-validate UDPipe lemmata."""
    service_name = "morpheus"
    _BASE = "https://morph.perseids.org/analysis/word"

    def __init__(self, http: DownstreamClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self.cache: TTLCache[str, Any] = TTLCache(settings.cache_max_items, settings.cache_ttl_seconds)

    async def lemmatize(self, form: str) -> DownstreamResult:
        cached = self.cache.get(form)
        if cached is not None:
            _log.info("span=%s morpheus form=%r status=ok cached=L1 lemmata=%r", _span.current(), form, cached)
            return DownstreamResult(
                data=cached,
                diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, cached=True),
            )
        url = f"{self._BASE}?lang=lat&engine=morpheuslat&word={quote(form)}"
        result = await self.http.get_json(self.service_name, url)
        if result.diagnostic.status == DownstreamStatus.OK:
            lemmata = _parse_morpheus_response(result.data)
            self.cache.set(form, lemmata)
            _log.info("span=%s morpheus form=%r status=ok lemmata=%r ms=%s", _span.current(), form, lemmata, result.diagnostic.latency_ms)
            return DownstreamResult(data=lemmata, diagnostic=result.diagnostic)
        _log.info("span=%s morpheus form=%r status=%s ms=%s", _span.current(), form, result.diagnostic.status, result.diagnostic.latency_ms)
        return result


_LIS_HEADERS = {
    "Origin": "https://www.latin-is-simple.com",
    "Referer": "https://www.latin-is-simple.com/",
}


class LatinIsSimpleClient:
    """Latin is Simple vocabulary client.

    Uses curl-cffi with Chrome TLS impersonation to bypass Cloudflare's
    TLS-fingerprint check, which blocks plain httpx/requests calls with HTTP 403.
    No paid proxy service required.
    """
    service_name = "latin_is_simple"

    def __init__(self, http: DownstreamClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self.cache: TTLCache[str, Any] = TTLCache(settings.cache_max_items, settings.cache_ttl_seconds)
        self._upstash = (
            UpstashCache(settings.upstash_redis_url, settings.upstash_redis_token)
            if settings.upstash_redis_url and settings.upstash_redis_token
            else None
        )
        # Persistent session reuses connections across concurrent requests.
        self._session = _CffiSession()

    async def close(self) -> None:
        await self._session.close()
        if self._upstash:
            await self._upstash.close()

    async def search(self, lemma: str) -> DownstreamResult:
        sid = _span.current()

        # L1: in-memory
        cached = self.cache.get(lemma)
        if cached is not None:
            _log.info("span=%s lis form=%r status=ok cached=L1", sid, lemma)
            return DownstreamResult(
                data=cached,
                diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, cached=True),
            )

        # L2: Upstash Redis (persistent across restarts)
        if self._upstash is not None:
            cached = await self._upstash.get(f"lis:{lemma}")
            if cached is not None:
                self.cache.set(lemma, cached)
                _log.info("span=%s lis form=%r status=ok cached=L2", sid, lemma)
                return DownstreamResult(
                    data=cached,
                    diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, cached=True),
                )

        # Live request via curl-cffi (bypasses Cloudflare TLS fingerprint check)
        params = {"query": lemma, "forms_only": "true", "format": "json"}
        url = f"{self.settings.latin_is_simple_base_url}/api/vocabulary/search/?{httpx.QueryParams(params)}"
        _log.info("span=%s lis form=%r live_request", sid, lemma)
        start = time.perf_counter()
        try:
            response = await self._session.get(
                url,
                impersonate=_LIS_IMPERSONATE,
                headers=_LIS_HEADERS,
                timeout=self.settings.downstream_timeout_seconds,
                verify=self.settings.verify_tls,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
        except _cffi_exc.Timeout:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _log.info("span=%s lis form=%r status=timeout ms=%d", sid, lemma, latency_ms)
            return DownstreamResult(
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.TIMEOUT,
                    reason="LIS_TIMEOUT",
                    message=f"{self.service_name} timed out",
                    latency_ms=latency_ms,
                )
            )
        except _cffi_exc.ConnectionError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _log.info("span=%s lis form=%r status=network_error ms=%d err=%r", sid, lemma, latency_ms, str(exc)[:80])
            return DownstreamResult(
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.NETWORK_ERROR,
                    reason="LIS_NETWORK_ERROR",
                    message=str(exc),
                    latency_ms=latency_ms,
                )
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _log.info("span=%s lis form=%r status=unknown_error ms=%d err=%r", sid, lemma, latency_ms, str(exc)[:80])
            return DownstreamResult(
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.UNKNOWN_ERROR,
                    reason="LIS_REQUEST_ERROR",
                    message=str(exc),
                    latency_ms=latency_ms,
                )
            )

        if response.status_code >= 400:
            _log.info("span=%s lis form=%r status=http_error http=%d ms=%d", sid, lemma, response.status_code, latency_ms)
            return DownstreamResult(
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.HTTP_ERROR,
                    reason="LIS_HTTP_ERROR",
                    message=f"{self.service_name} returned HTTP {response.status_code}",
                    http_status=response.status_code,
                    latency_ms=latency_ms,
                )
            )

        try:
            data = response.json()
        except Exception:
            _log.info("span=%s lis form=%r status=invalid_response ms=%d", sid, lemma, latency_ms)
            return DownstreamResult(
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.INVALID_RESPONSE,
                    reason="INVALID_JSON",
                    message=f"{self.service_name} returned invalid JSON",
                    latency_ms=latency_ms,
                )
            )

        self.cache.set(lemma, data)
        if self._upstash is not None:
            await self._upstash.set(f"lis:{lemma}", data)

        if isinstance(data, list) and not data:
            _log.info("span=%s lis form=%r status=not_found ms=%d", sid, lemma, latency_ms)
            return DownstreamResult(
                data=[],
                diagnostic=DownstreamDiagnostic(
                    status=DownstreamStatus.NOT_FOUND,
                    reason="LIS_LEMMA_NOT_FOUND",
                    message=f"{self.service_name} has no entry for this lemma",
                    latency_ms=latency_ms,
                ),
            )

        _log.info("span=%s lis form=%r status=ok hits=%d ms=%d", sid, lemma, len(data) if isinstance(data, list) else -1, latency_ms)
        return DownstreamResult(
            data=data,
            diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, latency_ms=latency_ms),
        )

