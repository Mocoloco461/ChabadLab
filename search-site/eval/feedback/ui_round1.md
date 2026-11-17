# UI/UX round 1 - Igrot Kodesh search site

Tested with Playwright/Chromium: desktop 1400x900 and iPhone 13 emulation. Scripts and screenshots are in /root/scratch/ui-test/ (t1-t5.py, *.png). No code was changed.

## Summary
- The basics work well: RTL layout is correct (arrows, "עד" ranges, pager and sort labels point the right way), searching, the reader, back/forward, shared #letter= URLs and pagination all work, and there were zero JS console errors (apart from one 404 for a bad letter id).
- The biggest problems are on mobile, where the filter panel is always expanded and pushes the first result about 850px down, past a 664px viewport. On the desktop side, the recipient filter matches text the user cannot see on the cards, so results for "שזר" look wrong.
- Smaller problems are the reader's prev/next, which walks the corpus instead of the result list, some silent failures (bad letter id, page beyond the last), a truncated phrase snippet, and keyboard/a11y gaps.

## Bugs

1. **[HIGH] Recipient filter matches hidden text, so the results look wrong.**
   - Steps: `?recipient=שזר` gives 110 results. None of the 110 cards shows "שזר". The cards read, e.g., "אל: בצרכי ציבור באמונה וכו' מר שניאור זלמן שי'" or "...מהרש"ז שי'".
   - Cause: the filter searches `recipient_info`, which includes the addressee footnote ("מר שניאור זלמן: שזר."). The card shows only the address line (`recipient`).
   - Effect: the user cannot tell why a letter matched. The card gives no sign whether it is the president Shazar or "מהרש"ז".
   - Screenshot: /root/scratch/ui-test/d_11_recip.png
   - The footnote also appears in the reader as a bare "1. מר שניאור זלמן: שזר." with no marker in the body (/root/scratch/ui-test/d_33_notes_bottom.png).

2. **[HIGH, mobile] The filter panel is always open and hides the results.**
   - Steps: iPhone 13, search "שידוך". The status line is at y=703 and the first result at y=850, in a 664px viewport. The user sees only the search box, three stacked mode buttons and the filters, with no sign anything was found.
   - Screenshots: /root/scratch/ui-test/m_02_shiduch.png, m_01_home_full.png
   - The three mode labels each take a full-width row, which wastes about 300px.

3. **[MED] A phrase snippet can cut the phrase in half.**
   - Steps: search `"מחנה ישראל"`, first result (אגרת תלה). The snippet ends "...למחנה…" with only the first word highlighted. The match is "מחנה ישראל", so the second word is cut off.
   - Screenshot: /root/scratch/ui-test/d_10_phrase.png
   - Cause (probably): the snippet window ends right after the first term of the phrase.

4. **[MED] A bad or missing letter id fails silently.**
   - Steps: open `#letter=999999999` (e.g. an old shared link). The API returns 404 and `openLetter` just returns. The reader keeps showing the previous letter (or nothing on a fresh load), with no message. Console: "Failed to load resource 404".

5. **[MED] `page=999` gives an empty, inconsistent page.**
   - Steps: `?q=שידוך&page=999`. The status says "נמצאו 266 אגרות" but 0 cards are shown, and the pager reads "→ 1 … 14 ←" with no current page marked.
   - Fix: clamp the page, or show "עמוד לא קיים - חזרה לעמוד 1".

6. **[MED] Queries that are only operators return the whole corpus.**
   - Steps: `-שידוך`, `"`, `*` or whitespace give "נמצאו 11,062 אגרות · חיפוש טקסט" and a list of 20 arbitrary letters. This is misleading. A negation with no positive term should say "יש להוסיף מילה לחיפוש".
   - Related: the single letter "א" returns 9,529 results with no hint.

7. **[LOW-MED] Reader prev/next goes to the next letter in the corpus, not the next search result.**
   - Steps: search "שידוך", open result 1 (id ...276), press "הבאה ←". You get ...277, which is not in the results (it has no highlights and is not marked active in the list).
   - This is fine for browsing but confusing for someone triaging results. Nothing in the label says "next in volume". Also, after next/prev the left list loses its `.active` card (the active-li count was 0).

8. **[LOW] Mixed-direction text in snippets and dates.**
   - Snippets that begin or end with ":" or "." show the punctuation on the wrong side, e.g. "…: מצילום כתי"ק" and "המבוגרת. :" (/root/scratch/ui-test/d_30_focus_li.png, m_11_results_scrolled.png).
   - The ISO Gregorian date "1959-03-29" sits next to Hebrew dates, and partial dates like "1973-08" appear without explanation. It would read better as "29.3.1959" or "29 במרץ 1959".
   - One card shows a doubled date_raw: "ב"ה, ג' תמוז, תש"ט | ב"ה, ו' תמוז".

9. **[LOW] Year and number inputs do not auto-submit.**
   - The selects (volume, year, month, language, sort) re-run the search on change. The Gregorian year and letter-number inputs and the recipient box do nothing until Enter or "חיפוש" is pressed. Nothing tells the user this.
   - Inverted ranges (`year_from=5720&year_to=5710`, `greg 1960..1950`) just say "לא נמצאו אגרות", with no hint that the range is reversed.

10. **[LOW] Accessibility bugs.**
    - `greg_to` and `num_to` have no accessible name (the wrapping label names only the first input). The selects in the volume and year ranges have no individual labels.
    - Result cards are `<li tabindex=0>` with no role or link semantics. Enter opens them, but Space does not, and screen readers do not announce them as actionable.
    - After Enter the focus stays on the card and is not moved to the reader. The reader is after 20 cards plus the pager in the DOM, so a keyboard user must Tab through everything to reach it.
    - The reader has no `role=dialog` and no `aria-modal`, and on mobile it covers the page without trapping focus.
    - The first result takes 38 Tab presses to reach, because of 19 filter controls and about 19 facet buttons, and there is no skip link.
    - The "✕" button is only 25x26px on mobile. The reader-bar links are 25px high, the facet chips 19px and the filter "clear" button 25px, all well under the 44px target.

## UX problems and confusing spots
- **First impression (desktop):** the search box, mode choice and filters are clear, but a large blank page sits below. There are no sample searches, no hint of what "11,062 אגרות" covers, and the "טיפים" section is collapsed. A first-time user does not learn that `"..."`, `-` and `*` exist.
- **Mode labels:** "לפי משמעות" and "משולב" are selectable, and the error appears only after a search. "האינדקס הסמנטי עדיין נבנה" is clear but gives no next step. Hybrid silently falls back to text, with the warning in red after the count and no statement that the results are text-only. The zero-result link "לחפש לפי משמעות" leads to this error.
- **Filter labels:**
  - "שנה" next to "שנה לועזית" is ambiguous (add "עברית").
  - The volume options "א (1-168)" are letter ranges, which is not obvious.
  - Both volume selects show "א"/"כח" as if a filter were set.
  - "מספיק שאחת המילים תופיע" and "כולל אותיות שימוש" have no tooltip.
  - "ניקוי סינונים" does not clear the search text, and the name is not specific.
- **Facet chips:** they are single-select and replace the existing filter, and there is no visible "active filter" chip or per-filter removal. After clicking a volume chip the user must find the volume range in the form to undo it. The chips also show only the top 8, with no way to see all.
- **Result card:** the sort order is not shown in the status line. The "אל:" line shows the raw address, including garbled strings like "בצרכי ציבור באמונה וכו' מר שניאור זלמן שי'". Undated letters show "[ה'תשל"ח]" in brackets with no explanation.
- **Reader:** the text size (1.12rem, line-height 1.75) and the mark highlights are good, and the notes are small but readable. The header repeats "ב"ה, י"ט אד"ש, תשי"ט" in both the metadata and the body, and the recipient is not in the header at all. The "במקור ↗" link is hidden among the controls, and "העתק קישור" gives no visible fallback if the clipboard is blocked (it fails silently).
- **Zero results:** "לא נמצאו אגרות." is terse. It does not say which filters are active or suggest removing them (impossible filter `vol 1 + lang en` is only "לא נמצאו אגרות. לחפש לפי משמעות"). For a nonsense word, offering the semantic search is fine, but there is no spell/prefix hint.
- **Back button:** works and restores the form, but "close the reader" adds a history entry, so Back after closing reopens the letter.
- **History noise:** the page title never includes the query.
- **No loading indicator:** old results stay on screen with only "מחפש…" in a grey line.
- **Pager:** only arrows and numbers, with no "עמוד 3 מתוך 14" text. After a page change, scrolling to the top is correct.

## Improvement ideas, ranked by user impact
1. **Collapse the filters on mobile** (`<details>`/"סינון (n)" button, closed by default, with the active-filter count in the label), and make the three modes a segmented control in one row. Files: index.html and style.css, `@media (max-width: 900px)`. The first result should be visible after a search without scrolling. Consider a sticky compact search bar once results are showing.
2. **Fix the recipient story.** Show the resolved addressee (the footnote name) on the card ("אל: מר שניאור זלמן שזר") and highlight the matched recipient text. Consider one filter for the names in the footnote. In the reader, add the recipient to the header and tie notes to markers in the body.
3. **Show active filters as removable chips** above the results ("כרך כז ✕", "שנה: תשי"ז ✕", "אל: שזר ✕") and return a useful zero-result message ("אין תוצאות עם הסינון הנוכחי - הסר: כרך כז / שפה אנגלית"). Warn on inverted ranges, and tell the user when only operators or one letter were entered.
4. **Make the reader navigation relate to the results.** "הבאה"/"הקודמת" should step through the current result list when a search is active (with "תוצאה 3 מתוך 266"), with a separate "אגרת קודמת/הבאה בכרך" option. Move focus to the reader on open and give it `role=dialog`/`aria-modal`, and return focus to the card on close. On mobile, make the bar controls 44px and move "✕" to a larger tap target.
5. **Fix the phrase snippet** so it contains the full match and highlights every term, and normalise punctuation direction (wrap the snippet in `<bdi>` or `dir="rtl"` with `unicode-bidi: plaintext` per paragraph).
6. **Handle failures visibly:** a toast or inline "האגרת לא נמצאה" for a bad `#letter=`, clamp `page`, and show the active page in the pager ("עמוד 3 מתוך 14"). Add `aria-label`s to every input (volume, year and number ranges, `greg_to`, `num_to`) and make the cards real links (an `<a>` inside the `<li>`).
7. **Make semantic and hybrid modes honest while the index builds:** disable the two radios with a note ("בקרוב"), or add "מוצגות תוצאות טקסט בלבד" to the hybrid warning. Hide the "לחפש לפי משמעות" button until the index is ready.
8. **Add example searches to the empty home page** (שידוך, "מחנה ישראל", רוגאטש*, שידוך -גירושין) as clickable chips, and open the tips by default on the first visit.
9. **Date formatting:** show Gregorian dates in Hebrew form ("29.3.1959" or "מרץ 1959"), de-duplicate date_raw, and explain the brackets on editorial dates in a tooltip.
10. **Quality of life:** auto-submit on the number and recipient inputs (debounced), add a skip-to-results link, include the query in the page title, and make the facet chips bigger on mobile.
