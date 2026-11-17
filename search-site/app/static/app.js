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
  for (const [a, b] of [["vol_from", "vol_to"], ["year_from", "year_to"], ["greg_from", "greg_to"], ["num_from", "num_to"]]) {
    if (state[a] && state[b] && +state[a] > +state[b]) { [state[a], state[b]] = [state[b], state[a]]; $("#" + a).value = state[a]; $("#" + b).value = state[b]; }
  }
}

const FILTER_LABEL = {
  vol_from: (v) => `מכרך ${META?.volumes[v - 1]?.volume_heb ?? v}`, vol_to: (v) => `עד כרך ${META?.volumes[v - 1]?.volume_heb ?? v}`,
  year_from: (v) => `משנת ${yearLabel(v)}`, year_to: (v) => `עד שנת ${yearLabel(v)}`,
  greg_from: (v) => `מ-${v}`, greg_to: (v) => `עד ${v}`, num_from: (v) => `מאגרת ${v}`, num_to: (v) => `עד אגרת ${v}`,
  lang: (v) => LANG[v] || v, month: (v) => `חודש ${v}`, recipient: (v) => `נמען: ${v}`,
};
function yearLabel(y) { return META?.years.find((x) => x.year == y)?.label || y; }

function renderActiveFilters() {
  const active = FILTERS.filter((f) => state[f]);
  $("#filter-count").textContent = active.length ? String(active.length) : "";
  $("#active-filters").innerHTML = active.map((f) =>
    `<button type="button" data-f="${f}" title="הסרת הסינון">${esc(FILTER_LABEL[f](state[f]))} ✕</button>`).join("");
  $("#active-filters").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $("#" + b.dataset.f).value = ""; submit();
  }));
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

function updateChrome() {
  renderActiveFilters();
  $("#examples").classList.toggle("hidden", !!hasQuery());
}

// ------------------------------------------------------------ search
async function runSearch(push = true) {
  if (push) history.pushState(null, "", "?" + stateToParams().toString() + location.hash);
  updateChrome();
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
  $("#results").classList.add("loading");
  let data;
  try {
    data = await api("/api/search?" + p.toString());
  } catch (e) {
    $("#status").innerHTML = `<span class="err">שגיאה בחיפוש (${esc(e.message)})</span>`;
    $("#results").classList.remove("loading");
    return;
  }
  $("#results").classList.remove("loading");
  if (!data.results.length && data.total > 0 && state.page > 1) {  // page beyond the end
    state.page = Math.max(1, Math.ceil(data.total / data.size));
    return runSearch(true);
  }
  lastResults = data.results;
  document.title = (state.q ? `${state.q} · ` : "") + "חיפוש באגרות קודש";
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
      : "לא נמצאו אגרות." + (FILTERS.some((f) => state[f]) ? ` יש סינונים פעילים – אפשר להסיר אותם למעלה, או <button class="link" id="clear-all">לנקות את כל הסינונים</button>.` : "");
    if (!data.total && data.mode === "text" && state.q && state.match === "all" && state.q.trim().split(/\s+/).length > 1)
      msg += ` <button class="link" id="try-any">לחפש אגרות עם אחת המילים</button>`;
    if (!data.total && data.mode === "text" && state.q)
      msg += ` <button class="link" id="try-sem">לחפש לפי משמעות</button>`;
    if (data.relaxed && data.total)
      msg = (data.strict_total ? `${data.strict_total} אגרות עם כל המילים, ועוד אגרות עם רוב המילים` : `אין אגרת שמופיעות בה כל המילים יחד. מוצגות <b>${data.total.toLocaleString("he-IL")}</b> אגרות עם רוב המילים`) + ` · ${data.ms} מ"ש`;
    if (data.warning) msg += ` <span class="err">${esc(data.warning)}</span>`;
    st.innerHTML = msg;
    $("#try-any")?.addEventListener("click", () => { $("#match_any").checked = true; submit(); });
    $("#clear-all")?.addEventListener("click", () => { for (const f of FILTERS) $("#" + f).value = ""; submit(); });
    $("#try-sem")?.addEventListener("click", () => { form.mode.value = "semantic"; submit(); });
  }
  renderFacets(data.facets);
  $("#results").innerHTML = data.results.map((r, i) => `
    <li data-id="${r.id}" data-i="${i}" tabindex="0" role="button" aria-label="אגרת ${esc(r.letter_number_heb || "")}, כרך ${esc(r.volume_heb)}" class="${currentLetter === r.id ? "active" : ""}">
      <div class="head">
        <span class="num">אגרת ${esc(r.letter_number_heb || "")}</span>
        <span class="meta">כרך ${esc(r.volume_heb)}${r.date_raw ? " · " + esc(r.date_raw) : r.date_editorial ? " · [" + esc(r.date_editorial) + "]" : ""}${r.gregorian_date ? " · " + esc(fmtGreg(r.gregorian_date)) : ""}${r.language && r.language !== "he" ? " · " + (LANG[r.language] || r.language) : ""}</span>
      </div>
      ${r.recipient || r.recipient_note ? `<div class="rcpt">${r.recipient ? "אל: " + esc(r.recipient) : ""}${r.recipient_note ? ` <span class="rnote">(${esc(r.recipient_note)})</span>` : ""}</div>` : ""}
      <div class="snip">${r.snippet || ""}</div>
      ${r.matched_terms ? `<div class="tags">${r.matched_terms} מתוך ${r.n_terms} מילים</div>` : ""}
      ${data.mode === "hybrid" ? `<div class="tags">${r.in_text ? "✓ מילים" : ""} ${r.in_semantic ? "✓ משמעות" : ""}</div>` : ""}
    </li>`).join("");
  renderPager(data);
}

function fmtGreg(g) {
  const [y, m, d] = String(g).split("-");
  return d ? `${+d}.${+m}.${y}` : m ? `${+m}.${y}` : y;
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
  $("#reader").classList.remove("hidden");
  try { d = await api("/api/letter/" + id); } catch {
    currentLetter = null;
    $("#letter").innerHTML = `<p class="notfound">האגרת לא נמצאה (מזהה ${esc(id)}). ייתכן שהקישור ישן או שגוי.</p>`;
    updateResultNav();
    return;
  }
  currentLetter = d.id;
  const terms = state.mode === "text" ? queryTerms() : queryTerms().filter((t) => t.length > 2);
  const paras = (d.full_text || "").split(/\n\n+/).map((p) => `<p>${highlight(p, terms)}</p>`).join("");
  const notes = d.notes.length ? `<ol class="notes">${d.notes.map((n) =>
    `<li>${n.term ? `<b>${esc(n.term)}</b>: ` : ""}${esc(n.text)}</li>`).join("")}</ol>` : "";
  $("#letter").innerHTML = `
    <h2 id="letter-title" tabindex="-1">אגרת ${esc(d.letter_number_heb || "")}</h2>
    <div class="lmeta">
      <span>כרך ${esc(d.volume_heb)}</span>
      ${d.date_raw ? `<span>${esc(d.date_raw)}</span>` : ""}
      ${d.date_editorial ? `<span>[${esc(d.date_editorial)}]</span>` : ""}
      ${d.gregorian_date ? `<span>${esc(fmtGreg(d.gregorian_date))}</span>` : ""}
      ${d.language ? `<span>${LANG[d.language] || d.language}</span>` : ""}
    </div>
    ${d.recipient_note ? `<div class="recip">הנמען: ${esc(d.recipient_note)}</div>` : ""}
    ${paras}${notes}`;
  $("#source-link").href = d.url;
  $("#prev-letter").disabled = !d.prev_id;
  $("#next-letter").disabled = !d.next_id;
  $("#prev-letter").dataset.id = d.prev_id || "";
  $("#next-letter").dataset.id = d.next_id || "";
  $("#reader").scrollTop = 0;
  document.querySelectorAll(".results li").forEach((li) => li.classList.toggle("active", +li.dataset.id === d.id));
  updateResultNav();
  if (push) history.pushState(null, "", location.pathname + location.search + "#letter=" + d.id);
  document.title = `אגרת ${d.letter_number_heb} · חיפוש באגרות קודש`;
  $("#letter-title").focus({ preventScroll: true });
}

function updateResultNav() {
  const i = lastResults.findIndex((r) => r.id === currentLetter);
  $("#result-nav").style.display = lastResults.length && i >= 0 ? "" : "none";
  $("#result-pos").textContent = i >= 0 ? `${i + 1}/${lastResults.length}` : "";
  $("#prev-result").disabled = i <= 0;
  $("#next-result").disabled = i < 0 || i >= lastResults.length - 1;
}

function stepResult(delta) {
  const i = lastResults.findIndex((r) => r.id === currentLetter);
  const r = lastResults[i + delta];
  if (r) openLetter(r.id);
}

function closeLetter(push = true) {
  const was = currentLetter;
  $("#reader").classList.add("hidden");
  currentLetter = null;
  if (push) history.replaceState(null, "", location.pathname + location.search);
  document.title = (state.q ? `${state.q} · ` : "") + "חיפוש באגרות קודש";
  document.querySelector(`.results li[data-id="${was}"]`)?.focus();
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
  if (li && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openLetter(+li.dataset.id); }
});
$("#prev-result").addEventListener("click", () => stepResult(-1));
$("#next-result").addEventListener("click", () => stepResult(1));
for (const id of ["greg_from", "greg_to", "num_from", "num_to", "recipient"])
  $("#" + id).addEventListener("change", () => submit());
if (window.innerWidth < 900) $("#filters-box").open = false;
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
