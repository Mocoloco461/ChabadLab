#!/usr/bin/env python3
"""
HTTP server for the Igrot Kodesh search site.

    cd search-site/app && uvicorn server:app --host 0.0.0.0 --port 8765

API
    GET /api/search?q=&mode=text|semantic|hybrid&page=1&size=20&sort=relevance|date|date_desc|number
                   &prefixes=1&match=all|any
                   &vol_from&vol_to&year_from&year_to&greg_from&greg_to&num_from&num_to
                   &lang=he,yi&month=&recipient=&dated=1
    GET /api/letter/{id}
    GET /api/letter/number/{n}      -> redirect target id for a letter number
    GET /api/meta                   volumes, years, languages, semantic index status
"""
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import search

STATIC = Path(__file__).resolve().parent / "static"
FILTER_KEYS = ("vol_from", "vol_to", "year_from", "year_to", "greg_from", "greg_to",
               "num_from", "num_to", "lang", "month", "recipient", "dated")

app = FastAPI(title="Igrot Kodesh search")


@app.on_event("startup")
def warm_up():
    # load the semantic index + model in the background so the first query is fast
    def _load():
        idx = search.semantic_index()
        if idx is not None:
            idx.encode("חינוך")
        search.reranker()
    threading.Thread(target=_load, daemon=True).start()


@app.get("/api/search")
def api_search(request: Request, q: str = "", mode: str = "text", page: int = 1, size: int = 20,
               sort: str = "relevance", prefixes: int = 1, match: str = "all"):
    t0 = time.time()
    p = request.query_params
    filters = {k: p.get(k) for k in FILTER_KEYS if p.get(k) not in (None, "")}
    size = max(1, min(size, 100))
    page = max(1, min(page, 100000))
    offset = (page - 1) * size
    q = q.strip()
    if mode == "semantic" and q:
        out = search.semantic_search(q, filters, offset, size)
    elif mode == "hybrid" and q:
        out = search.hybrid_search(q, filters, offset, size, prefixes=bool(prefixes))
    else:
        out = search.text_search(q, filters, sort, offset, size, bool(prefixes), match)
        mode = "text"
    out.update({"q": q, "mode": mode, "page": page, "size": size, "filters": filters,
                "ms": int((time.time() - t0) * 1000)})
    return JSONResponse(out)


@app.get("/api/letter/{lid}")
def api_letter(lid: int):
    d = search.get_letter(lid) if 0 < lid < 2 ** 62 else None
    if not d:
        raise HTTPException(404, "letter not found")
    return d


@app.get("/api/letter/number/{n}")
def api_letter_number(n: int):
    lid = search.find_letter(number=n) if 0 < n < 10 ** 6 else None
    if not lid:
        raise HTTPException(404, "letter not found")
    return {"id": lid}


@app.get("/api/meta")
def api_meta():
    return search.meta()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
