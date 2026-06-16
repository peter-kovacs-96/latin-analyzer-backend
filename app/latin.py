"""Latin text utilities: tokenisation, morphology decoding, and LIS matching."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import Morphology


# ---------------------------------------------------------------------------
# UDPipe → structured Morphology
# ---------------------------------------------------------------------------

_UPOS_TO_POS: dict[str, str] = {
    "NOUN": "noun",
    "VERB": "verb",
    "ADJ": "adjective",
    "ADV": "adverb",
    "PRON": "pronoun",
    "ADP": "preposition",
    "CCONJ": "conjunction",
    "SCONJ": "conjunction",
    "AUX": "auxiliary",
    "PROPN": "proper_noun",
    "NUM": "numeral",
    "DET": "determiner",
    "PART": "particle",
    "INTJ": "interjection",
}

# Non-finite verb forms: VerbForm feature overrides the POS label so the
# frontend can distinguish infinitives and participles from finite verbs.
_VERBFORM_POS_OVERRIDE: dict[tuple[str, str], str] = {
    ("VERB", "Inf"): "infinitive",
    ("VERB", "Part"): "participle",
    ("VERB", "Ger"): "gerund",
    ("VERB", "Gdv"): "gerundive",
    ("AUX",  "Inf"): "infinitive",
    ("AUX",  "Part"): "participle",
    ("NOUN", "Ger"): "gerund",
    ("ADJ",  "Part"): "participle",
    ("ADJ",  "Gdv"): "gerundive",
}

_UD_CASE: dict[str, str] = {
    "Nom": "nominative",
    "Gen": "genitive",
    "Dat": "dative",
    "Acc": "accusative",
    "Abl": "ablative",
    "Voc": "vocative",
}
_UD_NUMBER: dict[str, str] = {"Sing": "singular", "Plur": "plural"}
_UD_GENDER: dict[str, str] = {"Masc": "masculine", "Fem": "feminine", "Neut": "neuter"}
_UD_PERSON: dict[str, str] = {"1": "1", "2": "2", "3": "3"}
_UD_TENSE: dict[str, str] = {
    "Pres": "present",
    "Past": "past",
    "Fut":  "future",
    "Perf": "perfect",
    "Pqp":  "pluperfect",
    "Imp":  "imperfect",
}
_UD_MOOD:  dict[str, str] = {"Ind": "indicative", "Sub": "subjunctive", "Imp": "imperative"}
_UD_VFORM: dict[str, str] = {"Inf": "infinitive", "Part": "participle", "Ger": "gerund", "Gdv": "gerundive"}
_UD_VOICE: dict[str, str] = {"Act": "active", "Pass": "passive"}

# Dependency relation → human-readable syntactic role label
_UD_DEPREL: dict[str, str] = {
    "root":        "predicate",
    "nsubj":       "subject",
    "obj":         "object",
    "iobj":        "indirect_object",
    "obl":         "adverbial",
    "amod":        "modifier",
    "nmod":        "genitive_modifier",
    "advmod":      "adverbial",
    "cop":         "copula",
    "conj":        "conjoined",
    "det":         "determiner_role",
    "case":        "case_marker",
    "cc":          "coordinator",
    "xcomp":       "predicative_complement",
    "ccomp":       "clausal_complement",
    "vocative":    "vocative",
    "appos":       "appositive",
    "parataxis":   "parataxis",
    "dislocated":  "dislocated",
    "expl":        "expletive",
}


def ud_to_morphology(upos: str, feats_str: str, deprel: str) -> tuple[Morphology, str | None]:
    """Convert UDPipe token fields to a (Morphology, syntactic_role) pair.

    Returns structured English-value fields; any feature not present in the
    token is left as None.  syntactic_role is None for unknown dependency
    relations.
    """
    from app.models import Morphology  # local import to avoid circular dependency

    parsed = _feats(feats_str)
    verb_form = parsed.get("VerbForm", "")

    pos = _VERBFORM_POS_OVERRIDE.get((upos, verb_form)) or _UPOS_TO_POS.get(upos, upos.lower())

    # Mood: prefer the explicit Mood feature; fall back to VerbForm for non-finite forms
    mood = _UD_MOOD.get(parsed.get("Mood", "")) or _UD_VFORM.get(verb_form)

    morphology = Morphology(
        pos=pos,
        case=_UD_CASE.get(parsed.get("Case", "")),
        number=_UD_NUMBER.get(parsed.get("Number", "")),
        gender=_UD_GENDER.get(parsed.get("Gender", "")),
        person=_UD_PERSON.get(parsed.get("Person", "")),
        tense=_UD_TENSE.get(parsed.get("Tense", "")),
        mood=mood,
        voice=_UD_VOICE.get(parsed.get("Voice", "")),
    )
    syntactic_role = _UD_DEPREL.get(deprel)
    return morphology, syntactic_role


# ---------------------------------------------------------------------------
# Latin WordNet morpho code → structured Morphology
# ---------------------------------------------------------------------------

# WordNet morpho code layout (10-char string, 0-indexed):
#   [0]     POS: n v a r p c d m e
#   verbs:  [1] person, [2] number, [3] tense, [4] mood, [5] voice
#   others: [2] number, [6] gender, [7] case

_WN_POS:    dict[str, str] = {"n": "noun", "v": "verb", "a": "adjective", "r": "adverb", "p": "pronoun", "c": "conjunction", "d": "adverb", "m": "numeral", "e": "preposition"}
_WN_PER:    dict[str, str] = {"1": "1", "2": "2", "3": "3"}
_WN_NUM:    dict[str, str] = {"s": "singular", "p": "plural"}
_WN_TEN:    dict[str, str] = {"p": "present", "i": "imperfect", "f": "future", "r": "perfect", "l": "pluperfect", "t": "future_perfect"}
_WN_MOO:    dict[str, str] = {"i": "indicative", "s": "subjunctive", "n": "infinitive", "m": "imperative", "p": "participle"}
_WN_VOI:    dict[str, str] = {"a": "active", "p": "passive"}
_WN_GEN:    dict[str, str] = {"m": "masculine", "f": "feminine", "n": "neuter"}
_WN_CAS:    dict[str, str] = {"n": "nominative", "g": "genitive", "d": "dative", "a": "accusative", "b": "ablative", "v": "vocative"}


def wn_to_morphology(tag: str | None) -> Morphology:
    """Decode a Latin WordNet 10-char morpho code into a Morphology object.

    Used only as a fallback when UDPipe did not produce a token.
    Note: no syntactic_role is available from WordNet.
    """
    from app.models import Morphology

    if not tag or len(tag) < 9:
        return Morphology()
    t = (tag + "----------")[:10]
    p = t[0]
    if p == "v":
        return Morphology(
            pos=_WN_POS.get(p),
            person=_WN_PER.get(t[1]),
            number=_WN_NUM.get(t[2]),
            tense=_WN_TEN.get(t[3]),
            mood=_WN_MOO.get(t[4]),
            voice=_WN_VOI.get(t[5]),
        )
    return Morphology(
        pos=_WN_POS.get(p),
        number=_WN_NUM.get(t[2]),
        gender=_WN_GEN.get(t[6]),
        case=_WN_CAS.get(t[7]),
    )


# ---------------------------------------------------------------------------
# Latin is Simple matching
# ---------------------------------------------------------------------------

# Maps UDPipe UPOS tags to LIS intern_type values
UPOS_TO_LIS_TYPE: dict[str, str] = {
    "NOUN": "noun",
    "PROPN": "noun",
    "VERB": "verb",
    "AUX": "verb",
    "ADJ": "adjective",
    "ADV": "adverb",
}


def _en_translation(item: dict) -> str:
    """Extract the English translation string from a single LIS result entry."""
    translations = (item.get("translations_unstructured") or {}).get("en")
    if isinstance(translations, list):
        return ", ".join(map(str, translations[:3]))
    if isinstance(translations, dict):
        values: list[str] = []
        for v in translations.values():
            values.extend(v if isinstance(v, list) else [v])
        return ", ".join(map(str, values[:3]))
    return translations or ""


def find_lis_match(results: list, lemma: str, upos: str = "") -> dict | None:
    """Return the best-matching LIS entry for *lemma* given a UDPipe UPOS tag.

    Scoring:
      +10  short_name == lemma (exact match)
      +8   first token of full_name == lemma (e.g. "vinco" in "vinco, vincis…")
      +5   intern_type matches expected LIS type for *upos*
      skip entries with "still in translation" or empty English text
    """
    if not results:
        return None

    lemma_lc = lemma.lower()
    expected_type = UPOS_TO_LIS_TYPE.get(upos, "")

    best_score = -1
    best_item: dict | None = None

    for item in results:
        en = _en_translation(item)
        if not en or "still in translation" in en:
            continue

        sn = item.get("short_name", "").lower()
        fn_first = item.get("full_name", "").split(",")[0].strip().lower()

        if sn == lemma_lc:
            form_score = 10
        elif fn_first == lemma_lc:
            form_score = 8
        else:
            continue  # no form match at all

        pos_score = 5 if expected_type and item.get("intern_type") == expected_type else 0
        total = form_score + pos_score

        if total > best_score:
            best_score = total
            best_item = item

    return best_item


def extract_lis_meaning(result: list | dict | str | None, lemma: str = "", upos: str = "") -> str:
    """Return the English meaning for a LIS search result.

    When *lemma* and/or *upos* are provided the best-matching entry is chosen
    instead of always picking result[0].
    """
    if not result or not isinstance(result, list):
        return ""
    if lemma or upos:
        best = find_lis_match(result, lemma, upos)
        if best:
            return _en_translation(best)
    for item in result:
        en = _en_translation(item)
        if en and "still in translation" not in en:
            return en
    return ""


def extract_lis_fullname(result: list | dict | str | None, lemma: str = "", upos: str = "") -> str:
    """Return the dictionary full_name (e.g. 'amor, amoris [m.] C') from LIS."""
    if not result or not isinstance(result, list):
        return ""
    if lemma or upos:
        best = find_lis_match(result, lemma, upos)
        if best:
            return best.get("full_name", "")
    return result[0].get("full_name", "") if result else ""


# ---------------------------------------------------------------------------
# CoNLL-U / tokenisation helpers
# ---------------------------------------------------------------------------

def tokenize(text: str) -> list[str]:
    return re.findall(r"[A-Za-zÀ-ÿ]+", text)


def _feats(value: str) -> dict[str, str]:
    """Parse a CoNLL-U FEATS string ('Key=Val|Key=Val') into a dict."""
    result: dict[str, str] = {}
    if value and value != "_":
        for item in value.split("|"):
            if "=" in item:
                key, val = item.split("=", 1)
                result[key] = val
    return result


def parse_conllu(conllu: str) -> list[dict[str, str]]:
    tokens: list[dict[str, str]] = []
    for line in conllu.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        col = line.split("\t")
        # Skip malformed rows, CoNLL-U multi-word tokens ("10-11"), empty nodes
        # ("1.1"), and punctuation tokens — none contribute to morphological analysis.
        if len(col) < 8 or not col[0].isdigit() or col[3] == "PUNCT":
            continue
        tokens.append({"form": col[1], "lemma": col[2], "upos": col[3], "feats": col[5], "deprel": col[7]})
    return tokens


# ---------------------------------------------------------------------------
# WordNet helpers
# ---------------------------------------------------------------------------

def extract_wordnet_lemma(candidate: dict, fallback: str) -> str:
    lemma = candidate.get("lemma", {})
    if isinstance(lemma, dict):
        return lemma.get("lemma", fallback)
    return fallback


def extract_wordnet_morpho(candidate: dict) -> str:
    morpho = candidate.get("morpho", [None])
    if isinstance(morpho, list) and morpho:
        return morpho[0] or ""
    lemma = candidate.get("lemma", {})
    if isinstance(lemma, dict):
        return lemma.get("morpho", "")
    return ""


# ---------------------------------------------------------------------------
# Sentence grouping
# ---------------------------------------------------------------------------

def split_into_sentence_groups(lines: list[str], mode: str = "sentence") -> list[list[int]]:
    """
    Group 0-based line indices into sentence groups.

    mode="sentence": boundary at blank line OR line ending with . ! ?
    mode="stanza":   boundary at blank line only

    Blank lines are never included in any group.
    Returns list of groups; each group is a non-empty list of line indices.
    """
    groups: list[list[int]] = []
    current: list[int] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(i)
            if mode == "sentence" and stripped[-1] in ".!?":
                groups.append(current)
                current = []
    if current:
        groups.append(current)
    return groups
