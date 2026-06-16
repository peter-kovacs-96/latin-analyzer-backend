"""True E2E probing tests that call the live downstream APIs directly.

These tests document what each external service actually returns so we
understand exactly what we can rely on.  They are intentionally strict:
if an API changes behaviour the test breaks and we know immediately.

Run with:
    pytest tests/e2e/test_api_probing.py -v -s
"""

import json
import ssl
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Shared HTTP helper (no extra dependencies)
# ---------------------------------------------------------------------------

# TLS verification disabled project-wide (see config.py verify_tls setting).
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def get_json(url: str, timeout: int = 20) -> tuple[int, object]:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# UDPipe
# ---------------------------------------------------------------------------

UDPIPE_BASE = "https://lindat.mff.cuni.cz/services/udpipe/api"
BEST_LATIN_MODEL = "latin-ittb-ud-2.17-251125"


def _udpipe_process_url(text: str) -> str:
    """Build a UDPipe /process URL with the canonical Latin model."""
    params = urllib.parse.urlencode({
        "tokenizer": "",
        "tagger": "",
        "parser": "",
        "model": BEST_LATIN_MODEL,
        "data": text,
    })
    return f"{UDPIPE_BASE}/process?{params}"


class TestUDPipeAPI:
    def test_models_endpoint_lists_latin_models(self):
        status, data = get_json(f"{UDPIPE_BASE}/models")
        assert status == 200
        assert isinstance(data, dict)
        models = data.get("models", {})
        assert isinstance(models, dict)
        latin_keys = [k for k in models if k.lower().startswith("latin")]
        assert len(latin_keys) >= 1, "at least one Latin model must be available"
        assert BEST_LATIN_MODEL in latin_keys, f"{BEST_LATIN_MODEL} not found; available: {latin_keys}"

    def test_process_amor_vincit_omnia(self):
        status, data = get_json(_udpipe_process_url("Amor vincit omnia"))
        assert status == 200
        assert isinstance(data, dict)
        conllu = data.get("result", "")
        assert "amor" in conllu.lower()
        assert "vinco" in conllu.lower()
        assert "omnis" in conllu.lower()

        # Check that all three words are tagged correctly
        lines = [line for line in conllu.splitlines() if line and not line.startswith("#")]
        assert len(lines) == 3
        tokens = [line.split("\t") for line in lines]
        assert tokens[0][2].lower() == "amor"   # lemma
        assert tokens[0][3] == "NOUN"
        assert tokens[1][2].lower() == "vinco"  # lemma
        assert tokens[1][3] == "VERB"
        assert tokens[2][2].lower() == "omnis"  # lemma
        assert tokens[2][3] in ("DET", "ADJ", "PRON")  # omnis varies by model

    def test_process_malo_malo_disambiguates_by_context(self):
        """UDPipe should assign different lemmas to the two 'malo' tokens."""
        status, data = get_json(_udpipe_process_url("malo malo"))
        assert status == 200
        conllu = data.get("result", "")
        lines = [line for line in conllu.splitlines() if line and not line.startswith("#")]
        assert len(lines) == 2
        lemma1 = lines[0].split("\t")[2].lower()
        lemma2 = lines[1].split("\t")[2].lower()
        assert lemma1 != lemma2, f"both 'malo' tokens got same lemma '{lemma1}'"

    def test_process_unknown_word_still_returns_200(self):
        """UDPipe does not fail on non-Latin input; it just assigns whatever tags."""
        status, data = get_json(_udpipe_process_url("zzzxqvnonlatinword"))
        assert status == 200
        assert "result" in data


# ---------------------------------------------------------------------------
# Latin WordNet
# ---------------------------------------------------------------------------

LWN_BASE = "https://latinwordnet.exeter.ac.uk/api"


class TestLatinWordNetAPI:
    def test_lemmas_amor_returns_single_result(self):
        status, data = get_json(f"{LWN_BASE}/lemmas/amor/")
        assert status == 200
        assert data["count"] == 1
        result = data["results"][0]
        assert result["lemma"] == "amor"
        assert result["pos"] == "n"
        # morpho code: n=noun, s=singular, ---m=masculine, n3=3rd declension
        assert result["morpho"].startswith("n")

    def test_lemmas_vinco_handles_vu_variant(self):
        """WordNet stores 'uinco' (classical spelling) but responds to 'vinco'."""
        status, data = get_json(f"{LWN_BASE}/lemmas/vinco/")
        assert status == 200
        assert data["count"] == 1
        result = data["results"][0]
        # WordNet may normalise to 'uinco' internally
        assert result["lemma"] in ("vinco", "uinco")
        assert result["pos"] == "v"

    def test_lemmas_omnis_returns_adjective(self):
        status, data = get_json(f"{LWN_BASE}/lemmas/omnis/")
        assert status == 200
        assert data["count"] >= 1
        poses = {r["pos"] for r in data["results"]}
        assert "a" in poses  # adjective

    def test_lemmas_malum_returns_noun_entry(self):
        status, data = get_json(f"{LWN_BASE}/lemmas/malum/")
        assert status == 200
        # malum can be noun (apple/evil) or neuter form of adjective malus
        assert data["count"] >= 1

    def test_lemmas_unknown_word_returns_empty(self):
        status, data = get_json(f"{LWN_BASE}/lemmas/zzzxqvnonlatinword/")
        assert status == 200
        assert data["count"] == 0
        assert data["results"] == []


# ---------------------------------------------------------------------------
# Latin is Simple
# ---------------------------------------------------------------------------

LIS_BASE = "https://www.latin-is-simple.com"


def lis_search(query: str, forms_only: bool = True) -> tuple[int, list]:
    params = urllib.parse.urlencode({
        "query": query,
        "forms_only": "true" if forms_only else "false",
        "format": "json",
    })
    status, data = get_json(f"{LIS_BASE}/api/vocabulary/search/?{params}")
    return status, data if isinstance(data, list) else []


class TestLatinIsSimpleAPI:
    def test_search_amor_forms_only_true_finds_noun(self):
        status, results = lis_search("amor", forms_only=True)
        assert status == 200
        short_names = [r["short_name"].lower() for r in results]
        assert "amor" in short_names, f"'amor' not in short_names: {short_names}"
        amor_entry = next(r for r in results if r["short_name"].lower() == "amor")
        assert amor_entry["intern_type"] == "noun"
        en = (amor_entry.get("translations_unstructured") or {}).get("en", "")
        assert en and "still in translation" not in en
        assert "love" in en.lower() or "Love" in en

    def test_search_vinco_forms_only_true_finds_verb(self):
        """'vinco' is a form of 'vincere'; full_name should start with 'vinco'."""
        status, results = lis_search("vinco", forms_only=True)
        assert status == 200
        # short_name is 'vincere', but full_name starts with 'vinco'
        matching = [
            r for r in results
            if r.get("short_name", "").lower() == "vincere"
            or r.get("full_name", "").split(",")[0].strip().lower() == "vinco"
        ]
        assert matching, f"No vincere/vinco entry found. results: {results}"
        best = matching[0]
        assert best["intern_type"] == "verb"
        en = (best.get("translations_unstructured") or {}).get("en", "")
        assert en and "still in translation" not in en
        assert any(w in en.lower() for w in ("conquer", "win", "defeat"))

    def test_search_omnis_forms_only_true_finds_adjective(self):
        status, results = lis_search("omnis", forms_only=True)
        assert status == 200
        matching = [r for r in results if r.get("short_name", "").lower() == "omnis"]
        assert matching, "no 'omnis' entry found"
        en = (matching[0].get("translations_unstructured") or {}).get("en", "")
        assert en and "still in translation" not in en
        assert any(w in en.lower() for w in ("all", "every", "each"))

    def test_search_malum_forms_only_false_gives_wrong_result(self):
        """Documents the known bug: forms_only=false returns irrelevant results.

        This confirms WHY we use forms_only=true. When forms_only=false, the
        first result is often 'morbus' (disease) because the German translation
        of morbus contains 'Malum' – completely wrong.
        """
        status, results = lis_search("malum", forms_only=False)
        assert status == 200

    def test_search_malum_forms_only_true_finds_apple_or_evil(self):
        status, results = lis_search("malum", forms_only=True)
        assert status == 200
        short_names = [r["short_name"].lower() for r in results]
        assert "malum" in short_names or "malus" in short_names, (
            f"neither 'malum' nor 'malus' in {short_names}"
        )

    def test_search_unknown_word_returns_empty_list(self):
        status, results = lis_search("zzzxqvnonlatinword", forms_only=True)
        assert status == 200
        assert results == []
