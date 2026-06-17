"""End-to-end tests for the Latin Analyzer backend against real downstream APIs.

The backend process is started once per session (scope="session") and all
tests share it.  External API calls are real – no mocks.

The only non-health endpoint is POST /analyze/stream; all tests use that.
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator

import pytest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(url: str, timeout: int = 10) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def wait_for_health(base_url: str) -> None:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, payload = request_json(f"{base_url}/health", timeout=3)
            if status == 200 and payload == {"ok": True}:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"service did not become healthy at {base_url}") from last_error


@pytest.fixture(scope="session")
def backend_url() -> Iterator[str]:
    port = free_port()
    env = {
        **os.environ,
        "LATIN_ANALYZER_DOWNSTREAM_TIMEOUT_SECONDS": "12",
        "LATIN_ANALYZER_DOWNSTREAM_CONNECT_TIMEOUT_SECONDS": "6",
        "LATIN_ANALYZER_DOWNSTREAM_RETRIES": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_for_health(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def stream(base_url: str, text: str, timeout: int = 60, lang: str | None = "en") -> list[dict]:
    """POST raw text to /analyze/stream and return all NDJSON objects.

    Defaults to lang=en so test assertions on label strings remain stable
    regardless of the server-side default language. Pass lang=None to omit
    the ?lang= parameter and exercise the server default (Hungarian).
    """
    url = f"{base_url}/analyze/stream" + (f"?lang={lang}" if lang is not None else "")
    req = urllib.request.Request(
        url,
        data=text.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return [json.loads(line) for line in raw.strip().splitlines() if line.strip()]


def get_sentence(base_url: str, text: str, lang: str | None = "en") -> dict:
    """Send one sentence (single or multi-line) and return the sentence chunk."""
    results = stream(base_url, text, lang=lang)
    sentences = [r for r in results if "lines" in r]
    assert len(sentences) == 1
    return sentences[0]


def stream_line(base_url: str, text: str) -> dict:
    """Single-line convenience: return the first line's word-level result."""
    sentence = get_sentence(base_url, text)
    assert len(sentence["lines"]) == 1
    return sentence["lines"][0]


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------

def test_amor_vincit_omnia(backend_url: str) -> None:
    result = stream_line(backend_url, "Amor vincit omnia")
    assert result["text"] == "Amor vincit omnia"
    assert result["summary"]["used_udpipe"] is True
    assert result["summary"]["word_count"] == 3
    assert result["summary"]["partial_failure"] is False
    assert [w["form"] for w in result["words"]] == ["Amor", "vincit", "omnia"]
    assert all(w["lemma"] for w in result["words"])
    assert all(w["downstreams"]["latin_wordnet"]["status"] == "ok" for w in result["words"])
    assert all(w["downstreams"]["latin_is_simple"]["status"] == "ok" for w in result["words"])
    assert all(w["confidence"] == "full" for w in result["words"])


def test_ambiguous_malo_malo_disambiguated_by_context(backend_url: str) -> None:
    result = stream_line(backend_url, "malo malo")
    assert result["summary"]["used_udpipe"] is True
    assert [w["form"] for w in result["words"]] == ["malo", "malo"]
    # UDPipe assigns different lemmas based on sentence context
    assert result["words"][0]["lemma"] != result["words"][1]["lemma"]
    # Different dependency roles prove contextual disambiguation fired
    assert result["words"][0]["syntactic_role"] != result["words"][1]["syntactic_role"]


def test_unknown_word_has_no_dictionary_hit(backend_url: str) -> None:
    result = stream_line(backend_url, "zzzxqvnonlatinword")
    word = result["words"][0]
    assert word["downstreams"]["latin_wordnet"]["status"] == "not_found"
    assert word["downstreams"]["latin_is_simple"]["status"] == "not_found"
    assert word["meaning"] == ""
    assert word["confidence"] == "form_only"


def test_line_with_no_word_tokens(backend_url: str) -> None:
    result = stream_line(backend_url, "123 !!!")
    assert result["summary"]["word_count"] == 0
    assert result["words"] == []


# ---------------------------------------------------------------------------
# Structured morphology
# ---------------------------------------------------------------------------

def test_amor_morphology(backend_url: str) -> None:
    result = stream_line(backend_url, "Amor vincit omnia")
    by_form = {w["form"]: w for w in result["words"]}
    amor = by_form["Amor"]

    assert amor["upos"] == "NOUN"
    assert amor["morphology"]["pos"] == "noun"
    assert amor["morphology"]["case"] == "nominative"
    assert amor["morphology"]["number"] == "singular"
    assert amor["morphology"]["gender"] == "masculine"
    # Verbal features not applicable to nouns
    assert amor["morphology"]["tense"] is None
    assert amor["morphology"]["mood"] is None
    assert amor["morphology"]["voice"] is None
    assert amor["morphology"]["person"] is None
    assert amor["syntactic_role"] == "subject"


def test_vincit_morphology(backend_url: str) -> None:
    result = stream_line(backend_url, "Amor vincit omnia")
    by_form = {w["form"]: w for w in result["words"]}
    vincit = by_form["vincit"]

    assert vincit["upos"] == "VERB"
    assert vincit["morphology"]["pos"] == "verb"
    assert vincit["morphology"]["person"] == "3"
    assert vincit["morphology"]["number"] == "singular"
    assert vincit["morphology"]["tense"] == "present"
    assert vincit["morphology"]["mood"] == "indicative"
    assert vincit["morphology"]["voice"] == "active"
    # Nominal features not applicable to finite verbs
    assert vincit["morphology"]["case"] is None
    assert vincit["syntactic_role"] == "predicate"


def test_form_only_word_has_null_morphology(backend_url: str) -> None:
    """A word UDPipe doesn't recognise must have all morphology fields null."""
    result = stream_line(backend_url, "zzzxqvnonlatinword")
    word = result["words"][0]
    assert word["confidence"] == "form_only"
    assert word["lemma"] is None
    m = word["morphology"]
    for field in ("pos", "case", "number", "gender", "person", "tense", "mood", "voice"):
        assert m[field] is None, f"expected {field} to be null, got {m[field]!r}"
    assert word["syntactic_role"] is None


# ---------------------------------------------------------------------------
# Meaning & dictionary_form
# ---------------------------------------------------------------------------

def test_amor_meaning_and_dictionary_form(backend_url: str) -> None:
    result = stream_line(backend_url, "Amor")
    amor = result["words"][0]
    assert amor["lemma"].lower() == "amor"
    assert "love" in amor["meaning"].lower(), f"got: {amor['meaning']!r}"
    assert "amor" in amor["dictionary_form"].lower()
    # Should contain declension info (comma-separated principal parts)
    assert "," in amor["dictionary_form"]


def test_vincit_meaning(backend_url: str) -> None:
    result = stream_line(backend_url, "vincit")
    word = result["words"][0]
    assert word["lemma"].lower() in ("vinco", "uinco")
    assert any(w in word["meaning"].lower() for w in ("conquer", "win", "defeat", "overcome")), (
        f"got: {word['meaning']!r}"
    )


def test_confident_words_expose_lemma_and_morphology(backend_url: str) -> None:
    """Words with full or no_meaning confidence always have a non-null lemma and pos.

    This is the structural invariant that makes no_meaning confidence useful:
    even when LIS has no English meaning, lemma and morphology are still reliable.
    """
    result = stream_line(backend_url, "Amor vincit omnia")
    for word in result["words"]:
        assert word["confidence"] in ("full", "no_meaning", "form_only")
        if word["confidence"] in ("full", "no_meaning"):
            assert word["lemma"] is not None
            assert word["morphology"]["pos"] is not None


# ---------------------------------------------------------------------------
# Streaming behaviour
# ---------------------------------------------------------------------------

def test_stream_returns_one_sentence_for_unpunctuated_lines(backend_url: str) -> None:
    poem = "Amor vincit omnia\net nos cedamus Amori"
    results = stream(backend_url, poem)
    sentences = [r for r in results if "lines" in r]
    assert len(sentences) == 1
    assert sentences[0]["sentence_number"] == 1
    assert len(sentences[0]["lines"]) == 2
    assert sentences[0]["lines"][0]["summary"]["word_count"] == 3
    assert sentences[0]["lines"][1]["summary"]["word_count"] == 4


def test_stream_blank_lines_are_passed_through(backend_url: str) -> None:
    text = "Amor vincit omnia\n\net nos cedamus Amori"
    results = stream(backend_url, text)
    assert len(results) == 3
    sentences = [r for r in results if "lines" in r]
    empties = [r for r in results if r.get("empty")]
    assert len(sentences) == 2
    assert len(empties) == 1
    assert empties[0]["line_number"] == 2
    assert sentences[0]["lines"][0]["line_number"] == 1
    assert sentences[1]["lines"][0]["line_number"] == 3


def test_stream_preserves_order_across_multiple_lines(backend_url: str) -> None:
    poem = "\n".join([
        "Arma virumque cano",
        "Troiae qui primus ab oris",
        "Italiam fato profugus",
    ])
    results = stream(backend_url, poem)
    sentences = [r for r in results if "lines" in r]
    assert len(sentences) == 1
    assert sentences[0]["sentence_number"] == 1
    assert len(sentences[0]["lines"]) == 3
    for i, line_result in enumerate(sentences[0]["lines"], start=1):
        assert line_result["line_number"] == i
        assert line_result["summary"]["word_count"] >= 3


def test_cross_line_sentence_context(backend_url: str) -> None:
    """Subject on line 1, predicate on line 2 — UDPipe uses full sentence context."""
    sentence = get_sentence(backend_url, "Amor\nvincit omnia")
    assert len(sentence["lines"]) == 2
    amor = sentence["lines"][0]["words"][0]
    vincit = sentence["lines"][1]["words"][0]
    assert amor["syntactic_role"] == "subject"
    assert vincit["syntactic_role"] == "predicate"


# ---------------------------------------------------------------------------
# Localisation
# ---------------------------------------------------------------------------

def test_hungarian_labels_returned_by_default(backend_url: str) -> None:
    """With no ?lang= param the server defaults to Hungarian labels."""
    sentence = get_sentence(backend_url, "Amor vincit omnia", lang=None)
    words = {w["form"]: w for w in sentence["lines"][0]["words"]}

    amor = words["Amor"]
    assert amor["morphology"]["pos"] == "főnév"
    assert amor["morphology"]["case"] == "alanyeset"
    assert amor["syntactic_role"] == "alany"
    assert amor["confidence"] == "teljes"

    vincit = words["vincit"]
    assert vincit["syntactic_role"] == "állítmány"
    assert vincit["morphology"]["pos"] == "ige"
    assert vincit["morphology"]["tense"] == "jelen idő"
    assert vincit["morphology"]["mood"] == "kijelentő mód"


def test_english_labels_with_lang_en(backend_url: str) -> None:
    """?lang=en returns unmodified English labels."""
    result = stream_line(backend_url, "Amor vincit omnia")   # stream_line pins lang=en
    amor = next(w for w in result["words"] if w["form"] == "Amor")
    assert amor["morphology"]["pos"] == "noun"
    assert amor["morphology"]["case"] == "nominative"
    assert amor["syntactic_role"] == "subject"
    assert amor["confidence"] == "full"
