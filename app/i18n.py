"""Localisation for Latin Analyzer API responses.

Only controlled-vocabulary fields are translated (morphological labels, syntactic
roles, confidence levels).  Free-text content — `meaning`, `dictionary_form`,
`form`, `lemma` — is left untouched because it comes from English/Latin
dictionaries, not from this codebase's vocabulary.

Translation is applied to the plain dict produced by `model_dump()`, so Pydantic
type constraints on the original models are never violated.
"""

from __future__ import annotations

SUPPORTED_LANGUAGES: frozenset[str] = frozenset({"en", "hu"})

# ---------------------------------------------------------------------------
# Hungarian translation table
# ---------------------------------------------------------------------------

_HU: dict[str, str] = {
    # Parts of speech (morphology.pos)
    "noun":         "főnév",
    "verb":         "ige",
    "adjective":    "melléknév",
    "adverb":       "határozószó",
    "pronoun":      "névmás",
    "preposition":  "elöljárószó",
    "conjunction":  "kötőszó",
    "auxiliary":    "segédige",
    "proper_noun":  "tulajdonnév",
    "numeral":      "számnév",
    "determiner":   "névelő",
    "particle":     "partikula",
    "interjection": "indulatszó",
    # Non-finite verb forms (morphology.pos and morphology.mood)
    "infinitive":   "főnévi igenév",
    "participle":   "melléknévi igenév",
    "gerund":       "gerundium",
    "gerundive":    "gerundivum",
    # Case (morphology.case)
    "nominative":   "alanyeset",
    "genitive":     "birtokos eset",
    "dative":       "részeseset",
    "accusative":   "tárgyeset",
    "ablative":     "ablativus",
    "vocative":     "vocativus",
    # Number (morphology.number)
    "singular":     "egyes szám",
    "plural":       "többes szám",
    # Gender (morphology.gender)
    "masculine":    "hímnem",
    "feminine":     "nőnem",
    "neuter":       "semleges nem",
    # Tense (morphology.tense)
    "present":          "jelen idő",
    "imperfect":        "imperfectum",
    "future":           "jövő idő",
    "perfect":          "perfectum",
    "pluperfect":       "plusquamperfectum",
    "future_perfect":   "futurum exactum",
    "past":             "múlt idő",
    # Mood (morphology.mood)
    "indicative":   "kijelentő mód",
    "subjunctive":  "kötő mód",
    "imperative":   "felszólító mód",
    # Voice (morphology.voice)
    "active":       "cselekvő",
    "passive":      "szenvedő",
    # Syntactic roles (syntactic_role)
    "subject":                  "alany",
    "predicate":                "állítmány",
    "object":                   "tárgy",
    "indirect_object":          "részestárgy",
    "adverbial":                "határozó",
    "modifier":                 "jelző",
    "genitive_modifier":        "birtokos jelző",
    "copula":                   "kopula",
    "conjoined":                "mellérendelt tag",
    "determiner_role":          "determináns",
    "case_marker":              "esetjelölő",
    "coordinator":              "koordináló kötőszó",
    "predicative_complement":   "predikatív kiegészítő",
    "clausal_complement":       "tagmondatos kiegészítő",
    "appositive":               "értelmező",
    "parataxis":                "mellérendelés",
    "dislocated":               "kiemelt elem",
    "expletive":                "töltelékszó",
    # Confidence (confidence)
    "full":         "teljes",
    "no_meaning":   "hiányzó jelentés",
    "form_only":    "csak alak",
}

_TABLES: dict[str, dict[str, str]] = {"hu": _HU}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _t(value: str | None, table: dict[str, str]) -> str | None:
    if value is None:
        return None
    return table.get(value, value)


def translate_word_dict(word: dict, table: dict[str, str]) -> dict:
    """Return a copy of a model_dump()'d WordAnalysis dict with labels translated."""
    m = word.get("morphology") or {}
    return {
        **word,
        "morphology": {
            **m,
            "pos":    _t(m.get("pos"),    table),
            "case":   _t(m.get("case"),   table),
            "number": _t(m.get("number"), table),
            "gender": _t(m.get("gender"), table),
            "tense":  _t(m.get("tense"),  table),
            "mood":   _t(m.get("mood"),   table),
            "voice":  _t(m.get("voice"),  table),
            # person stays as "1" / "2" / "3" in all languages
        },
        "syntactic_role": _t(word.get("syntactic_role"), table),
        "confidence":     _t(word.get("confidence"),     table),
    }


def translate_response_dict(data: dict, lang: str) -> dict:
    """Translate controlled-vocabulary values in a model_dump()'d AnalysisResponse.

    Returns *data* unchanged when lang == "en" or lang is not supported.
    Free-text fields (meaning, dictionary_form, form, lemma) are never modified.
    """
    table = _TABLES.get(lang)
    if table is None:
        return data
    return {
        **data,
        "words": [translate_word_dict(w, table) for w in data.get("words", [])],
    }
