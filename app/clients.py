import asyncio
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.cache import TTLCache
from app.config import Settings
from app.models import DownstreamDiagnostic, DownstreamResult, DownstreamStatus

_DEFAULT_UDPIPE_MODEL = "latin"


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
                return DownstreamResult(
                    diagnostic=DownstreamDiagnostic(
                        status=DownstreamStatus.INVALID_RESPONSE,
                        reason="INVALID_WORDNET_LEMMA_RESPONSE",
                        message=f"{self.service_name} returned an unexpected lemma response shape",
                    )
                )
            if not data:
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
            return DownstreamResult(data=data, diagnostic=result.diagnostic)
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
        return result


_LIS_HEADERS = {
    "Origin": "https://www.latin-is-simple.com",
    "Referer": "https://www.latin-is-simple.com/",
}


class LatinIsSimpleClient:
    service_name = "latin_is_simple"

    def __init__(self, http: DownstreamClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings
        self.cache: TTLCache[str, Any] = TTLCache(settings.cache_max_items, settings.cache_ttl_seconds)

    async def search(self, lemma: str) -> DownstreamResult:
        cached = self.cache.get(lemma)
        if cached is not None:
            return DownstreamResult(
                data=cached,
                diagnostic=DownstreamDiagnostic(status=DownstreamStatus.OK, cached=True),
            )
        params = {"query": lemma, "forms_only": "true", "format": "json"}
        direct_url = f"{self.settings.latin_is_simple_base_url}/api/vocabulary/search/?{httpx.QueryParams(params)}"
        result = await self.http.get_json(
            self.service_name,
            direct_url,
            extra_headers=_LIS_HEADERS,
        )
        if (
            result.diagnostic.status == DownstreamStatus.HTTP_ERROR
            and result.diagnostic.http_status == 403
            and self.settings.zenrows_api_key
        ):
            zenrows_url = (
                f"https://api.zenrows.com/v1/"
                f"?apikey={self.settings.zenrows_api_key}"
                f"&url={quote(direct_url)}"
                f"&js_render=true"
            )
            result = await self.http.get_json(self.service_name, zenrows_url)
        if result.diagnostic.status == DownstreamStatus.OK:
            if isinstance(result.data, list) and not result.data:
                return DownstreamResult(
                    data=[],
                    diagnostic=DownstreamDiagnostic(
                        status=DownstreamStatus.NOT_FOUND,
                        reason="LIS_LEMMA_NOT_FOUND",
                        message=f"{self.service_name} has no entry for this lemma",
                        latency_ms=result.diagnostic.latency_ms,
                    ),
                )
            self.cache.set(lemma, result.data)
        return result
