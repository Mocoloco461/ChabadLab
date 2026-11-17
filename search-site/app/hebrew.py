"""Hebrew text helpers shared by the indexer and the server."""
import re

NIQQUD = re.compile(r"[֑-ֽֿ-ׇ]")  # points + cantillation (keeps maqaf U+05BE)
MAQAF = "־"
HEB_LETTERS = "אבגדהוזחטיכךלמםנןסעפףצץקרשת"

# one-letter prefixes ("אותיות השימוש") that attach to a Hebrew word
PREFIX_LETTERS = "והבלמשכ"
# common two-letter combinations: וה, וב, ול, ומ, וש, שה, שב, של, מה, בה, לה, כש, וכ, דה ...
PREFIX_COMBOS = ["וה", "וב", "ול", "ומ", "וש", "וכ", "שה", "שב", "של", "שמ", "מה", "כש", "וכש", "ושה", "ושב"]


def clean(text):
    """Text as indexed and displayed in snippets: no niqqud, unified quote marks."""
    if not text:
        return ""
    t = NIQQUD.sub("", text)
    t = t.replace(MAQAF, " ").replace("״", '"').replace("׳", "'")
    t = t.replace("”", '"').replace("“", '"').replace("’", "'").replace("‘", "'")
    return t


# ------------------------------------------------------------- query parsing
TOKEN_RE = re.compile(r'"[^"]+"|\S+')


def _fts_tokens(word):
    """A user word as the FTS5 unicode61 tokenizer sees it: split on quotes/punct.
    שליט"א -> ['שליט', 'א']"""
    return [t for t in re.split(r"[^\w]+", clean(word)) if t]


def _phrase(tokens):
    return '"' + " ".join(tokens) + '"'


def expand_word(word, prefixes=True):
    """FTS5 expression for one user word, optionally matching it with Hebrew prefixes.
    Words ending with * are prefix searches (FTS5 'word*')."""
    star = word.endswith("*")
    toks = _fts_tokens(word.rstrip("*"))
    if not toks:
        return None
    first, rest = toks[0], toks[1:]
    variants = [first]
    is_heb = bool(re.match(rf"^[{HEB_LETTERS}]", first))
    if prefixes and is_heb and len(first) >= 2:
        variants += [p + first for p in PREFIX_LETTERS]
        variants += [p + first for p in PREFIX_COMBOS]
    exprs = []
    for v in variants:
        if rest:
            exprs.append(_phrase([v] + rest) + ("*" if star else ""))
        else:
            exprs.append(f'"{v}"' + ("*" if star else ""))
    return "(" + " OR ".join(exprs) + ")" if len(exprs) > 1 else exprs[0]


def phrase_expr(words, prefixes=True):
    """Words that must appear consecutively (a single word is a 1-word phrase);
    the first word may take a Hebrew prefix."""
    toks = [t for w in words for t in _fts_tokens(w)]
    if not toks:
        return None
    first = [toks[0]]
    if prefixes and re.match(rf"^[{HEB_LETTERS}]", toks[0]) and len(toks[0]) >= 2:
        first += [p + toks[0] for p in PREFIX_LETTERS]
        if len(toks) == 1 or len(toks[0]) >= 3:
            first += [p + toks[0] for p in PREFIX_COMBOS]
    exprs = [_phrase([v] + toks[1:]) for v in first]
    return "(" + " OR ".join(exprs) + ")" if len(exprs) > 1 else exprs[0]


def build_fts_query(q, prefixes=True, mode="all"):
    """User query -> FTS5 MATCH expression.
    * "quoted text" = exact phrase (first word may still take a prefix)
    * -word         = exclude
    * word*         = prefix match
    mode: "all" (AND, default) | "any" (OR)
    Returns None if nothing searchable."""
    from abbrev import MAX_WORDS, alternatives
    parts, negs = [], []
    raws = TOKEN_RE.findall(q or "")
    i = 0
    while i < len(raws):
        # known abbreviation / spelling variant (longest match over plain words)
        matched = False
        for n in range(min(MAX_WORDS, len(raws) - i), 0, -1):
            seq = raws[i:i + n]
            if any(w.startswith(("-", '"')) or w.endswith("*") for w in seq):
                continue
            alts = alternatives(seq)
            if alts:
                exprs = [e for e in (phrase_expr(a.split(), prefixes) for a in alts) if e]
                parts.append("(" + " OR ".join(exprs) + ")")
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
        if raw.startswith('"') and raw.endswith('"') and len(raw) > 2:
            words = raw[1:-1].split()
            toks = [t for w in words for t in _fts_tokens(w)]
            if not toks:
                continue
            first_variants = [toks[0]]
            if prefixes and re.match(rf"^[{HEB_LETTERS}]", toks[0]) and len(toks[0]) >= 2:
                first_variants += [p + toks[0] for p in PREFIX_LETTERS]
            exprs = [_phrase([v] + toks[1:]) for v in first_variants]
            e = "(" + " OR ".join(exprs) + ")" if len(exprs) > 1 else exprs[0]
        else:
            e = expand_word(raw, prefixes)
        if not e:
            continue
        (negs if neg else parts).append(e)
    if not parts:
        return None
    joiner = " AND " if mode == "all" else " OR "
    expr = joiner.join(parts)
    if negs:
        expr = f"({expr}) NOT " + " NOT ".join(negs)
    return expr


def highlight_terms(q):
    """Plain words of the query, for client-side highlighting."""
    out = []
    for raw in TOKEN_RE.findall(q or ""):
        if raw.startswith("-"):
            continue
        out += [t for w in raw.strip('"').split() for t in _fts_tokens(w.rstrip("*"))]
    return out


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
