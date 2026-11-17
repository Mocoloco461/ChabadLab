# מנוע חיפוש לאגרות קודש

> **ממשיכים את העבודה? מתחילים ב-[`NEXT_STEPS.md`](NEXT_STEPS.md)** (הקמה בשרת GPU והצעדים הבאים) וב-`WORKLOG.md` בשורש הריפו.

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

**שרת עם GPU (Windows + Git Bash, ‏RTX 4070):**
```bash
python -m pip install --user uv
python -m uv venv .venv --python 3.12 --managed-python
python -m uv pip install --python .venv/Scripts/python.exe torch --index-url https://download.pytorch.org/whl/cu128
python -m uv pip install --python .venv/Scripts/python.exe fastapi "uvicorn[standard]" numpy sentence-transformers httpx
export PYTHONUTF8=1
.venv/Scripts/python.exe search-site/build_index.py
.venv/Scripts/python.exe search-site/build_embeddings.py --model Qwen/Qwen3-Embedding-4B --batch 8 --save-every 5000        # כ-26 דקות, הורדה של 8GB
.venv/Scripts/python.exe search-site/build_embeddings.py --model intfloat/multilingual-e5-large --batch 32 --save-every 5000 # כ-2 דקות
```

**איזה אינדקס סמנטי נטען:** השרת בוחר אוטומטית את האפשרות החזקה ביותר שהבנייה שלה הסתיימה:
1. **השילוב Qwen3-Embedding-4B + e5-large** (סכום הדמיון של שני המודלים). זו האפשרות הטובה ביותר בבדיקות.
2. bge-m3 + e5-large
3. Qwen3-4B
4. bge-m3
5. e5-large
6. e5-base
7. e5-small

* אפשר לבחור ידנית עם `IGROT_EMB=<folder>`, או עם כמה תיקיות מופרדות בפסיק כדי לשלב מודלים. כל המודלים בשילוב חייבים להיות בנויים על אותם קטעים.
* השילוב מריץ שני מודלים לכל שאילתה.
  * **על GPU:** כ-55 מ"ש לשאילתה.
  * **על CPU:** ‏Qwen3-4B לוקח כ-0.6 שניות לשאילתה, ו-bge-m3 + e5-large לוקחים כ-65 מ"ש.
  * בשרת בלי GPU כדאי לבנות רק את bge-m3 ואת e5-large.
**מדרג מחדש (reranker):** במצבים "לפי משמעות" ו"משולב", 50 האגרות הראשונות מדורגות מחדש עם `BAAI/bge-reranker-v2-m3` (cross-encoder).
* המודל מקבל את השאילתה ואת הקטע הכי קרוב של כל אגרת. הסדר הסופי משלב את הדירוג הראשון ואת דירוג ה-reranker (RRF ‏1:1, ‏k=10).
* כ-0.3 שניות לשאילתה על GPU.
* **ברירת מחדל:** פועל אוטומטית כשיש GPU, וכבוי על CPU.
* `IGROT_RERANK=off` מכבה אותו, ו-`IGROT_RERANK=<model>` בוחר מודל אחר.
* `IGROT_RERANK_N` קובע כמה אגרות מדורגות מחדש. 100 נותן כ-1.5 נקודות יותר, וכ-0.6 שניות לשאילתה.
* שאילתה עם תחביר מדויק (מרכאות, `*`, `-`) לא עוברת דירוג מחדש.
* **כוונון:** `eval/tune_rerank.py --model <reranker>`.

**תיקון שגיאות כתיב (`app/spell.py`):**
* **מה מתוקן:** רק מילה שלא מופיעה באף אגרת, גם לא אחרי הסרת אותיות השימוש. התיקון הוא למילה שכיחה במרחק עריכה 1, או 2 במילה ארוכה. יש עדיפות לבלבולים נפוצים כמו ט/ת, כ/ח, ס/ש, א/ע וחסר או עודף י/ו.
* **במצב טקסט:** החיפוש רץ על השאילתה המתוקנת, ומוצגת הודעה "מציג תוצאות עבור..." עם כפתור לחיפוש בדיוק (`nocorrect=1`).
* **במצב משולב:** רק צד המילים מתוקן, והמודל הסמנטי מקבל את המקור. כך מילה מודרנית שלא מופיעה באגרות לא "מתוקנת" בטעות.
* **מגבלה:** שגיאה שיוצרת מילה אמיתית אחרת (שבת/שבט) לא מזוהה.
* `IGROT_SPELL=0` מכבה את התיקון.

**שמות של נמענים:** בשאילתה קצרה (עד 5 מילים) שרוב המילים בה מופיעות בשורות נמען, למשל "שמטוב לונדון", אגרות שהנמען שלהן מכיל את כל המילים האלה מקבלות משקל נוסף במצב המשולב (RRF ‏1.5). `IGROT_REC_W=0` מכבה את זה.

**מיון במצב משולב ובמצב משמעות:** `sort=date|date_desc|number` ממיין את כל האגרות שנמצאו. כשממיינים, ה-reranker לא רץ.

**שנה לועזית:** לכל אגרת יש טווח (`greg_year_min`/`greg_year_max`). באגרות בלי תאריך לועזי (395), הטווח נגזר מהשנה העברית:
* אם בשורת התאריך מופיע חודש או חג, הטווח מצטמצם לשנה אחת. נבדק על 9,529 אגרות עם תאריך ידוע: נכון ב-99.98% מהמקרים.
* אחרת הטווח הוא שתי שנים.

סינון לפי שנה לועזית תופס אגרת אם הטווח שלה חופף.

* **ב-Windows:** בבדיקות כתבו `127.0.0.1` ולא `localhost`, כי `localhost` מוסיף 2 שניות לכל בקשה.
* **השוואת מודלים:** `search-site/scripts/eval_model.sh <slug>[,<slug2>] "<label>"` מעלה שרת זמני ומריץ את שני הסטים.

## API
* `GET /api/search?q=&mode=text|semantic|hybrid&page=&size=&sort=relevance|date|date_desc|number&prefixes=1&match=all|any&vol_from&vol_to&year_from&year_to&greg_from&greg_to&num_from&num_to&lang&month&recipient&nocorrect=1`
  * בתשובה יש שדה `corrected` (`from`/`to`/`fixes`) כשבוצע תיקון כתיב, ושדה `reranked`.
* `GET /api/letter/{id}`
* `GET /api/letter/number/{n}`
* `GET /api/meta`

## בדיקות: לולאת משוב

* **`eval/cases/*.jsonl`** – מקרי בדיקה. סוכנים כותבים אותם מתוך אגרות אקראיות (`eval/samples/`) בלי לראות את המנוע.
* **`eval/run_eval.py`** – מריץ את כל המקרים מול השרת. מקרה עובר אם האגרת הנכונה מופיעה ב-10 התוצאות הראשונות. התוצאות נכתבות ל-`eval/results/` ולשורה חדשה ב-`eval/history.csv`.

```bash
python3 search-site/eval/run_eval.py --label "תיאור השינוי"
```
* **היעד:** 80% הצלחה בסך הכול, בסט הראשי ובסטי האימות (`eval/holdout/holdout1-3.jsonl`).
* **סט האימות הראשי הוא holdout3:** סוכנים בלי הקשר כתבו אותו ובדקו אותו. לא מכוונים עליו.
* **סט פיתוח לסוגים חדשים** (`cases/dev_newtypes.jsonl`): אנגלית, נושא, שגיאת כתיב, שם. הוא חלק מהסט הראשי, ומותר לכוונן עליו.
* **מדדים:**
  * **קפדני:** האגרת המקורית מופיעה ב-10 הראשונות.
  * **`mrr`:** ממוצע של 1 חלקי המקום של האגרת המקורית.
  * **`any_rel@10` / `any_rel2@10`:** יש ב-10 הראשונות אגרת שסומנה רלוונטית. בגרסת 2 נספרות רק אגרות שקיבלו ציון 2.
  * **`ndcg@10`**
* **מאיפה באים סימוני הרלוונטיות (`eval/qrels/`):**
  * **שופטים:** סוכני LLM שמדרגים את התוצאות (`eval/judge.py prep|merge`).
  * **שאילתות של שמות:** כל אגרת שהנמען שלה מכיל את השם נחשבת רלוונטית. הקובץ נוצר עם `eval/name_qrels.py`.
* **השוואת ריצות:** `eval/compare.py "<תווית>"`.
* `HOLDOUTS=0 scripts/eval_model.sh ...` מריץ רק את הסט הראשי. כך עושים כשמשווים גרסאות, כדי לא להחליט לפי סטי האימות.
* **היסטוריה:** כל שינוי והשפעתו מתועדים ב-`WORKLOG.md` בשורש הריפו.
