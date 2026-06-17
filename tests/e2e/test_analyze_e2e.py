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


# ---------------------------------------------------------------------------
# Regression: real-world problematic words (from progihoz.docx)
#
# Each test below pins a word that exposed a distinct pipeline bug.  Keep them
# green so later changes don't silently reintroduce the failure.  See the
# analyzer/latin source comments for the mechanism behind each fix.
# ---------------------------------------------------------------------------

def _word(result: dict, form: str) -> dict:
    return next(w for w in result["words"] if w["form"] == form)


def test_enclitic_que_is_analysed(backend_url: str) -> None:
    """'gregesque' = greges (grex) + enclitic -que.  UDPipe emits a multiword
    token ('1-2 gregesque' → greges + que); the analyzer must collapse it to the
    head sub-token instead of dropping the word as unrecognised."""
    result = stream_line(backend_url, "Quid mihi, si pereunt homines, armenta, gregesque;?")
    greg = _word(result, "gregesque")
    assert greg["lemma"] == "grex"
    assert greg["upos"] == "NOUN"
    assert greg["confidence"] == "full"
    assert "flock" in greg["meaning"].lower() or "herd" in greg["meaning"].lower()


def test_punctuation_glued_word_after_semicolon(backend_url: str) -> None:
    """'ferit;surdis' has no space, so UDPipe glues ';' onto 'surdis'.  The
    alignment must strip edge punctuation so the bare 'surdis' is recognised."""
    result = stream_line(backend_url, "Haec ferit, illa ferit;surdis haec auribus, illa")
    surdis = _word(result, "surdis")
    assert surdis["lemma"] == "surdus"
    assert surdis["confidence"] == "full"
    assert "deaf" in surdis["meaning"].lower()


def test_ferit_morpheus_correction_preserved(backend_url: str) -> None:
    """README invariant: UDPipe lemmatises 'ferit' as 'fero'; Morpheus corrects
    it to 'ferio' (to strike).  The alignment fixes must not break this."""
    result = stream_line(backend_url, "Haec ferit, illa ferit;surdis haec auribus, illa")
    ferits = [w for w in result["words"] if w["form"] == "ferit"]
    assert len(ferits) == 2
    for word in ferits:
        assert word["lemma"] == "ferio"
        assert word["confidence"] == "full"


def test_word_wrapped_in_typographic_quotes(backend_url: str) -> None:
    """'„accipe”' — UDPipe glues the closing curly quote onto the word.  The bare
    'accipe' must still resolve (lemma corrected by Morpheus)."""
    result = stream_line(backend_url, "Quum Venerem aspicerem sine flammis; „accipe”, dixi,")
    accipe = _word(result, "accipe")
    assert accipe["lemma"] == "accipio"
    assert accipe["confidence"] == "full"
    assert "accept" in accipe["meaning"].lower() or "receive" in accipe["meaning"].lower()


def test_lis_headword_differs_from_lemma(backend_url: str) -> None:
    """'faucibus' → UDPipe lemma 'fauces', but the LIS headword is 'faux'.  The
    LIS matcher's POS fallback must accept the single matching noun result."""
    result = stream_line(backend_url, "Insidet et siccis faucibus atra fames?")
    fauc = _word(result, "faucibus")
    assert fauc["confidence"] == "full"
    assert fauc["meaning"]  # non-empty
    assert "faux" in fauc["dictionary_form"].lower()


def test_wrong_udpipe_lemma_recovered_via_lis(backend_url: str) -> None:
    """'Venerem' (acc. of Venus) → UDPipe mis-lemmatises as 'venio' and Morpheus
    returns nothing for the capitalised form, but LIS finds 'Venus'.  The POS
    fallback must surface the meaning even though the lemma stays UDPipe's."""
    result = stream_line(backend_url, "Quum Venerem aspicerem sine flammis; „accipe”, dixi,")
    ven = _word(result, "Venerem")
    assert ven["confidence"] == "full"
    assert "venus" in ven["meaning"].lower()
    assert "venus" in ven["dictionary_form"].lower()


def test_eiusdem_form_based_lis_lookup_still_works(backend_url: str) -> None:
    """Guard the surface-form LIS lookup: 'eiusdem' (a form of 'idem') only
    resolves because LIS is queried by surface form, not by lemma."""
    result = stream_line(backend_url, "De statua eiusdem")
    eiusdem = _word(result, "eiusdem")
    assert eiusdem["lemma"] == "idem"
    assert eiusdem["confidence"] == "full"
    assert "same" in eiusdem["meaning"].lower()
