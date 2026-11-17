"""Hebrew text helpers shared by the indexer and the server.

Indexing model
    The FTS index holds `index_text(text)`: niqqud removed, maqaf -> space, and
    gershayim/geresh *inside* a word removed (שליט"א -> שליטא, לקו"ת -> לקות),
    so an abbreviation is one token and cannot match across word boundaries
    (ד"ה no longer matches חב"ד ה'). Queries are normalized the same way.
    Snippets are cut from the original text in Python (see search.make_snippet).
"""
import re

NIQQUD = re.compile(r"[֑-ֽֿ-ׇ]")  # points + cantillation (keeps maqaf U+05BE)
MAQAF = "־"
HEB_LETTERS = "אבגדהוזחטיכךלמםנןסעפףצץקרשת"
GERSH_IN_WORD = re.compile(r"(?<=[א-ת])[\"'](?=[א-ת])")
SOURCE_JUNK = re.compile(r"\[mazkir\d*\]")

# one-letter prefixes ("אותיות השימוש") that attach to a Hebrew word
PREFIX_LETTERS = "והבלמשכד"
# common combinations
PREFIX_COMBOS = ["וה", "וב", "ול", "ומ", "וש", "וכ", "וד", "שה", "שב", "של", "שמ", "מה", "כש", "וכש", "ושה", "ושב",
                 "דה", "לה", "בה", "כה"]


def clean(text):
    """Display text: no niqqud, unified quote marks, source junk removed."""
    if not text:
        return ""
    t = NIQQUD.sub("", text)
    t = SOURCE_JUNK.sub("", t)
    t = t.replace(MAQAF, " ").replace("״", '"').replace("׳", "'")
    t = t.replace("”", '"').replace("“", '"').replace("’", "'").replace("‘", "'")
    return t


def index_text(text):
    """Text as stored in the FTS index (see module doc)."""
    return GERSH_IN_WORD.sub("", clean(text))


# ------------------------------------------------------------- query parsing
TOKEN_RE = re.compile(r'-?"[^"]+"|\S+')


def _prep_query(q):
    q = clean(q or "")
    # maqaf / hyphen between letters joins words: לקוטי-תורה -> לקוטי תורה (leading "-" = exclusion)
    q = re.sub(r"(?<=[א-תA-Za-z])-(?=[א-תA-Za-z])", " ", q)
    # gershayim inside a word must not be taken as a phrase quote
    return GERSH_IN_WORD.sub("\u0001", q)


def _restore(w):
    return w.replace("\u0001", '"')


def _fts_tokens(word):
    """A user word as the index sees it: gershayim removed, split on other punctuation."""
    w = GERSH_IN_WORD.sub("", word.replace("\u0001", '"'))
    return [t for t in re.split(r"[^\w]+", w) if t]


def _phrase(tokens):
    return '"' + " ".join(tokens) + '"'


def _variants(tok, prefixes):
    v = [tok]
    if prefixes and re.match(rf"^[{HEB_LETTERS}]", tok) and len(tok) >= 2:
        if len(tok) == 2:  # short words / abbreviations: full expansion is mostly noise (שדה, מדה for ד"ה)
            v += ["ו" + tok, "ה" + tok, "וה" + tok]
        else:
            v += [p + tok for p in PREFIX_LETTERS] + [p + tok for p in PREFIX_COMBOS]
    return v


def phrase_expr(words, prefixes=True, star=False):
    """Words that must appear consecutively (a single word is a 1-word phrase);
    the first word may take a Hebrew prefix."""
    toks = [t for w in words for t in _fts_tokens(w)]
    if not toks:
        return None
    exprs = [_phrase([v] + toks[1:]) + ("*" if star else "") for v in _variants(toks[0], prefixes)]
    return "(" + " OR ".join(exprs) + ")" if len(exprs) > 1 else exprs[0]


def parse_query(q):
    """-> list of items {"kind": word|phrase|alts, "neg", "words", "alts", "star"} (words in display form)."""
    from abbrev import MAX_WORDS, alternatives
    raws = TOKEN_RE.findall(_prep_query(q))
    items, i = [], 0
    while i < len(raws):
        matched = False
        for n in range(min(MAX_WORDS, len(raws) - i), 0, -1):
            seq = raws[i:i + n]
            if any(w.startswith(("-", '"')) or w.endswith(("*", '"')) for w in seq):
                continue
            alts = alternatives([_restore(w) for w in seq])
            if alts:
                items.append({"kind": "alts", "neg": False, "words": [_restore(w) for w in seq],
                              "alts": [a.split() for a in alts]})
                i += n
                matched = True
                break
        if matched:
            continue
        raw = raws[i]
        i += 1
        neg = raw.startswith("-") and len(raw) > 1
        if neg:
            raw = raw[1:]
        if len(raw) > 2 and raw.startswith('"') and raw.endswith('"'):
            words = [_restore(w) for w in raw[1:-1].split()]
            if words:
                items.append({"kind": "phrase", "neg": neg, "words": words})
            continue
        raw = _restore(raw.strip('"'))
        star = raw.endswith("*")
        raw = raw.rstrip("*")
        if _fts_tokens(raw):
            items.append({"kind": "word", "neg": neg, "words": [raw], "star": star})
    return items


def item_expr(it, prefixes=True, exact=False):
    """FTS expression for one parsed item. exact=True: no prefix expansion (tiered ranking)."""
    pf = prefixes and not exact
    if it["kind"] == "alts":
        exprs = [e for e in (phrase_expr(a, pf) for a in it["alts"]) if e]
        return "(" + " OR ".join(exprs) + ")" if exprs else None
    return phrase_expr(it["words"], pf, star=it.get("star", False))


def build_fts_query(q, prefixes=True, mode="all", exact=False, items=None):
    """User query -> FTS5 MATCH expression.
    * "quoted text" = exact phrase (first word may still take a prefix)
    * -word / -"phrase" = exclude
    * word*         = prefix match
    * known abbreviations / spelling variants are expanded (abbrev.py)
    mode: "all" (AND) | "any" (OR). Returns None if nothing searchable."""
    items = parse_query(q) if items is None else items
    parts = [e for e in (item_expr(it, prefixes, exact) for it in items if not it["neg"]) if e]
    negs = [e for e in (item_expr(it, prefixes, False) for it in items if it["neg"]) if e]
    if not parts:
        return None
    expr = (" AND " if mode == "all" else " OR ").join(parts)
    if negs:
        expr = f"({expr}) NOT " + " NOT ".join(negs)
    return expr


def phrase_query(q, prefixes=True):
    """For an unquoted multi-word query: the whole query as one phrase (for ranking)."""
    items = [it for it in parse_query(q) if not it["neg"]]
    if len(items) < 2 or any(it["kind"] != "word" or it.get("star") for it in items):
        return None
    return phrase_expr([it["words"][0] for it in items], prefixes)


def highlight_terms(q):
    """Words to highlight (incl. abbreviation alternatives), in display form."""
    out = []
    for it in parse_query(q):
        if it["neg"]:
            continue
        if it["kind"] == "alts":
            for a in it["alts"]:
                out += a
        else:
            out += it["words"]
    return [w for w in out if w]


def term_regex(word, prefixes=True):
    """Regex matching a word in display text: optional gershayim between letters,
    optional Hebrew prefixes, not inside another word."""
    core = GERSH_IN_WORD.sub("", word)
    core = re.sub(r"[^\w]+", "", core)
    if not core:
        return None
    body = "[\"']?".join(re.escape(c) for c in core)
    pre = f"(?:[{PREFIX_LETTERS}]{{0,2}})" if prefixes and re.match(rf"^[{HEB_LETTERS}]", core) else ""
    return rf"(?<![א-ת\w]){pre}{body}"


# ------------------------------------------------------------- hebrew numerals
_ONES = ["", "א", "ב", "ג", "ד", "ה", "ו", "ז", "ח", "ט"]
_TENS = ["", "י", "כ", "ל", "מ", "נ", "ס", "ע", "פ", "צ"]
_HUNDREDS = ["", "ק", "ר", "ש", "ת", "תק", "תר", "תש", "תת", "תתק"]


def heb_num(n, geresh=True):
    """5716 -> 'תשט"ז' (thousands dropped), 15 -> 'ט"ו'."""
    if not n:
        return ""
    n = n % 1000
    s = _HUNDREDS[n // 100]
    rem = n % 100
    if rem == 15:
        s += "טו"
    elif rem == 16:
        s += "טז"
    else:
        s += _TENS[rem // 10] + _ONES[rem % 10]
    if not geresh:
        return s
    return s[:-1] + '"' + s[-1] if len(s) > 1 else s + "'"


def heb_year(y):
    return heb_num(y) if y else ""
