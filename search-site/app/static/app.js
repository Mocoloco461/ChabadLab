"use strict";
const $ = (s) => document.querySelector(s);
const form = $("#search-form");
const FILTERS = ["vol_from", "vol_to", "year_from", "year_to", "greg_from", "greg_to",
                 "num_from", "num_to", "lang", "month", "recipient"];
const LANG = { he: "עברית", yi: "אידיש", en: "אנגלית", ru: "רוסית" };
let META = null;
let state = { q: "", mode: "text", page: 1, sort: "relevance", prefixes: "1", match: "all" };
let lastResults = [];
let currentLetter = null;

function esc(s) {
  return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
}

// ------------------------------------------------------------ meta / filters
async function loadMeta() {
  META = await api("/api/meta");
  $("#total-letters").textContent = META.total.toLocaleString("he-IL");
  for (const id of ["vol_from", "vol_to"]) {
    const sel = $("#" + id);
    sel.innerHTML = `<option value="">${id === "vol_from" ? "א" : "כח"}</option>` +
      META.volumes.map((v) => `<option value="${v.volume}">${v.volume_heb} (${v.first}–${v.last})</option>`).join("");
  }
  for (const id of ["year_from", "year_to"]) {
    const sel = $("#" + id);
    sel.innerHTML = `<option value="">${id === "year_from" ? "מתחילה" : "עד סוף"}</option>` +
      META.years.map((y) => `<option value="${y.year}">${y.label} (${y.n})</option>`).join("");
  }
  $("#month").innerHTML = `<option value="">הכל</option>` + META.months.map((m) => `<option>${esc(m)}</option>`).join("");
}

function readForm() {
  state.q = $("#q").value.trim();
  state.mode = form.mode.value;
  state.sort = $("#sort").value;
  state.prefixes = $("#prefixes").checked ? "1" : "0";
  state.match = $("#match_any").checked ? "any" : "all";
  for (const f of FILTERS) state[f] = $("#" + f).value.trim();
}

function writeForm() {
  $("#q").value = state.q || "";
  for (const r of form.mode) r.checked = r.value === (state.mode || "text");
  $("#sort").value = state.sort || "relevance";
  $("#prefixes").checked = state.prefixes !== "0";
  $("#match_any").checked = state.match === "any";
  for (const f of FILTERS) $("#" + f).value = state[f] || "";
}

function stateToParams(extra = {}) {
  const p = new URLSearchParams();
  const s = { ...state, ...extra };
  for (const [k, v] of Object.entries(s)) {
    if (v === "" || v == null) continue;
    if (k === "page" && +v === 1) continue;
    if (k === "sort" && v === "relevance") continue;
    if (k === "prefixes" && v === "1") continue;
    if (k === "match" && v === "all") continue;
    if (k === "mode" && v === "text") continue;
    p.set(k, v);
  }
  return p;
}

function stateFromUrl() {
  const p = new URLSearchParams(location.search);
  state = { q: "", mode: "text", page: 1, sort: "relevance", prefixes: "1", match: "all" };
  for (const [k, v] of p) state[k] = v;
  state.page = +state.page || 1;
}

function hasQuery() {
  return state.q || FILTERS.some((f) => state[f]);
}

// ------------------------------------------------------------ search
async function runSearch(push = true) {
  if (push) history.pushState(null, "", "?" + stateToParams().toString() + location.hash);
  if (!hasQuery()) {
    $("#status").textContent = "";
    $("#results").innerHTML = "";
    $("#pager").innerHTML = "";
    $("#facets").innerHTML = "";
    return;
  }
  const p = stateToParams();
  p.set("page", state.page);
  p.set("mode", state.mode);
  $("#status").textContent = state.mode === "text" ? "מחפש…" : "מחפש לפי משמעות…";
  let data;
  try {
    data = await api("/api/search?" + p.toString());
  } catch (e) {
    $("#status").innerHTML = `<span class="err">שגיאה בחיפוש (${esc(e.message)})</span>`;
    return;
  }
  lastResults = data.results;
  renderResults(data);
}

function renderResults(data) {
  const st = $("#status");
  if (data.error) {
    st.innerHTML = `<span class="err">${esc(data.error)}</span>`;
  } else {
    const modeName = { text: "טקסט", semantic: "לפי משמעות", hybrid: "משולב" }[data.mode];
    let msg = data.total
      ? `נמצאו <b>${data.total.toLocaleString("he-IL")}</b> אגרות${data.capped ? " (המובילות)" : ""} · חיפוש ${modeName} · ${data.ms} מ"ש`
      : "לא נמצאו אגרות.";
    if (!data.total && data.mode === "text" && state.q && state.match === "all" && state.q.trim().split(/\s+/).length > 1)
      msg += ` <button class="link" id="try-any">לחפש אגרות עם אחת המילים</button>`;
    if (!data.total && data.mode === "text" && state.q)
      msg += ` <button class="link" id="try-sem">לחפש לפי משמעות</button>`;
    if (data.warning) msg += ` <span class="err">${esc(data.warning)}</span>`;
    st.innerHTML = msg;
    $("#try-any")?.addEventListener("click", () => { $("#match_any").checked = true; submit(); });
    $("#try-sem")?.addEventListener("click", () => { form.mode.value = "semantic"; submit(); });
  }
  renderFacets(data.facets);
  $("#results").innerHTML = data.results.map((r, i) => `
    <li data-id="${r.id}" data-i="${i}" tabindex="0" class="${currentLetter === r.id ? "active" : ""}">
      <div class="head">
        <span class="num">אגרת ${esc(r.letter_number_heb || "")}</span>
        <span class="meta">כרך ${esc(r.volume_heb)}${r.date_raw ? " · " + esc(r.date_raw) : r.date_editorial ? " · [" + esc(r.date_editorial) + "]" : ""}${r.gregorian_date ? " · " + esc(r.gregorian_date) : ""}${r.language && r.language !== "he" ? " · " + (LANG[r.language] || r.language) : ""}</span>
      </div>
      ${r.recipient ? `<div class="rcpt">אל: ${esc(r.recipient)}</div>` : ""}
      <div class="snip">${r.snippet || ""}</div>
      ${data.mode === "hybrid" ? `<div class="tags">${r.in_text ? "✓ מילים" : ""} ${r.in_semantic ? "✓ משמעות" : ""}</div>` : ""}
    </li>`).join("");
  renderPager(data);
}

function renderFacets(f) {
  const el = $("#facets");
  if (!f) { el.innerHTML = ""; return; }
  const top = (arr, n) => [...arr].sort((a, b) => b.n - a.n).slice(0, n);
  const vols = f.volume.length > 1 ? `<span class="fgroup">כרכים: ${top(f.volume, 8).map((v) =>
    `<button data-f="vol" data-v="${v.key}">${META.volumes[v.key - 1]?.volume_heb ?? v.key} (${v.n})</button>`).join("")}</span>` : "";
  const yrs = f.year.filter((y) => y.key).length > 1 ? `<span class="fgroup">שנים: ${top(f.year.filter((y) => y.key), 8).map((y) =>
    `<button data-f="year" data-v="${y.key}">${y.label} (${y.n})</button>`).join("")}</span>` : "";
  const lng = f.language.length > 1 ? `<span class="fgroup">שפה: ${f.language.map((l) =>
    `<button data-f="lang" data-v="${l.key}">${LANG[l.key] || l.key} (${l.n})</button>`).join("")}</span>` : "";
  el.innerHTML = vols + yrs + lng;
  el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    const v = b.dataset.v;
    if (b.dataset.f === "vol") { $("#vol_from").value = v; $("#vol_to").value = v; }
    if (b.dataset.f === "year") { $("#year_from").value = v; $("#year_to").value = v; }
    if (b.dataset.f === "lang") { $("#lang").value = v; }
    submit();
  }));
}

function renderPager(data) {
  const pages = Math.ceil(data.total / data.size);
  const el = $("#pager");
  if (pages <= 1) { el.innerHTML = ""; return; }
  const cur = data.page;
  const nums = new Set([1, pages, cur - 2, cur - 1, cur, cur + 1, cur + 2].filter((n) => n >= 1 && n <= pages));
  let html = `<button ${cur === 1 ? "disabled" : ""} data-p="${cur - 1}">→</button>`;
  let prev = 0;
  for (const n of [...nums].sort((a, b) => a - b)) {
    if (n - prev > 1) html += "<span>…</span>";
    html += `<button class="${n === cur ? "cur" : ""}" data-p="${n}">${n}</button>`;
    prev = n;
  }
  html += `<button ${cur === pages ? "disabled" : ""} data-p="${cur + 1}">←</button>`;
  el.innerHTML = html;
  el.querySelectorAll("button[data-p]").forEach((b) => b.addEventListener("click", () => {
    state.page = +b.dataset.p;
    runSearch();
    window.scrollTo({ top: 0 });
  }));
}

function submit() {
  readForm();
  state.page = 1;
  runSearch();
}

// ------------------------------------------------------------ reader
function highlight(text, terms) {
  let s = esc(text);
  for (const t of [...new Set(terms)].sort((a, b) => b.length - a.length)) {
    if (t.length < 2) continue;
    const re = new RegExp(`(?<![א-ת])([והבלמשכ]{0,2}${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "g");
    s = s.replace(re, "<mark>$1</mark>");
  }
  return s;
}

function queryTerms() {
  if (!state.q) return [];
  return state.q.replace(/"/g, " ").split(/\s+/).filter((w) => w && !w.startsWith("-")).map((w) => w.replace(/\*$/, ""));
}

async function openLetter(id, push = true) {
  let d;
  try { d = await api("/api/letter/" + id); } catch { return; }
  currentLetter = d.id;
  const terms = state.mode === "text" ? queryTerms() : queryTerms().filter((t) => t.length > 2);
  const paras = (d.full_text || "").split(/\n\n+/).map((p) => `<p>${highlight(p, terms)}</p>`).join("");
  const notes = d.notes.length ? `<ol class="notes">${d.notes.map((n) =>
    `<li>${n.term ? `<b>${esc(n.term)}</b>: ` : ""}${esc(n.text)}</li>`).join("")}</ol>` : "";
  $("#letter").innerHTML = `
    <h2>אגרת ${esc(d.letter_number_heb || "")}</h2>
    <div class="lmeta">
      <span>כרך ${esc(d.volume_heb)}</span>
      ${d.date_raw ? `<span>${esc(d.date_raw)}</span>` : ""}
      ${d.date_editorial ? `<span>[${esc(d.date_editorial)}]</span>` : ""}
      ${d.gregorian_date ? `<span>${esc(d.gregorian_date)}</span>` : ""}
      ${d.language ? `<span>${LANG[d.language] || d.language}</span>` : ""}
    </div>
    ${paras}${notes}`;
  $("#source-link").href = d.url;
  $("#prev-letter").disabled = !d.prev_id;
  $("#next-letter").disabled = !d.next_id;
  $("#prev-letter").dataset.id = d.prev_id || "";
  $("#next-letter").dataset.id = d.next_id || "";
  $("#reader").classList.remove("hidden");
  $("#reader").scrollTop = 0;
  document.querySelectorAll(".results li").forEach((li) => li.classList.toggle("active", +li.dataset.id === d.id));
  if (push) history.pushState(null, "", location.pathname + location.search + "#letter=" + d.id);
  document.title = `אגרת ${d.letter_number_heb} · חיפוש באגרות קודש`;
}

function closeLetter(push = true) {
  $("#reader").classList.add("hidden");
  currentLetter = null;
  if (push) history.pushState(null, "", location.pathname + location.search);
  document.title = "חיפוש באגרות קודש";
}

// ------------------------------------------------------------ wiring
form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
for (const r of form.mode) r.addEventListener("change", () => { if (state.q) submit(); });
for (const id of ["sort", "prefixes", "match_any", "vol_from", "vol_to", "year_from", "year_to", "lang", "month"])
  $("#" + id).addEventListener("change", () => { if (hasQuery() || $("#" + id).value) submit(); });
$("#clear").addEventListener("click", () => { for (const f of FILTERS) $("#" + f).value = ""; submit(); });
$("#results").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li) openLetter(+li.dataset.id);
});
$("#results").addEventListener("keydown", (e) => {
  const li = e.target.closest("li[data-id]");
  if (li && e.key === "Enter") openLetter(+li.dataset.id);
});
$("#prev-letter").addEventListener("click", (e) => e.target.dataset.id && openLetter(+e.target.dataset.id));
$("#next-letter").addEventListener("click", (e) => e.target.dataset.id && openLetter(+e.target.dataset.id));
$("#close-reader").addEventListener("click", () => closeLetter());
$("#copy-link").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(location.href); $("#copy-link").textContent = "הועתק ✓"; } catch {}
  setTimeout(() => ($("#copy-link").textContent = "העתק קישור"), 1500);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && currentLetter) closeLetter(); });
window.addEventListener("popstate", () => { stateFromUrl(); writeForm(); runSearch(false); routeHash(); });

function routeHash() {
  const m = location.hash.match(/letter=(\d+)/);
  if (m) openLetter(+m[1], false); else if (currentLetter) closeLetter(false);
}

(async function init() {
  await loadMeta();
  stateFromUrl();
  writeForm();
  await runSearch(false);
  routeHash();
})();
