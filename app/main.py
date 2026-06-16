import json as _json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.analyzer import AnalyzerService
from app.clients import DownstreamClient, LatinIsSimpleClient, LatinWordNetClient, MorpheusClient, UDPipeClient
from app.config import get_settings
from app.i18n import translate_response_dict
from app.latin import split_into_sentence_groups
from app import span as _span
from app.logging_setup import setup as _setup_logging

_setup_logging()
_log = logging.getLogger(__name__)

settings = get_settings()
http_client = DownstreamClient(settings)
wordnet_client = LatinWordNetClient(http_client, settings)
udpipe_client = UDPipeClient(http_client, settings)
latin_is_simple_client = LatinIsSimpleClient(http_client, settings)
morpheus_client = MorpheusClient(http_client, settings)
analyzer = AnalyzerService(wordnet_client, udpipe_client, latin_is_simple_client, morpheus_client)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await http_client.start()
    yield
    await http_client.close()
    await latin_is_simple_client.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/debug/lis")
async def debug_lis(word: str = Query(default="amor")) -> dict:
    """Debug endpoint: tries multiple approaches to reach LIS and reports results."""
    url = f"{settings.latin_is_simple_base_url}/api/vocabulary/search/?query={word}&forms_only=true&format=json"
    results = {}

    # Attempt 1: httpx with browser-like headers
    try:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Origin": "https://www.latin-is-simple.com",
            "Referer": "https://www.latin-is-simple.com/",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Sec-CH-UA": '"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"Windows"',
        }
        async with httpx.AsyncClient(verify=False, http2=True) as client:
            response = await client.get(url, headers=headers, timeout=10)
        results["httpx_full_headers"] = {
            "status_code": response.status_code,
            "cf_mitigated": response.headers.get("cf-mitigated"),
            "body_preview": response.text[:200],
        }
    except Exception as exc:
        results["httpx_full_headers"] = {"error": str(exc)}

    # Attempt 2: curl-cffi Chrome impersonation
    try:
        from curl_cffi import requests as cffi_requests
        response = cffi_requests.get(url, impersonate="chrome", timeout=10, verify=False)
        results["curl_cffi_chrome"] = {
            "status_code": response.status_code,
            "cf_mitigated": response.headers.get("cf-mitigated"),
            "body_preview": response.text[:200],
        }
    except ImportError:
        results["curl_cffi_chrome"] = {"error": "curl-cffi not installed"}
    except Exception as exc:
        results["curl_cffi_chrome"] = {"error": str(exc)}

    # Attempt 3: curl-cffi Firefox impersonation
    try:
        from curl_cffi import requests as cffi_requests
        response = cffi_requests.get(url, impersonate="firefox", timeout=10, verify=False)
        results["curl_cffi_firefox"] = {
            "status_code": response.status_code,
            "cf_mitigated": response.headers.get("cf-mitigated"),
            "body_preview": response.text[:200],
        }
    except ImportError:
        results["curl_cffi_firefox"] = {"error": "curl-cffi not installed"}
    except Exception as exc:
        results["curl_cffi_firefox"] = {"error": str(exc)}

    return {"url": url, "results": results}


@app.post("/analyze/stream")
async def analyze_stream_endpoint(
    request: Request,
    mode: str = Query(default="sentence"),
    lang: str = Query(default="hu"),
) -> StreamingResponse:
    """Stream sentence-by-sentence morphological analysis of a multi-line Latin text.

    Request body : raw UTF-8 Latin text.
    ?mode=sentence  (default) boundary at blank line or line ending . ! ?
    ?mode=stanza    boundary at blank line only
    ?lang=hu        (default) Hungarian morphological labels
    ?lang=en        English labels

    Each NDJSON object is one of:
      {"line_number": N, "empty": true}
      {"sentence_number": N, "lines": [<AnalysisResponse>, ...]}
    """
    body = await request.body()
    text = body.decode("utf-8", errors="replace")

    async def generate():
        sid = _span.new()
        _log.info("span=%s analyze_start mode=%s lang=%s chars=%d", sid, mode, lang, len(text))

        lines = text.splitlines()
        groups = split_into_sentence_groups(lines, mode=mode)

        # Map each non-blank line index to its sentence group index.
        line_to_group: dict[int, int] = {
            idx: g
            for g, group in enumerate(groups)
            for idx in group
        }

        group_cache: dict[int, list] = {}
        sentence_counter = 0

        for line_number, line in enumerate(lines, start=1):
            line_idx = line_number - 1
            if not line.strip():
                yield _json.dumps({"line_number": line_number, "empty": True}, ensure_ascii=False) + "\n"
                continue

            g_idx = line_to_group[line_idx]

            if g_idx not in group_cache:
                group = groups[g_idx]
                group_lines = [lines[i].strip() for i in group]
                group_cache[g_idx] = await analyzer.analyze_group(group_lines)
                sentence_counter += 1
                # Emit the whole sentence as one NDJSON object
                sentence_results = group_cache[g_idx]
                sentence_lines = [
                    {"line_number": groups[g_idx][j] + 1, **translate_response_dict(r.model_dump(), lang)}
                    for j, r in enumerate(sentence_results)
                ]
                yield _json.dumps(
                    {"sentence_number": sentence_counter, "lines": sentence_lines},
                    ensure_ascii=False,
                ) + "\n"

        _log.info("span=%s analyze_done sentences=%d", sid, sentence_counter)

    return StreamingResponse(generate(), media_type="application/x-ndjson")
