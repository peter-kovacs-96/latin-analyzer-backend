"""Deterministic regression tests for individual problematic words.

Each word below exposed a distinct analysis bug on a real poem (progihoz.docx).
These tests pin the *pipeline's* handling of those words so later changes can't
silently reintroduce the failure.

To stay reliable they do NOT hit the live downstream APIs on every run (which
throttle our IP and make the suite flaky).  Instead they run the real
``AnalyzerService`` in-process against a **cassette** of recorded *real*
downstream responses, committed under ``fixtures/downstream_cassette.json``.

Refresh the cassette against the live APIs when the pipeline's inputs change::

    LATIN_ANALYZER_E2E_RECORD=1 \
    LATIN_ANALYZER_LATIN_IS_SIMPLE_BASE_URL=https://latin-is-simple-proxy.gondir96.workers.dev \
    LATIN_ANALYZER_UPSTASH_REDIS_URL=... LATIN_ANALYZER_UPSTASH_REDIS_TOKEN=... \
    python -m pytest tests/e2e/test_word_regression.py

In record mode each downstream call is delegated to the real client and stored;
in replay mode (the default) responses come only from the cassette.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from app.analyzer import AnalyzerService
from app.config import get_settings
from app.models import DownstreamResult

CASSETTE_PATH = Path(__file__).parent / "fixtures" / "downstream_cassette.json"
RECORD = os.getenv("LATIN_ANALYZER_E2E_RECORD") == "1"


class _CassetteClient:
    """Replay a real downstream client's DownstreamResults from a JSON cassette.

    Keys are ``"{service_name}:{arg}"``.  In record mode the call is delegated to
    the wrapped real client and the result stored; in replay mode the recorded
    result is returned and a miss is a hard failure (re-record needed).
    """

    def __init__(self, real, method: str, service_name: str, store: dict) -> None:
        self._real = real
        self._method = method
        self._store = store
        # Fixed name so cassette keys are identical in record and replay mode
        # (in replay `real` is None and must not influence the key).
        self.service_name = service_name

    async def _call(self, arg: str) -> DownstreamResult:
        key = f"{self.service_name}:{arg}"
        if not RECORD:
            entry = self._store.get(key)
            if entry is None:
                raise AssertionError(
                    f"cassette miss for {key!r}; re-record with "
                    f"LATIN_ANALYZER_E2E_RECORD=1 (see module docstring)."
                )
            return DownstreamResult.model_validate(entry)
        result = await getattr(self._real, self._method)(arg)
        self._store[key] = result.model_dump(mode="json")
        return result


class _UDPipe(_CassetteClient):
    async def process(self, text: str) -> DownstreamResult:
        return await self._call(text)


class _WordNet(_CassetteClient):
    async def lemmatize(self, form: str) -> DownstreamResult:
        return await self._call(form)


class _LatinIsSimple(_CassetteClient):
    async def search(self, lemma: str) -> DownstreamResult:
        return await self._call(lemma)


class _Morpheus(_CassetteClient):
    async def lemmatize(self, form: str) -> DownstreamResult:
        return await self._call(form)


# Sentences are analysed as whole sentences (full UDPipe context), keyed for lookup.
GREGESQUE = "Quid mihi, si pereunt homines, armenta, gregesque;?"
SURDIS = "Haec ferit, illa ferit;surdis haec auribus, illa"
ACCIPE = "Quum Venerem aspicerem sine flammis; „accipe”, dixi,"
FAUCIBUS = "Insidet et siccis faucibus atra fames?"
EIUSDEM = "De statua eiusdem"
SYLUAS = "Ibat venatum in syluas, telumque gerebat"
HYEMS = "Friget hyems."
MOUET = "Flatilis in venis spiritus, ora mouet."
SCULPTUS = "Friget hyems. lapis haec sculptus, et illa lapis."
TIBI = "Flens tandem dixit, non est tibi Caelia dispar;"
TE = "Hanc volui, non te, parce, ferire deam."
OBRIGUERE = "Palluit, utque silex obriguere comae."

SENTENCES = [
    GREGESQUE, SURDIS, ACCIPE, FAUCIBUS, EIUSDEM, SYLUAS, HYEMS, MOUET, SCULPTUS,
    TIBI, TE, OBRIGUERE,
]


@pytest.fixture(scope="session")
def analyses() -> dict:
    """Analyse every regression sentence once and return {sentence: response}."""
    store: dict = {}
    if CASSETTE_PATH.exists():
        store = json.loads(CASSETTE_PATH.read_text(encoding="utf-8"))

    async def run() -> dict:
        settings = get_settings()
        http = real_lis = None
        if RECORD:
            from app.clients import (
                DownstreamClient,
                LatinIsSimpleClient,
                LatinWordNetClient,
                MorpheusClient,
                UDPipeClient,
            )

            http = DownstreamClient(settings)
            await http.start()
            real_wn = LatinWordNetClient(http, settings)
            real_ud = UDPipeClient(http, settings)
            real_lis = LatinIsSimpleClient(http, settings)
            real_mo = MorpheusClient(http, settings)
        else:
            real_wn = real_ud = real_lis = real_mo = None

        analyzer = AnalyzerService(
            wordnet=_WordNet(real_wn, "lemmatize", "latin_wordnet", store),
            udpipe=_UDPipe(real_ud, "process", "udpipe", store),
            latin_is_simple=_LatinIsSimple(real_lis, "search", "latin_is_simple", store),
            morpheus=_Morpheus(real_mo, "lemmatize", "morpheus", store),
        )
        try:
            return {s: await analyzer.analyze(s) for s in SENTENCES}
        finally:
            if RECORD:
                CASSETTE_PATH.parent.mkdir(parents=True, exist_ok=True)
                CASSETTE_PATH.write_text(
                    json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                await real_lis.close()
                await http.close()

    return asyncio.run(run())


def _word(analyses: dict, sentence: str, form: str):
    return next(w for w in analyses[sentence].words if w.form == form)


def test_enclitic_que_is_analysed(analyses: dict) -> None:
    """'gregesque' = greges (grex) + enclitic -que.  UDPipe emits a multiword
    token ('1-2 gregesque' → greges + que); parse_conllu must collapse it to the
    head sub-token instead of dropping the word as unrecognised."""
    greg = _word(analyses, GREGESQUE, "gregesque")
    assert greg.lemma == "grex"
    assert greg.upos == "NOUN"
    assert greg.confidence.value == "full"
    assert "flock" in greg.meaning.lower() or "herd" in greg.meaning.lower()


def test_punctuation_glued_word_after_semicolon(analyses: dict) -> None:
    """'ferit;surdis' has no space, so UDPipe glues ';' onto 'surdis'.  The
    alignment must strip edge punctuation so the bare 'surdis' is recognised."""
    surdis = _word(analyses, SURDIS, "surdis")
    assert surdis.lemma == "surdus"
    assert surdis.confidence.value == "full"
    assert "deaf" in surdis.meaning.lower()


def test_ferit_morpheus_correction_preserved(analyses: dict) -> None:
    """README invariant: UDPipe lemmatises 'ferit' as 'fero'; Morpheus corrects
    it to 'ferio' (to strike).  The alignment fixes must not break this."""
    ferits = [w for w in analyses[SURDIS].words if w.form == "ferit"]
    assert len(ferits) == 2
    for word in ferits:
        assert word.lemma == "ferio"
        assert word.confidence.value == "full"


def test_word_wrapped_in_typographic_quotes(analyses: dict) -> None:
    """'„accipe”' — UDPipe glues the closing curly quote onto the word.  The bare
    'accipe' must still resolve (lemma corrected by Morpheus)."""
    accipe = _word(analyses, ACCIPE, "accipe")
    assert accipe.lemma == "accipio"
    assert accipe.confidence.value == "full"
    assert "accept" in accipe.meaning.lower() or "receive" in accipe.meaning.lower()


def test_lis_headword_differs_from_lemma(analyses: dict) -> None:
    """'faucibus' → UDPipe lemma 'fauces', but the LIS headword is 'faux'.  The
    LIS matcher's POS fallback must accept the single matching noun result."""
    fauc = _word(analyses, FAUCIBUS, "faucibus")
    assert fauc.confidence.value == "full"
    assert fauc.meaning  # non-empty
    assert "faux" in fauc.dictionary_form.lower()


def test_wrong_udpipe_lemma_recovered_via_lis(analyses: dict) -> None:
    """'Venerem' (acc. of Venus) → UDPipe mis-lemmatises as 'venio' and Morpheus
    returns nothing for the capitalised form, but LIS finds 'Venus'.  The POS
    fallback must surface the meaning even though the lemma stays UDPipe's."""
    ven = _word(analyses, ACCIPE, "Venerem")
    assert ven.confidence.value == "full"
    assert "venus" in ven.meaning.lower()
    assert "venus" in ven.dictionary_form.lower()


def test_eiusdem_form_based_lis_lookup_still_works(analyses: dict) -> None:
    """Guard the surface-form LIS lookup: 'eiusdem' (a form of 'idem') only
    resolves because LIS is queried by surface form, not by lemma."""
    eiusdem = _word(analyses, EIUSDEM, "eiusdem")
    assert eiusdem.lemma == "idem"
    assert eiusdem.confidence.value == "full"
    assert "same" in eiusdem.meaning.lower()


def test_uv_and_yi_spelling_variant_resolves_via_lis_fallback(analyses: dict) -> None:
    """'syluas' = classical 'silvas' (y→i, u→v).  LIS's form search misses the
    medieval spelling; the normalised-spelling fallback must recover 'forest'."""
    syluas = _word(analyses, SYLUAS, "syluas")
    assert syluas.confidence.value == "full"
    assert "forest" in syluas.meaning.lower()


def test_yi_spelling_variant_resolves_via_wordnet_and_lis_fallback(analyses: dict) -> None:
    """'hyems' = classical 'hiems' (y→i).  Both WordNet and LIS miss the
    y-spelling, so without the fallback the word is form_only; the normalised
    spelling recovers it to a full 'winter'."""
    hyems = _word(analyses, HYEMS, "hyems")
    assert hyems.confidence.value == "full"
    assert "winter" in hyems.meaning.lower()


def test_uv_spelling_variant_resolves_via_lis_fallback(analyses: dict) -> None:
    """'mouet' = classical 'movet' (consonantal u→v).  LIS misses the u-spelling;
    the fallback recovers the verb 'moveo' with a meaning."""
    mouet = _word(analyses, MOUET, "mouet")
    assert mouet.lemma == "moveo"
    assert mouet.confidence.value == "full"
    assert "move" in mouet.meaning.lower()


def test_morpheus_corrects_udpipe_pos_guess(analyses: dict) -> None:
    """'telumque' — UDPipe couldn't place the unsplit enclitic and dumped it into
    ADV (no features).  Morpheus knows the head is the noun 'telum', so the POS
    correction must fix the part of speech from ADV to NOUN."""
    telumque = _word(analyses, SYLUAS, "telumque")
    assert telumque.upos == "NOUN"


def test_udpipe_pos_error_recovered_via_morpheus_lemma(analyses: dict) -> None:
    """'sculptus' — UDPipe mis-tags the participle as a NOUN and keeps lemma
    'sculptus', but LIS files it under the verb 'sculpo' (one of Morpheus's
    analyses).  Morpheus lemmata as extra LIS name-match candidates must recover
    the meaning despite the wrong UDPipe part of speech."""
    sculptus = _word(analyses, SCULPTUS, "sculptus")
    assert sculptus.confidence.value == "full"
    assert "carve" in sculptus.meaning.lower() or "engrave" in sculptus.meaning.lower()


def test_second_person_pronoun_resolves_via_paradigm(analyses: dict) -> None:
    """'tibi' (dative of 'tu') — LIS files the personal pronoun under the single
    suppletive paradigm entry 'ego, tu, -'.  Matching a non-first principal part
    lets 2nd/3rd-person forms resolve instead of staying form_only."""
    tibi = _word(analyses, TIBI, "tibi")
    assert tibi.lemma == "tu"
    assert tibi.confidence.value == "full"
    assert "pronoun" in tibi.meaning.lower() or "you" in tibi.meaning.lower()


def test_te_keeps_its_own_specific_entry(analyses: dict) -> None:
    """The paradigm match is the weakest tier, so 'te' (which has its own LIS
    entry) still resolves to that specific entry rather than the generic
    'ego, tu, -' paradigm."""
    te = _word(analyses, TE, "te")
    assert te.confidence.value == "full"
    assert "you" in te.meaning.lower()


def test_morpheus_hyphenated_lemma_is_stripped(analyses: dict) -> None:
    """Morpheus marks morpheme boundaries with hyphens ('ob-rigesco').  Stripping
    them yields the dictionary headword 'obrigesco', so the lemma is confirmed
    (no longer form_only) even when LIS lacks the inflected surface form."""
    obr = _word(analyses, OBRIGUERE, "obriguere")
    assert obr.lemma == "obrigesco"
    assert obr.confidence.value != "form_only"
