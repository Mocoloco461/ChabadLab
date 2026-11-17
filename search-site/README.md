# מנוע חיפוש לאגרות קודש

אתר חיפוש לכל 11,062 האגרות. הנתונים לקוחים מ-`data/igtotkodsh/igrot_kodesh.jsonl.gz`.

| מצב | מה הוא עושה | איך זה עובד |
|---|---|---|
| **טקסט** | מילים מדויקות, ביטויים במרכאות, החרגה (`-מילה`), תחילית (`מילה*`) | SQLite FTS5. מילים בעברית מורחבות אוטומטית עם אותיות השימוש (ו/ה/ב/ל/מ/ש/כ) |
| **לפי משמעות** | מוצא אגרות לפי רעיון, גם כשהמילים שונות, וגם מעברית ליידיש | הטמעות של מודל רב-לשוני (`multilingual-e5` / `bge-m3`) על קטעים של כ-900 תווים |
| **משולב** | שני הסוגים יחד | Reciprocal Rank Fusion של שתי הרשימות |

**סינונים:** כרך, שנה עברית ולועזית, חודש, שפה, נמען וכתובת (כולל הערת העורכים שמזהה את הנמען), מספר אגרת.

## התקנה והרצה

```bash
# סביבה (פעם אחת)
uv venv /root/venv-igrot && VIRTUAL_ENV=/root/venv-igrot uv pip install -r search-site/requirements.txt
VIRTUAL_ENV=/root/venv-igrot uv pip install torch --index-url https://download.pytorch.org/whl/cpu

# אינדקס טקסט (כ-25 שניות)
python3 search-site/build_index.py                  # -> search-site/data/search.db

# אינדקס סמנטי (מעבד בלבד: e5-small כ-30 דקות, bge-m3 כמה שעות)
/root/venv-igrot/bin/python search-site/build_embeddings.py --model intfloat/multilingual-e5-small

# שרת
cd search-site/app && /root/venv-igrot/bin/uvicorn server:app --host 0.0.0.0 --port 8765
```
התיקייה `search-site/data/` לא נכנסת ל-git, ובונים אותה מחדש עם הפקודות שלמעלה.
השרת טוען אוטומטית את האינדקס הסמנטי החזק ביותר שהבנייה שלו הסתיימה: קודם bge-m3, אחר כך e5-base, ואחר כך e5-small. אפשר לבחור אינדקס ספציפי עם המשתנה `IGROT_EMB=<folder>`.

## API
* `GET /api/search?q=&mode=text|semantic|hybrid&page=&size=&sort=relevance|date|date_desc|number&prefixes=1&match=all|any&vol_from&vol_to&year_from&year_to&greg_from&greg_to&num_from&num_to&lang&month&recipient`
* `GET /api/letter/{id}`
* `GET /api/letter/number/{n}`
* `GET /api/meta`

## בדיקות: לולאת משוב

* **`eval/cases/*.jsonl`** – מקרי בדיקה. סוכנים כותבים אותם מתוך אגרות אקראיות (`eval/samples/`) בלי לראות את המנוע.
* **`eval/run_eval.py`** – מריץ את כל המקרים מול השרת. מקרה עובר אם האגרת הנכונה מופיעה ב-10 התוצאות הראשונות. התוצאות נכתבות ל-`eval/results/` ולשורה חדשה ב-`eval/history.csv`.

```bash
python3 search-site/eval/run_eval.py --label "תיאור השינוי"
```
* **היעד:** 80% הצלחה בסך הכול.
* **היסטוריה:** כל שינוי והשפעתו מתועדים ב-`WORKLOG.md` בשורש הריפו.
