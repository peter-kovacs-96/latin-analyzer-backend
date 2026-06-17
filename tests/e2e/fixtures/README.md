# Test fixtures

- **`downstream_cassette.json`** — recorded real downstream responses replayed by
  `test_word_regression.py` (see ADR-0009). Refresh with `LATIN_ANALYZER_E2E_RECORD=1`.

- **`test1.docx`, `test2.docx`** — sample Latin poems (early-modern spelling) used
  to exercise the analysis pipeline end to end. They are the source of the
  word-level regression cases (enclitics, glued punctuation, u/v & y/i spelling
  variants, LIS-headword / POS mismatches). `test2.docx` is the original sample;
  `test1.docx` ("De Venere et Cupidine") is an additional poem.

  Quick manual run against a local backend:

  ```bash
  python - <<'PY' | curl -s -X POST localhost:8000/analyze/stream \
      -H 'Content-Type: text/plain; charset=utf-8' --data-binary @-
  import re, html, zipfile
  xml = zipfile.ZipFile("tests/e2e/fixtures/test1.docx").read("word/document.xml").decode("utf-8")
  print("\n".join(html.unescape("".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)))
                   for p in re.split(r"</w:p>", xml)))
  PY
  ```
