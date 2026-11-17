"""Spelling correction ("did you mean") for Hebrew query words.

Vocabulary = the FTS index terms with their document counts (fts5vocab, ~115k terms).
A query word is a candidate for correction only if neither it nor its stem (after
stripping Hebrew prefix letters) occurs in the corpus. Corrections are edit-distance
1 (2 for long words) variants that are frequent in the corpus; typical Hebrew
confusions (ט/ת, כ/ח, ס/ש, א/ע, ק/כ, ו/י added or dropped) are preferred.

A typo that produces another real word (שבת/שבט) cannot be detected this way.
"""
import math
import re
import threading

HEB = "אבגדהוזחטיכלמנסעפצקרשתךםןףץ"
PREFIXES = ("וכש", "וש", "וה", "וב", "ול", "ומ", "וכ", "של", "שב", "שה", "כש", "לכ", "מה", "שמ",
            "ו", "ה", "ב", "ל", "מ", "ש", "כ", "ד")
CONFUSABLE = {frozenset(p) for p in ("טת", "כח", "סש", "אע", "קכ", "בו", "וי", "צס", "גק")}
FINALS = {"כ": "ך", "מ": "ם", "נ": "ן", "פ": "ף", "צ": "ץ"}
MIN_DF = 3          # a correction must appear in at least this many letters
_word = re.compile(r"^[א-ת]+$")

_vocab = {"df": None}
_lock = threading.Lock()


def vocab(con):
    with _lock:
        if _vocab["df"] is None:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.spell_vocab USING fts5vocab(main, letters_fts, row)")
            _vocab["df"] = dict(con.execute("SELECT term, doc FROM temp.spell_vocab"))
    return _vocab["df"]


def _fix_final(w):
    """normalise final/non-final letter forms after an edit"""
    if not w:
        return w
    inner = "".join({v: k for k, v in FINALS.items()}.get(ch, ch) for ch in w[:-1])
    return inner + FINALS.get(w[-1], w[-1])


def _edits1(w):
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    out = {}
    for a, b in splits:
        if b:
            out[_fix_final(a + b[1:])] = ("del", b[0], "")
        if len(b) > 1:
            out[_fix_final(a + b[1] + b[0] + b[2:])] = ("swap", "", "")
        for ch in HEB[:22]:
            if b and ch != b[0]:
                out[_fix_final(a + ch + b[1:])] = ("sub", b[0], ch)
            out[_fix_final(a + ch + b)] = ("ins", "", ch)
    out.pop(w, None)
    return out


def _bonus(op):
    kind, x, y = op
    if kind == "swap":
        return 1.0
    if kind == "sub" and frozenset((x, y)) in CONFUSABLE:
        return 1.5
    if kind in ("del", "ins") and (x or y) in "וי":
        return 1.5
    return 0.0


def known(df, w):
    if df.get(w, 0) > 0:
        return True
    return any(w.startswith(p) and df.get(w[len(p):], 0) > 0 for p in PREFIXES if len(w) - len(p) >= 2)


def suggest(con, w):
    """-> corrected word or None"""
    if len(w) < 3 or not _word.match(w):
        return None
    df = vocab(con)
    if known(df, w):
        return None
    stems = [("", w)] + [(p, w[len(p):]) for p in PREFIXES if w.startswith(p) and len(w) - len(p) >= 3]
    best, best_score = None, 0.0
    for p, stem in stems:
        for cand, op in _edits1(stem).items():
            n = df.get(cand, 0)
            if n >= MIN_DF:
                score = math.log(n) + _bonus(op) - (0.3 if p else 0.0)
                if score > best_score:
                    best, best_score = p + cand, score
    if best is None and len(w) >= 6:  # edit distance 2 for long words, stricter frequency
        for cand1 in _edits1(w):
            for cand in _edits1(cand1):
                n = df.get(cand, 0)
                if n >= 10 and math.log(n) > best_score:
                    best, best_score = cand, math.log(n)
    return best


def correct_query(con, q):
    """-> (corrected query, [(wrong, right), ...]) ; only plain Hebrew words outside quotes are touched"""
    if '"' in q.replace('״', '"') and q.count('"') >= 2:
        # leave quoted phrases alone, correct the rest
        parts = re.split(r'("[^"]*")', q)
    else:
        parts = [q]
    fixes, out = [], []
    for part in parts:
        if part.startswith('"'):
            out.append(part)
            continue
        toks = re.split(r"(\s+)", part)
        for i, t in enumerate(toks):
            core = t.strip("-*")
            if t.startswith("-") or not core:
                continue
            s = suggest(con, core)
            if s:
                fixes.append((core, s))
                toks[i] = t.replace(core, s)
        out.append("".join(toks))
    return "".join(out), fixes
