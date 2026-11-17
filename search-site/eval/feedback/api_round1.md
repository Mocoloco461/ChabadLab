# API QA - round 1 (text mode)

Tested 2026-11-17 02:15-02:45 against http://localhost:8765. Code files changed while I was testing (search.py modified 02:31; the server picked it up), so some items below are marked "fixed mid-test".
The embedding build was running, so machine load was about 9-10 and absolute latencies are somewhat inflated.

## Summary
- Plain-word recall is very good: on a random sample of 60 mid-frequency Hebrew/Yiddish words, 99.3% of the letters that contain the word (with any prefix) are returned. No ranking-independent false negatives were found except the missing prefix ד (see Search-quality #1). Spelling-variant groups (ורשה/ווארשא, חינוך/חנוך, ירושלים/ירושלם) work.
- Weak spots are: (a) `lang=he` (and `lang=yi`) filters are very slow (5-6 s) because of the SQLite query plan; (b) abbreviations and phrases match across word boundaries (`ד"ה` returns `חב"ד, ה'`), and phrase matches are not ranked above scattered matches; (c) the ד prefix, final ה/א, רבנו/רבינו and similar variants are not handled; (d) `-"phrase"` exclusion and `year_from=<huge number>` are broken.
- Error handling is mostly graceful: one 500 class (integer overflow), no FTS syntax errors leaked. Sorting by date/number is correct, except that 395 letters that have a Hebrew year but no Gregorian date are sorted to the end under `sort=date`.

## Bugs

1. **HIGH - `lang` filter is extremely slow.**
   `/api/search?q=חינוך&lang=he` takes 5.7 s (also 2.8 s for `משיח`+lang=he, 0.9-1.2 s for lang=yi), against about 90 ms for the same query unfiltered or with `lang=he,yi` (96 ms).
   Cause: `EXPLAIN QUERY PLAN` shows SQLite drives the join from `idx_lang` (`SEARCH l USING COVERING INDEX idx_lang`) and then runs an FTS MATCH for each of the 9,661 rows, instead of running the MATCH first. Year and volume filters are fine (6-10 ms).
   Fix: drop `idx_lang`, or write the condition as `+l.language IN (...)`, or use `l.id IN (SELECT rowid FROM letters_fts WHERE letters_fts MATCH ?)`. This filter is likely to be used a lot (the Yiddish/Hebrew toggle).

2. **MED - negated phrase `-"..."` does not work.**
   `/api/search?q=גאולה -"משיח צדקנו"` returns total=1 (expected about 900 = letters with גאולה minus the 510 with the phrase).
   Cause: `TOKEN_RE = "[^"]+"|\S+` matches `-"משיח` as one token and `צדקנו"` as another. The result is "exclude משיח and require צדקנו".
   Fix: make the regex `-?"[^"]+"|\S+`.

3. **MED - `sort=date` / `date_desc` sort 395 dated letters as if undated.**
   395 letters have `hebrew_year` but a NULL `gregorian_date` (for example id 1200690016, date_raw `ה'תשכ"ח`, 5728). The sort key `gregorian_date IS NULL, gregorian_date` puts them after all dated letters, so under `sort=date` Hebrew years go ...5734, then 5728, 5728, and so on.
   The same 395 letters are also excluded by `greg_from/greg_to`, because `gregorian_year` is NULL for them, while `year_from/year_to` includes them, so the two filters give different counts.
   Fix: fall back to `COALESCE(gregorian_date, (hebrew_year-3760)||'-07')` for sorting and for `gregorian_year`.

4. **MED - abbreviations and short phrases match across word boundaries, and the highlight is wrong.**
   `ד"ה`: 999 results. The top results are `חב"ד, ה' עליהם` (snippet `חב"<mark>ד ה</mark>'`).
   `ד"ת`: results #1 and #2 are `חב"ד ת.ד. 5046` and `תשכ"ד] ת"ח` (`<mark>ד] ת</mark>"ח`).
   `מו"ח`: result #2 is `כמו ח` ("like ח", id 1200840235; the phrase `מו ח` with prefix כ).
   Cause: the indexer splits `ד"ה` into the tokens `ד` and `ה`, and the query becomes the phrase "ד ה".
   Fix: tokenize intra-word gershayim as one token, for example convert `(?<=\w)["״](?=\w)` to ״ at index time and query time and add `tokenchars '״'`. Then `ד״ה` and `מו״ח` are real tokens.

5. **MED - several Hebrew abbreviations are ambiguous, and the expansion is wrong for citations.**
   `ח"ו` is always expanded to `חס ושלום` and `ח"ו` (1,299 hits). In `לקו"ש ח"ו` (volume 6), result #2 and #3 are plain `חס ושלום` letters with no `ח"ו`.
   `ר"ה`, `ת"ת` and `א"י` are similar. The abbrev.py header says ambiguous forms should not be listed, but `ר"ה` and `ח"ו` are.
   Suggestion: expand an abbreviation to its full form, but not the other way, for ambiguous ones.

6. **LOW-MED - maqaf, niqqud and hyphen bypass the abbreviation/variant expansion.**
   `בית המדרש` 151 vs `בית־המדרש` 87 vs `בֵּית הַמִּדְרָשׁ` 106.
   `לקוטי תורה` 974 vs `לקוטי־תורה` 145.
   `חס ושלום` 1299 vs `חס-ושלום` 40.
   `חינוך` 1442 vs `חִנּוּךְ` (niqqud) 341.
   Cause: `alternatives()` looks up the raw token before it is run through `clean()`. Fix: apply `clean()` and replace `-`/maqaf with a space before the lookup, in `build_fts_query`.

7. **LOW - integer overflow gives HTTP 500.**
   `/api/search?q=ח&year_from=99999999999999999999`, `&page=99999999999999999999`, `/api/search?q=&num_from=99999999999999999999`, `/api/letter/99999999999999999999` all return a 500 "Internal Server Error". Clamp values (for example to 0..10^9) or return 400/404.

8. **LOW - invalid or odd parameters are silently ignored or give 422 in FastAPI format.**
   `year_from=תשט"ז` (Hebrew letters) and `greg_from=1956-01-01` are ignored and the unfiltered result is returned (total=1442). `from > to` returns 0 with no hint (`vol_from=5&vol_to=3`, `year_from=5716&year_to=5710`). `page=0` and `page=-1` return page 1 and echo `page:0`. `size=0` and `size=-5` return 1 result. `sort=foo`, `mode=foo` and `match=foo` are silently replaced by defaults. `page=abc` gives a 422 with an English pydantic body. `month=foo` and `lang=xx` give 0 results with no message. A Hebrew-numeral year filter would be a real usability gain (see Ideas).

9. **LOW - month filter `אדר` matches only the plain `אדר` (613 letters), not `אדר א`/`אדר ב` (629 more).** Users in leap years may expect the union. Month values are `אדר`, `אדר א`, `אדר ב`, `מנחם אב` etc. and must be copied exactly.

10. **LOW - relaxed (partial match) pagination is inconsistent.** For `q=ליל הסדר פסח` (strict_total=2): page 1 total=57, pages 2 and 3 total=59. The items themselves are consistent (57 unique across pages), only `total` jumps. Strict results (bm25 scores about 10-20) and relaxed results (score = number of matched terms, 2.0) share the same `score` field on different scales. Facets are `null` for relaxed results.

11. **LOW - slow long queries.** A 25-word query takes 1.3 s, 50 words 2.5 s, 100 words 5.2 s. `חינוך` repeated 50 times takes 1.0 s (the same term is expanded 50 times). `match=any` with 40 words takes 1.2 s. Suggest deduplicating terms and capping at about 12 terms (the rest are used only for the relaxed pass).

12. **LOW - `hybrid` mode: `sort` is ignored and `total` is capped at 300.** `/api/search?q=חינוך&mode=hybrid&sort=date` still returns relevance order with total=300 (text alone: 1442). Fine while the semantic index is missing (no crash; `warning` is set), but the UI should not show "300 results".

13. **LOW - leaked markup in data.** 19 letters contain literal `[mazkir1]...[/mazkir1]` tags in the text (visible in snippets, for example `[mazkir1]הרב ח.מ.א. חדקוב[/mazkir1]`, and searchable as the word `mazkir1`). Also visible in some snippets are footnote number strings such as `44 41 42ה) 43נשמט`.

14. **INFO - fixed mid-test:** at about 02:30, queries consisting only of a minus term, a lone quote, brackets, emoji or `*` returned the whole corpus (11,062 results, for example `-חינוך`, `"`, `😀`, `(((`). A newer version returns `total:0` with an error message "אין בשאילתה מילה לחיפוש...". Verified OK now. `"  "` (whitespace only) still browses the whole corpus, which is probably intended. The server was unreachable for a few seconds during one restart.

15. **INFO - letter numbers are not unique:** 87 `letter_number` values appear in more than one volume, and `/api/letter/number/{n}` returns only the lowest id.

### Edge cases checked, no problem found
Unbalanced quote (`"שלום ברכה` is treated as two words), only stopwords (`של את על` returns AND results, 3192), English/Latin (`hello world` gives 0 relaxed; `Chabad` gives 1), numbers (`123`, `5716` gives 0), gershayim ״ vs " (identical totals), single letters (`א`, `ח*`, 250-340 ms), emoji, `NEAR(a b)`, `title:חינוך`, `חינוך;DROP TABLE`, NUL characters, `%`, `_`, FTS keywords AND/OR/NOT (treated as words), invalid UTF-8, `size=1000` (capped to 100), page beyond the last (empty result), semantic mode while the index is being built (200 + error text), hybrid with all filters. No FTS syntax error leaked in any of these.

### Filters verified against SQL (all counts exact)
`year 5716` 985, `vol 5..7` 1034, `greg 1956` 1077, `num 100..200` 102, `lang yi` 1395, `lang en` 6, `month ניסן` 769, `year_from=5730` 1142, `year_to=5700` 21, `year+lang` 42. `recipient` matches against `recipient_info`, which includes the editors' address note (so `אקסלרוד` gives 16 letters, only 3 of them in the `recipient` field); partial names work (`אקס` 23); quotes and gershayim are ignored (`מהורא"א`, `מהוראא`, `מהורא״א` all match). Sorting verified over all 1442 results for `חינוך`: `date` and `date_desc` are monotone in gregorian_date; `number` is monotone in (volume, letter_number); no duplicate ids across pages; paging with size 20 and size 100 gives the same order.

## Search-quality problems

1. **Prefix ד ("ענין דחינוך") is not expanded.** `PREFIX_LETTERS = "והבלמשכ"` has no ד. Letters missing for `חינוך`/`חנוך`: 1200830193, 1200850099, 1200850218, 1200880128, 1200900359, 1200920224, 1200940020, 1200950282, 1200970189, 1200970259 (tokens דחנוך, דחינוך, דהחינוך). `שבת` misses 29 letters (דשבת; first ids 1200710331, 1200760034, 1200760110), `רבי` 23 (דרבי), `ליובאוויטש` 15 (דליובאוויטש; 1200780217, 1200790134), `גאולה` 8 (1200700220, 1200700258, 1200700332), `תניא` 2 (1200850011, 1200880112), `אדמו"ר` 5 (1200780055, 1200860010, 1200880553). In the Rebbe's style, "דְּ" is among the most common prefixes (about 650 prefixed forms for 16 common words alone, vs about 250 for ו+ה combined), so this is the largest recall gap: 1-5% of the letters for typical concept words. Also missing: דה, וד, כד. The same gap breaks exclusion: `גאולה -משיח` wrongly keeps 1200840244 (דמשיחא) and wrongly drops 6 letters.
2. **No phrase or proximity boost.** For unquoted multi-word queries, letters that contain the exact phrase are scattered through the ranking. Median rank of phrase-containing letters (out of N results): `ארץ ישראל` 685/1424 (only 57 of the 227 phrase letters are in the first 227), `יום טוב` 1373/2048, `לב ישראל` 167/702 (3 of 25 phrase letters in the top 25), `הבעל שם טוב` 105/747, `ליל הסדר` (the only letter containing the phrase, 1200760069, is rank 8 of 9), `הרב חדקוב` 5 of 18 phrase letters in the top 18. Cause: pure bm25 with AND. Diagnosis: bm25 is dominated by term frequency and length normalization; adjacency is ignored. Fix: second-pass boost for letters matching the phrase (a `NEAR(a b, 5)` or a phrase query on the top 300 bm25 candidates, then a score bonus).
3. **Exact form not preferred over prefixed forms.** For short words the top-20 is mixed with other words: `ים` (1473 hits vs 83 without prefixes) top 20 shows בים, ומים, מים, ..., שים, כים; `לב` shows כלב, שלב in the top 10; `אב` includes באב, כאב. Idea: weight exact-form tokens higher (second FTS query for the exact form on the top N, or boost by number of unprefixed occurrences).
4. **Spelling variants not unified** (counts of letters): `רבנו` 1347 vs `רבינו` 438; `אוסטרליה` 18 vs `אוסטרליא` 14 vs `אויסטראליע` 6; `הילולא` 625 vs `הילולה` 0 (the corpus has `הלולא` too); `ניו יורק` = `נויארק` 126 vs `ניויארק` 4; `ירושלים`/`ירושלם` are unified, but `יארצייט` 83 vs `יארצאייט`/`יאהרצייט` 0. `ליובאוויטש` 571 does not include `ליובאוויטשער` (99 more letters, Yiddish adjective; only `ליובאוויטש*` finds them, 606). Plural/construct forms are separate words: `שיעור` 501 vs `שיעורים` 614; `מנהג` 550 vs `מנהגים` 181; `תורה` 5205 vs `תורת` 2824; `אדמו"ר` 5507 vs `אדמור` (no gershayim) 28. A scholar searching "מנהג" will not see "מנהגים"; consider a stem/suffix option (ים/ות/ת, ו/י in the middle).
5. **Typo and unknown-word handling for 2-word queries.** `גאולה xyzzy`, `חינוך כשרר`, `הרב חדקובב` return 0 results (the relaxed pass requires at least 2 matched terms). With 3+ terms it works (`משיח גאולה xyzzy` gives the 328 results of the 2 real words). Suggest: for 2-term queries, fall back to the single most selective matching term and show a "did you mean" hint (trigram or edit distance against the FTS vocabulary: `fts5vocab`).
6. **Names: letters addressed to the person are not boosted.** `אקסלרוד`: the 3 letters whose `recipient` is him are at ranks 10, 13, 14 of 25; `גרינגלאס`: 16 addressed letters are spread over ranks 1-31 of 46. The recipient column has bm25 weight 1.5; consider 4-5, or a "to this person" facet.
7. **Cross-word matches in notes and recipient fields.** Results for `ורשה` include letters whose only match is in the editors' footnotes (`וורשא תרכ"ז` as a printing place, `מורשה קהלת יעקב` = "heritage of the congregation of Jacob", which matches ורשה only via the prefix מ). This is expected for notes (and probably useful); but the snippet does not say that the match is in a footnote. The prefix expansion also creates false hits for ורשה: מורשה, הורשה, בורשה.
8. **Result ranking for `מו"ח`** puts `מורי וחמי` letters first and `כמו ח` second (see Bug 4); the abbreviation itself, in 3,656 letters, is ranked by bm25 only, so for very frequent terms the first page is arbitrary. For very common terms consider ordering ties by date or by an in-text frequency cap.

## Performance (text mode, 25 typical queries x 3 runs, local, load about 9)
- p50 73 ms, p95 165-180 ms, max 260 ms (first run `חינוך`; cold cache).
- Queries over 500 ms (all other categories are fine): `lang=he` filter 2.8-6.2 s; `lang=yi` 0.9-1.2 s; long queries (25 words 1.3 s, 50 words 2.5 s, 100 words 5.2 s); `חינוך` repeated 50 times 1.0 s; `match=any` with 40 words 1.2 s; an 8-word relaxed query 2.2 s once (358 ms on repeat); 7-word relaxed `שלום וברכה בקשר עם גאולה ומשיח ביום א` 496 ms.
- 250-590 ms: very common words/stopwords (`שלום וברכה` 330-424 ms, `א` 255-275 ms, `ח*` 340 ms, `של` 200 ms, `שלום -` 290 ms), combined filters q+vol+year+lang+recipient 430-590 ms, page 50 or 9999 of `חינוך` 390-420 ms. Every page also recomputes `COUNT(*)` and the 3 facet queries; cache them or compute only on page 1.
- Hybrid (text-only fallback) 40-140 ms.

## Snippets
- 3,359 snippets over 36 queries (size=100): 0 unbalanced `<mark>` tags, 0 snippets without a highlight, 0 raw HTML tags other than mark, no control characters. HTML is escaped correctly.
- Wrong or odd highlights: cross-word abbreviation matches (Bug 4: `חב"<mark>ד ה</mark>'`, `תשכ"<mark>ד] ת</mark>"ח`); a match found only in a footnote or recipient column shows a snippet from there with no indication; the snippet of `לקו"ת` letter 1200940058 starts with text that has no highlight (the match is later in the 28-token window). Letters with `[mazkir1]` tags show them in snippets.
- Highlights follow prefix forms correctly (`<mark>ללקו"ת</mark>`, `<mark>ממוהרש"ב</mark>`), and expanded abbreviations (`ארה"ב` query highlights `ארצות הברית`).

## Improvement ideas, ranked by expected impact
1. Add prefix ד (and דה, וד, כד) to `PREFIX_LETTERS`/`PREFIX_COMBOS` (hebrew.py). Fixes 1-5% recall loss on most concept words (Search-quality 1). One-line change, but check precision on short words (`דם`, `דב`); keep a minimum word length of 3 for ד.
2. Fix the `lang` filter plan (Bug 1): `+l.language` or a subquery. Cuts 5.7 s to under 150 ms.
3. Phrase/proximity boost for multi-word queries (Search-quality 2): run the bm25 query, take the top 300, and add a bonus when `"w1 w2"` (with prefix variants on the first word) matches, or `NEAR(w1 w2, 3)` for 3+ words.
4. Tokenize gershayim abbreviations as single tokens (Bug 4) and mark ambiguous abbreviations as one-directional in abbrev.py (Bug 5); apply `clean()`/hyphen normalisation before the abbreviation lookup (Bug 6).
5. Spelling-variant normalisation for common variants: ו/י in the middle (רבנו/רבינו, מצוה/מצווה, הלולא/הילולא), final ה/א (אוסטרליה/אוסטרליא), and the Yiddish suffix ער. Either extra rows in abbrev.py or a skeleton column (index strips י/ו except at the start, and the query is normalised the same way, boosting exact spelling).
6. Optional suffix expansion: a checkbox "כל הצורות" (ים/ות/ת/ה) producing `word*`-like or generated variants, since `מנהג` vs `מנהגים` and `תורה` vs `תורת` are the same lookup for a scholar.
7. Fix the `-"phrase"` regex (Bug 2) and clamp numeric parameters (Bug 7).
8. Accept Hebrew year input (`year_from=תשט"ז`, plus the `5716` and `716` forms) and a bare `5716`/`תשט"ז` in the query box as a year filter suggestion; return a clear JSON `warnings` list for ignored or invalid parameters (from>to, unknown month/lang/sort) instead of silently ignoring them.
9. Date sort and Gregorian filter fall back to Hebrew year for the 395 letters without a Gregorian date (Bug 3).
10. Two-word typo fallback and "did you mean" (Search-quality 5), using `fts5vocab` plus edit distance on tokens with df>=2.
11. Dedupe query terms and cap the number of terms in the main query to about 12 (Bug 11); compute facets and COUNT only on page 1.
12. Boost `recipient` hits (weight 4-5) or add a result flag `matched_in: body|recipient|notes` so the UI can label footnote-only matches (Search-quality 6 and 7).
13. Month filter `אדר` should include `אדר א/ב`; letter-number lookup should accept `volume`; strip `[mazkir1]` style tags at index time (Bugs 9, 13, 15).
