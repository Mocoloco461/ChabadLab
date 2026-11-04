#!/usr/bin/env python3
"""
Turn the raw API responses downloaded by scrape.py into one clean record per
letter, written as JSON Lines (one letter per line).

Usage:
    python3 scripts/igrot_kodesh/parse.py [--out data/igtotkodsh]

Outputs (in --out):
    igrot_kodesh.jsonl.gz   all letters, full text + metadata
    igrot_kodesh_index.csv  metadata only (no text), one row per letter
    volumes.json            per-volume summary (letter ranges, counts)
"""
import argparse
import csv
import gzip
import html
import json
import re
from pathlib import Path

try:
    from pyluach import dates as hdates
except ImportError:  # gregorian conversion is optional
    hdates = None

SOURCE_URL = "https://chabadlibrary.org/books/{id}"

# ---------------------------------------------------------------- gematria
GEM = {"א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7, "ח": 8, "ט": 9,
       "י": 10, "כ": 20, "ך": 20, "ל": 30, "מ": 40, "ם": 40, "נ": 50, "ן": 50,
       "ס": 60, "ע": 70, "פ": 80, "ף": 80, "צ": 90, "ץ": 90, "ק": 100, "ר": 200,
       "ש": 300, "ת": 400}


def gematria(s):
    return sum(GEM.get(ch, 0) for ch in s)


def letter_number(heb):
    """'א'רכד' -> 1224, 'קסט' -> 169"""
    heb = heb.strip().replace("׳", "'").replace('"', "").replace("״", "")
    if "'" in heb:
        th, rest = heb.split("'", 1)
        if th and all(c in GEM for c in th):
            return gematria(th) * 1000 + gematria(rest)
    return gematria(heb) or None


# ---------------------------------------------------------------- html → text
def html_to_text(fragment):
    t = re.sub(r"<br\s*/?>\s*", "\n", fragment, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = html.unescape(t).replace("\r", "")
    t = re.sub(r"[ \t ]+", " ", t)
    t = "\n".join(line.strip() for line in t.split("\n"))
    return t.strip()


BLOCK_RE = re.compile(
    r"<(p|h4|div|h\d)\b([^>]*)>(.*?)</\1>", re.S | re.I)


def blocks(text_html):
    """Yield (class, inner_html) for each top level block in document order."""
    for m in BLOCK_RE.finditer(text_html):
        attrs = m.group(2)
        cm = re.search(r"class\s*=\s*[\"']?([\w-]+)", attrs)
        yield (cm.group(1) if cm else ""), m.group(3)


# ---------------------------------------------------------------- dates
MONTHS = [  # (regex, month number in pyluach numbering: Nissan=1 ... Adar=12, Adar II=13)
    (r"אדר\s*(?:א|א'|ראשון|הא'|הראשון)(?![א-ת])|אד\"ר|אדר\"א", 12, "אדר א"),
    (r"אדר\s*(?:ב|ב'|שני|הב'|השני)(?![א-ת])|אד\"ש|אדר\"ב", 13, "אדר ב"),
    (r"(?:מר)?חשוון|(?:מר)?חשון|מרחשון|מ\"ח", 8, "חשון"),
    (r"תשרי", 7, "תשרי"),
    (r"כסלו|כסליו", 9, "כסלו"),
    (r"טבת", 10, "טבת"),
    (r"שבט", 11, "שבט"),
    (r"אדר", 12, "אדר"),
    (r"ניסן", 1, "ניסן"),
    (r"אייר|איר", 2, "אייר"),
    (r"סיון|סיוון", 3, "סיון"),
    (r"תמוז", 4, "תמוז"),
    (r"מנחם[\s-]*אב|מנ\"א|אב", 5, "מנחם אב"),
    (r"אלול", 6, "אלול"),
]
HEB = "אבגדהוזחטיכלמנסעפצקרשת"
NUM_TOKEN = rf"[{HEB}]{{1,3}}(?:\"|״|'|׳)?[{HEB}]?"


HOLIDAYS = [  # (regex, day, month number, month name) for dates written as a festival name
    (r"ער\"ה|ערב ר\"ה|ערב ראש השנה", 29, 6, "אלול"),
    (r"צום גדלי|צום השביעי", 3, 7, "תשרי"),
    (r"עיוהכ\"פ|ערב יוהכ\"פ|עיוה\"כ", 9, 7, "תשרי"),
    (r"ער\"ס|ערחה\"ס|ערב חה\"ס", 14, 7, "תשרי"),
    (r"הושע\"ר|הושענא רבה", 21, 7, "תשרי"),
    (r"שמח\"ת|שמחת תורה", 23, 7, "תשרי"),
    (r"י\"ט כסלו", 19, 9, "כסלו"),
    (r"ר\"ה לאילנות|ט\"ו בשבט", 15, 11, "שבט"),
    (r"ער\"פ|ערב פסח|ערחה\"פ", 14, 1, "ניסן"),
    (r"ל\"ג בעומר|לג\"ב|הילולא דרשב\"י", 18, 2, "אייר"),
    (r"ערה\"ש|ערחה\"ש|ערב חה\"ש|ערב שבועות", 5, 3, "סיון"),
    (r"ימי ה?הגבלה", 3, 3, "סיון"),
    (r"ערב חג השבועות|ערב שבועות", 5, 3, "סיון"),
    (r"פסח שני", 14, 2, "אייר"),
    (r"ערב חנוכה", 24, 9, "כסלו"),
    (r"חנוכה", None, 9, "כסלו"),
    (r"שושן פורים", 15, 12, "אדר"),
    (r"פורים קטן", 14, 12, "אדר א"),
    (r"פורים", 14, 12, "אדר"),
    (r"סליחות", None, 6, "אלול"),
    (r"חוה\"מ סוכות|חוהמ\"ס|חול המועד סוכות", None, 7, "תשרי"),
    (r"חוה\"מ פסח|חוהמ\"פ|חול המועד פסח", None, 1, "ניסן"),
    (r"בין כסא לעשור|עשי\"ת|עשרת ימי תשובה", None, 7, "תשרי"),
    (r"י\"ב[- ]י\"ג תמוז|חג הגאולה", 12, 4, "תמוז"),
    (r"ט\"ו מנ\"א|ט\"ו באב", 15, 5, "מנחם אב"),
    (r"ח\"י אלול", 18, 6, "אלול"),
]


def parse_hebrew_date(raw):
    """Best-effort parse of a date line such as 'ב"ה, י"ג אלול, תש"ב'.
    Returns dict with day / month / month_name / year (numbers may be None)."""
    if not raw:
        return {}
    s = raw.replace("״", '"').replace("׳", "'")
    s = re.sub(r"ב\s*\"\s*ה\.?,?", " ", s, count=1)  # strip ב"ה
    res = {"day": None, "month": None, "month_name": None, "year": None}
    mpos = None
    for rx, num, name in MONTHS:
        m = re.search(rf"(?<![{HEB}])(?:ל|ב)?(?:{rx})(?![{HEB}])", s)
        if m:
            res["month"], res["month_name"], mpos = num, name, m
            break
    # year: a gematria token containing a gershayim, ending the string or after the month
    tail = s[mpos.end():] if mpos else s
    tokens = re.findall(rf"(?<![{HEB}])((?:ה')?[{HEB}]{{1,4}}\"[{HEB}])(?![{HEB}])", tail)
    for tok in reversed(tokens):  # the year is normally the last abbreviation
        tok = tok.replace("'", "")
        if tok.startswith("ה") and len(tok) > 3:  # ה'תש"ב / השי"ת: ה = 5000
            tok = tok[1:]
        y = gematria(tok)
        # short form only for פ"ח..צ"ט (5688-5699); long form must start with ת/ש (תש"ב, שי"ת)
        if y < 400:
            if not (88 <= y <= 99):
                continue
        elif tok[0] not in "תש" or not (688 <= y <= 765):
            continue
        if y < 400:      # short form without hundreds, e.g. פ"ח = (תר)פ"ח = 5688
            y += 5600
        elif y < 1000:   # e.g. תש"ב = 5702
            y += 5000
        if 5680 <= y <= 5760:
            res["year"] = y
            break
    if not mpos:
        for rx, day, num, name in HOLIDAYS:
            if re.search(rx, s):
                res.update(day=day, month=num, month_name=name)
                break
    if mpos:
        head = s[:mpos.start()]
        if re.search(r"ער\"ח|ערב ר\"ח|א' דר\"ח", head):
            pass  # eve of / first day of a two-day Rosh Chodesh = 30th of previous month
        elif re.search(r"ב' דר\"ח", head):
            res["day"] = 1
        elif re.search(r"(?<![{0}])ר\"ח".format(HEB), head):
            res["day"] = 1
        else:
            dm = re.findall(rf"(?<![{HEB}])([{HEB}]{{1,2}}(?:\"[{HEB}]|'))", head)
            for tok in reversed(dm):
                d = gematria(tok)
                # skip 'יום ב'' style day-of-week tokens
                if 1 <= d <= 30 and not re.search(r"יום\s*$", head[:head.rfind(tok)]):
                    res["day"] = d
                    break
    return res


def to_gregorian(d):
    """ISO date (or YYYY-MM when the day is unknown). In a leap year a plain
    'אדר' is taken as Adar I; 'אדר ב' in a non-leap year falls back to Adar."""
    if not hdates or not d.get("year") or not d.get("month"):
        return None
    for m in (d["month"], 12):
        try:
            g = hdates.HebrewDate(d["year"], m, d.get("day") or 1).to_pydate()
            return g.isoformat() if d.get("day") else g.isoformat()[:7]
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- language
YI = re.compile(r"(?<![א-ת])(איז|און|דער|דאס|ניט|נישט|פון|זיין|אויף|מיט|וואס|ווערן|האט|זיך|אלע|מען|איך|אייך|זייער)(?![א-ת])")
HE_FUNC = re.compile(r"(?<![א-ת])(של|אשר|את|על|זה|כי|גם|אם|הנה|ובודאי|כמובן)(?![א-ת])")


def detect_lang(text):
    lat = len(re.findall(r"[A-Za-z]", text))
    cyr = len(re.findall(r"[А-Яа-я]", text))
    heb = len(re.findall(r"[א-ת]", text))
    total = lat + cyr + heb or 1
    if cyr / total > 0.5:
        return "ru"
    if lat / total > 0.5:
        return "en"
    yi, he = len(YI.findall(text)), len(HE_FUNC.findall(text))
    if yi > he:
        return "yi"
    return "he"


# ---------------------------------------------------------------- recipient
HONORIFIC_END = re.compile(r"(שי'|שיחי'|שליט\"א|נ\"י|תחי'|יחי'|ני'|ע\"ה|ז\"ל)")


GENERIC_BLESSING = re.compile(r"^(ה'|השם)\s+(עליהם|עליכם|עליהן)")


def recipient_name(address_lines):
    """Pick the address line that most likely holds the person's name."""
    for i, line in enumerate(address_lines):
        if HONORIFIC_END.search(line):
            if i and len(HONORIFIC_END.sub("", line).split()) < 2:
                line = address_lines[i - 1] + " " + line
            return line.strip(" ,.")
    lines = [x for x in address_lines if not GENERIC_BLESSING.match(x.strip())] or address_lines
    return lines[-1].strip(" ,.") if lines else None


# ---------------------------------------------------------------- letter
# class name -> role, for the two HTML schemas used on the site
# (volumes 1-24: igroys_*; volumes 25-28: bh / brooklyn / lichvod / chik ...)
ROLE = {
    "pagenumber": "number",
    "igroys_bsd": "date", "bh": "date",
    "taarich": "date_editorial",
    "igroys_city": "city", "brooklyn": "city",
    "igroys_address": "address", "lichvod": "address", "lichvod2": "address",
    "igroys_signature": "sig", "igroys_signature2": "sig", "chik": "sig", "mazkir": "sig",
    "igroys_comment_number": "skip", "oysbehaoro": "skip",
    "igroys_comment": "note",
}
INNER_DIV = re.compile(r"^\s*<div\b[^>]*class\s*=\s*[\"']?([\w-]+)[^>]*>(.*?)</div>", re.S | re.I)


def letter_blocks(text_html):
    """(role, inner_html) in document order, for both schemas."""
    for cls, inner in blocks(text_html):
        m = INNER_DIV.match(inner)  # new schema: <p class=bodytext><div class=X>..</div></p>
        if m:
            cls, inner = m.group(1), m.group(2)
        yield ROLE.get(cls, "text"), inner


def parse_note(inner, term_tag):
    """Split a footnote into (term, text). term_tag: regex of the element holding the term."""
    txt = html_to_text(inner)
    m = re.match(rf"\s*(?:<a\b[^>]*>)?\s*{term_tag}", inner, re.S)
    if m:
        # term = text up to the first ':' / '=' (within the first 120 chars)
        tm = re.match(r"(.{1,120}?)\s*[:=]\s*(.*)", txt, re.S)
        if tm:
            return tm.group(1).strip(), tm.group(2).strip()
    return None, txt


def parse_letter(node, vol_index, vol_heading, vol_id):
    c = node["content"]
    d = c["data"]
    text_html = d.get("text") or ""
    num_heb = None
    date_lines, city_lines, address_lines, body, post, sigs, notes = [], [], [], [], [], [], []
    editorial = []
    seen_sig = False
    for role, inner in letter_blocks(text_html):
        txt = html_to_text(inner)
        if role == "number":
            num_heb = num_heb or txt
            continue
        if not txt or role == "skip":
            continue
        if role == "date":
            date_lines.append(txt)
        elif role == "date_editorial":
            editorial.append(txt.strip("[]"))
        elif role == "city":
            city_lines.append(txt)
        elif role == "address":
            address_lines += [x for x in txt.split("\n") if x.strip()]
        elif role == "sig":
            sigs.append(txt)
            seen_sig = True
        elif role == "note":
            term, note_txt = parse_note(inner, r"<b>")
            notes.append({"term": term, "text": note_txt})
        else:
            (post if seen_sig else body).append(txt)

    # volumes 25-28 keep the footnotes in a separate "haoros" field
    for cls, inner in blocks(d.get("haoros") or ""):
        if "oysbehaoro" in inner or not html_to_text(inner):
            continue
        term, note_txt = parse_note(inner, r"<span[^>]*haorodiburmaschil")
        notes.append({"term": term, "text": note_txt})

    date_raw = " | ".join(date_lines) or None
    # undated letters often carry an editorial date like "[לפני ת"ש]" in the first line
    date_editorial = " | ".join(editorial) or None
    if not date_raw and not date_editorial and body and re.fullmatch(r"\[[^\]]{1,40}\]", body[0]):
        date_editorial = body[0].strip("[]")
    hd = parse_hebrew_date(date_raw or date_editorial)
    # source note: first note without a bold term (e.g. "נדפסה ב... / מהעתקה")
    source_note = next((n["text"] for n in notes if not n["term"]), None)

    full_text = "\n\n".join(
        [*date_lines, *city_lines, "\n".join(address_lines)] + body + sigs + post)
    full_text = re.sub(r"\n{3,}", "\n\n", full_text).strip()

    rec = {
        "id": node_id(node),
        "volume": vol_index,
        "volume_heb": vol_heading.replace("כרך", "").strip(),
        "volume_node_id": vol_id,
        "letter_number": letter_number(num_heb) if num_heb else None,
        "letter_number_heb": num_heb,
        "date_raw": date_raw,
        "date_editorial": date_editorial,
        "hebrew_day": hd.get("day"),
        "hebrew_month": hd.get("month_name"),
        "hebrew_year": hd.get("year"),
        "gregorian_date": to_gregorian(hd),
        "city": " ".join(city_lines) or None,
        "recipient": recipient_name(address_lines),
        "address": "\n".join(address_lines) or None,
        "language": detect_lang("\n".join(body)),
        "body": "\n\n".join(body),
        "postscript": "\n\n".join(post) or None,
        "signature": "\n".join(sigs) or None,
        "source_note": source_note,
        "notes": notes,
        "full_text": full_text,
        "html": text_html,
        "prev_id": c.get("nav", {}).get("previous"),
        "next_id": c.get("nav", {}).get("next"),
        "url": SOURCE_URL.format(id=node_id(node)),
    }
    return rec


def node_id(node):
    return node["content"]["tree"][-1]["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/igtotkodsh")
    args = ap.parse_args()
    out = Path(args.out)
    raw = out / "raw"
    index = json.loads((raw / "index.json").read_text(encoding="utf-8"))
    vols = index["content"]["data"]

    records, vol_summary = [], []
    for vi, vol in enumerate(vols, 1):
        vdir = raw / str(vol["id"])
        recs = []
        for f in vdir.glob("[0-9]*.json"):
            node = json.loads(f.read_text(encoding="utf-8"))
            if node["content"]["type"] != "page":
                continue
            recs.append(parse_letter(node, vi, vol["heading"], vol["id"]))
        recs.sort(key=lambda r: (r["letter_number"] or 0, r["id"]))
        records += recs
        nums = [r["letter_number"] for r in recs if r["letter_number"]]
        vol_summary.append({
            "volume": vi, "volume_heb": vol["heading"], "node_id": vol["id"],
            "letters": len(recs),
            "first_letter": min(nums) if nums else None,
            "last_letter": max(nums) if nums else None,
            "dated": sum(1 for r in recs if r["hebrew_year"]),
        })
        print(f"{vol['heading']}: {len(recs)} letters")

    with gzip.open(out / "igrot_kodesh.jsonl.gz", "wt", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    cols = ["id", "volume", "volume_heb", "letter_number", "letter_number_heb", "date_raw",
            "date_editorial", "hebrew_day", "hebrew_month", "hebrew_year", "gregorian_date",
            "city", "recipient", "language", "source_note", "url"]
    with open(out / "igrot_kodesh_index.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)

    (out / "volumes.json").write_text(json.dumps(vol_summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"total {len(records)} letters")


if __name__ == "__main__":
    main()
