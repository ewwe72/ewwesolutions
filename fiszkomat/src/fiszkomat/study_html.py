"""In-browser flashcard reviewer with 3D flip card + inline editor, served at /study/{job_id}."""

STUDY_HTML: str = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>fiszkomat — nauka</title>
<style>
:root {
  --bg:#f4ede0;--bg-paper:#faf6ec;--ink:#1a1814;--ink-soft:#4a443a;--ink-muted:#8a8275;
  --rule:#c9bfa8;--accent:#7a1f1f;--accent-soft:#a83a3a;--gold:#a87b3a;--green:#3a5a3a;
  --shadow:0 1px 0 rgba(0,0,0,.04);
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--ink);}
body{font-family:Inter,system-ui,Arial,sans-serif;min-height:100vh;
     padding:24px 16px 80px;line-height:1.5;position:relative;}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:
    radial-gradient(ellipse at 12% 8%, rgba(168,123,58,.06), transparent 40%),
    radial-gradient(ellipse at 88% 92%, rgba(122,31,31,.05), transparent 45%),
    radial-gradient(ellipse at 50% 50%, rgba(0,0,0,.015), transparent 70%);}
.wrap{max-width:680px;margin:0 auto;position:relative;z-index:1;}
header{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:14px;}
.eyebrow{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
         font-size:11px;letter-spacing:.18em;text-transform:uppercase;
         color:var(--ink-muted);margin-bottom:4px;}
h1{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;
   font-size:40px;line-height:1;letter-spacing:-.02em;}
h1 em{font-style:italic;color:var(--accent);}
.wordmark-link{text-decoration:none;color:inherit;display:inline-block;transition:opacity .15s ease;}
.wordmark-link:hover{opacity:.75;}
.wordmark-link:hover h1 em{color:var(--accent-soft);}
.subtitle{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;
          font-size:15px;color:var(--ink-soft);
          border-top:1px solid var(--rule);padding-top:8px;margin-bottom:20px;}

.stats{display:flex;flex-wrap:wrap;gap:6px 14px;margin-bottom:16px;
       padding:10px 14px;background:#fff;border-left:3px solid var(--gold);
       font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
       font-size:11px;color:var(--ink-soft);letter-spacing:.05em;}
.stats b{color:var(--ink);font-weight:600;}

/* ---- flip card ---- */
.flip-scene{perspective:1600px;margin-bottom:14px;}
.flip{position:relative;width:100%;min-height:360px;
      transform-style:preserve-3d;
      transition:transform 0.45s cubic-bezier(0.4, 0, 0.2, 1);
      cursor:pointer;}
.flip.is-back{transform:rotateY(180deg);}
.card-face{position:absolute;inset:0;padding:24px;
           background:var(--bg-paper);border:1px solid var(--rule);
           box-shadow:var(--shadow);
           backface-visibility:hidden;-webkit-backface-visibility:hidden;
           overflow-y:auto;
           display:flex;flex-direction:column;}
.card-face.back{transform:rotateY(180deg);}
@media (max-width:519px){
  .flip{min-height:320px;}
}

.tag{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
     font-size:11px;letter-spacing:.22em;text-transform:uppercase;
     color:var(--ink-muted);margin-bottom:10px;}
.title{font-family:'Cormorant Garamond',Georgia,serif;font-weight:700;
       font-size:30px;line-height:1.15;letter-spacing:-.01em;
       color:var(--accent);margin-bottom:10px;}
.drugs{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;
       font-size:18px;color:var(--ink-soft);margin-bottom:18px;}

.front-spacer{flex:1;}
.flip-hint{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
           font-size:10px;letter-spacing:.22em;text-transform:uppercase;
           color:var(--ink-muted);text-align:center;margin-top:12px;
           padding-top:10px;border-top:1px dashed var(--rule);}

.back-section{margin-top:14px;padding-top:12px;border-top:1px solid var(--rule);}
.back-section:first-of-type{margin-top:6px;padding-top:0;border-top:none;}
.back-label{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
            font-size:11px;letter-spacing:.18em;text-transform:uppercase;
            color:var(--gold);margin-bottom:6px;}
.back-body{font-size:16px;color:var(--ink);line-height:1.5;
           white-space:pre-wrap;word-wrap:break-word;}

/* toolbar between card and review buttons */
.card-toolbar{display:flex;justify-content:flex-end;margin-bottom:10px;}

.btn{display:block;width:100%;padding:14px 18px;min-height:48px;
     background:var(--accent);color:#fff;border:none;cursor:pointer;
     font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
     font-size:12px;letter-spacing:.15em;text-transform:uppercase;
     margin-top:18px;}
.btn:hover{background:var(--accent-soft);}
.btn.secondary{background:transparent;color:var(--ink-muted);
               border:1px solid var(--rule);margin-top:10px;}
.btn.secondary:hover{background:#fff;color:var(--ink);}

.review-row{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;
            transition:opacity 0.2s ease;}
@media (min-width:520px){.review-row{grid-template-columns:repeat(4,1fr);}}
.review-row.dim{opacity:0.4;pointer-events:none;}
.review-btn{padding:12px 8px;min-height:56px;border:none;cursor:pointer;
            font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
            font-size:12px;letter-spacing:.12em;text-transform:uppercase;
            color:#fff;display:flex;flex-direction:column;
            align-items:center;justify-content:center;gap:4px;}
.review-btn .ivl{font-size:10px;opacity:.78;letter-spacing:.08em;
                 text-transform:none;}
.rb-again{background:var(--gold);}
.rb-again:hover{background:#8a6330;}
.rb-hard{background:var(--accent-soft);}
.rb-hard:hover{background:#8a3030;}
.rb-good{background:var(--green);}
.rb-good:hover{background:#2e482e;}
.rb-easy{background:#4c7a4c;}
.rb-easy:hover{background:#3d633d;}

.edit-link{display:inline-block;
           font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
           font-size:11px;letter-spacing:.12em;color:var(--ink-muted);
           text-decoration:underline;cursor:pointer;background:none;
           border:none;padding:6px 0;}
.edit-link:hover{color:var(--accent);}

.edit-card{background:var(--bg-paper);padding:24px;border:1px solid var(--rule);
           box-shadow:var(--shadow);}
.edit-form label{display:block;
                 font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
                 font-size:11px;letter-spacing:.18em;text-transform:uppercase;
                 color:var(--gold);margin:14px 0 6px;}
.edit-form input[type=text], .edit-form textarea{
   width:100%;padding:10px 12px;background:#fff;border:1px solid var(--rule);
   font-family:inherit;font-size:15px;color:var(--ink);line-height:1.45;
   resize:vertical;}
.edit-form textarea{min-height:78px;}
.edit-actions{display:flex;gap:8px;margin-top:16px;}
.edit-actions .btn{margin-top:0;flex:1;}

.empty{background:var(--bg-paper);padding:32px 20px;border:1px solid var(--rule);
       box-shadow:var(--shadow);text-align:center;}
.empty h2{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;
          font-size:26px;color:var(--ink);margin-bottom:8px;}
.empty p{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;
         font-size:17px;color:var(--ink-soft);}

#loading,#error{padding:40px 20px;text-align:center;
                font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
                font-size:12px;color:var(--ink-muted);letter-spacing:.1em;
                text-transform:uppercase;}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
     background:var(--gold);margin-right:8px;vertical-align:middle;
     animation:pulse 1.2s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

footer{margin-top:32px;padding-top:18px;border-top:1px solid var(--rule);}
footer .links{display:flex;flex-wrap:wrap;gap:10px 18px;margin-bottom:10px;}
footer a{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
         font-size:11px;letter-spacing:.12em;color:var(--ink-soft);
         text-decoration:none;text-transform:uppercase;}
footer a:hover{color:var(--accent);}
footer .note{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;
             font-size:14px;color:var(--ink-muted);}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">/// nauka fiszek</div>
  <a href="/" class="wordmark-link" title="Strona główna"><h1>fiszk<em>o</em>mat</h1></a>
</header>
<div class="subtitle">Powtarzaj w przeglądarce. Edytuj w locie. Anki tylko gdy chcesz.</div>

<div id="stats" class="stats" style="display:none">
  <span><b id="st-total">0</b> fiszek razem</span>
  <span><b id="st-today">0</b> dziś</span>
  <span><b id="st-mastered">0</b> opanowane</span>
</div>

<div id="root">
  <div id="loading"><span class="dot"></span>Ładowanie fiszek…</div>
</div>

<footer>
  <div class="links">
    <a id="back-link" href="/">← Wróć do generowania</a>
    <a id="deck-link" href="#">Pobierz .apkg (oryginał, bez edycji)</a>
  </div>
  <div class="note">Edycje są zapisane lokalnie w przeglądarce. .apkg zawiera oryginalne karty.</div>
</footer>
</div>

<script>
(function(){
  "use strict";

  // ---- locate job_id from /study/<hex>, or sample slug from window.__SAMPLE_SLUG ----
  // Sample mode is set up by the /study/sample/<slug> route: it inlines the
  // cards as window.__INLINE_CARDS and the slug as window.__SAMPLE_SLUG, so we
  // skip the /jobs/<id>/cards fetch and key localStorage per slug instead.
  var SAMPLE_SLUG = (typeof window.__SAMPLE_SLUG === "string") ? window.__SAMPLE_SLUG : "";
  var m = window.location.pathname.match(/\/study\/([0-9a-fA-F]+)/);
  var JOB_ID = m ? m[1] : "";
  var STORAGE_KEY = SAMPLE_SLUG
    ? ("fiszkomat-sample-" + SAMPLE_SLUG)
    : ("fiszkomat-state-" + JOB_ID);

  var root  = document.getElementById("root");
  var statsBar = document.getElementById("stats");
  var stTotal = document.getElementById("st-total");
  var stToday = document.getElementById("st-today");
  var stMastered = document.getElementById("st-mastered");
  var deckLink = document.getElementById("deck-link");
  if (SAMPLE_SLUG) {
    deckLink.setAttribute("href", "/sample/" + SAMPLE_SLUG + "/deck");
  } else if (JOB_ID) {
    deckLink.setAttribute("href", "/jobs/" + JOB_ID + "/deck");
  }

  var CARDS = [];
  var STATE = { reviews: {}, edits: {} };
  var current = -1;          // index of card being shown
  var showingBack = false;   // is the flip currently rotated to back?
  var editing = false;
  var showAll = false;       // bypass due filter
  var flipEl = null;         // reference to current .flip element
  var reviewRowEl = null;    // reference to current .review-row
  var pendingAdvance = false;// true while waiting for flip-back transition before swapping card

  var INTERVALS = {
    again: 10 * 60 * 1000,
    hard:  4 * 60 * 60 * 1000,
    good:  24 * 60 * 60 * 1000,
    easy:  3 * 24 * 60 * 60 * 1000
  };

  // ---- storage helpers (try/catch wrapped) ----
  function loadState(){
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return { reviews: {}, edits: {} };
      var p = JSON.parse(raw);
      if (!p || typeof p !== "object") return { reviews: {}, edits: {} };
      if (!p.reviews || typeof p.reviews !== "object") p.reviews = {};
      if (!p.edits   || typeof p.edits   !== "object") p.edits   = {};
      return p;
    } catch (e) {
      return { reviews: {}, edits: {} };
    }
  }
  function saveState(){
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(STATE));
    } catch (e) { /* private mode / quota — ignore */ }
  }

  // ---- effective card view (edits overlay original) ----
  function eff(idx){
    var c = CARDS[idx] || {};
    var e = STATE.edits[idx] || {};
    return {
      z: c.z,
      t: ("t" in e) ? e.t : (c.t || ""),
      d: ("d" in e) ? e.d : (c.d || ""),
      m: ("m" in e) ? e.m : (c.m || ""),
      i: ("i" in e) ? e.i : (c.i || ""),
      c: ("c" in e) ? e.c : (c.c || ""),
      n: ("n" in e) ? e.n : (c.n || "")
    };
  }

  function ensureReview(idx){
    var r = STATE.reviews[idx];
    if (!r) {
      r = { due: 0, count: 0, last: 0 };
      STATE.reviews[idx] = r;
    }
    return r;
  }

  // ---- selection ----
  function pickNext(){
    var now = Date.now();
    if (showAll) {
      var n = (current < 0 ? -1 : current);
      for (var k = 1; k <= CARDS.length; k++){
        var j = (n + k) % CARDS.length;
        return j;
      }
      return -1;
    }
    var best = -1;
    for (var i = 0; i < CARDS.length; i++){
      var r = ensureReview(i);
      if (r.due <= now){
        if (best < 0 || i < best) best = i;
      }
    }
    return best;
  }

  // ---- time-until pretty print ----
  function prettyUntil(ms){
    if (ms <= 0) return "teraz";
    var s = Math.round(ms / 1000);
    if (s < 60) return "za " + s + " s";
    var m = Math.round(s / 60);
    if (m < 60) return "za " + m + " " + plural(m, "minutę", "minuty", "minut");
    var h = Math.round(m / 60);
    if (h < 24) return "za " + h + " " + plural(h, "godzinę", "godziny", "godzin");
    // next-day rollover
    var now = new Date();
    var endToday = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999).getTime();
    var dueAt = Date.now() + ms;
    var oneDay = 24 * 60 * 60 * 1000;
    if (dueAt - endToday < oneDay) return "jutro";
    var d = Math.round(ms / oneDay);
    return "za " + d + " " + plural(d, "dzień", "dni", "dni");
  }
  function plural(n, one, few, many){
    var mod10 = n % 10, mod100 = n % 100;
    if (n === 1) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function nextDueMs(){
    var now = Date.now();
    var best = Infinity;
    for (var i = 0; i < CARDS.length; i++){
      var r = ensureReview(i);
      if (r.due > now && r.due < best) best = r.due;
    }
    if (best === Infinity) return -1;
    return best - now;
  }

  // ---- stats ----
  function refreshStats(){
    var total = CARDS.length;
    var endToday = (function(){
      var n = new Date();
      return new Date(n.getFullYear(), n.getMonth(), n.getDate(), 23, 59, 59, 999).getTime();
    })();
    var todayCount = 0, mastered = 0;
    for (var i = 0; i < CARDS.length; i++){
      var r = ensureReview(i);
      if (r.due <= endToday) todayCount++;
      if ((r.count || 0) >= 3) mastered++;
    }
    stTotal.textContent = String(total);
    stToday.textContent = String(todayCount);
    stMastered.textContent = String(mastered);
    statsBar.style.display = "flex";
  }

  // ---- DOM helpers ----
  function el(tag, cls, text){
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function clear(node){ while(node.firstChild) node.removeChild(node.firstChild); }

  // ---- rendering ----
  function render(){
    refreshStats();
    clear(root);
    flipEl = null;
    reviewRowEl = null;
    pendingAdvance = false;

    if (editing){
      renderEdit();
      return;
    }

    if (current < 0 || current >= CARDS.length){
      renderEndOfSession();
      return;
    }

    var c = eff(current);

    // ---- flip scene ----
    var scene = el("div", "flip-scene");
    var flip = el("div", "flip");
    flip.setAttribute("role", "button");
    flip.setAttribute("tabindex", "0");
    flip.setAttribute("aria-label", "Odwróć fiszkę");

    // ---- front face ----
    // Front shows ONLY the drug names (no group name). The point of the test
    // is "given this drug, what group is it?" — showing the group on the
    // front gives away the answer. Per med-student feedback 2026-05-13.
    var front = el("div", "card-face front");
    front.appendChild(el("div", "tag", "Zajęcia " + (c.z != null ? c.z : "?")));
    front.appendChild(el("div", "title", c.d || "(brak leków)"));
    front.appendChild(el("div", "front-spacer"));
    front.appendChild(el("div", "flip-hint", "kliknij · spacja"));

    // ---- back face ----
    // Back shows the group name (the answer) prominently, plus mechanism /
    // indications / contraindications.
    var back = el("div", "card-face back");
    back.appendChild(el("div", "tag", "Zajęcia " + (c.z != null ? c.z : "?")));
    back.appendChild(el("div", "title", c.t || "(bez tytułu)"));
    appendBackSection(back, "Mechanizm",          c.m);
    appendBackSection(back, "Wskazania",          c.i);
    appendBackSection(back, "Przeciwwskazania",   c.c);
    if (c.n) appendBackSection(back, "Działania niepożądane", c.n);

    flip.appendChild(front);
    flip.appendChild(back);
    scene.appendChild(flip);
    root.appendChild(scene);
    flipEl = flip;

    // click anywhere on card flips it (unless we're mid-advance)
    flip.addEventListener("click", function(){
      if (pendingAdvance) return;
      toggleFlip();
    });
    flip.addEventListener("keydown", function(e){
      if (e.key === "Enter") { e.preventDefault(); toggleFlip(); }
    });

    // ---- toolbar (edit link below card) ----
    var toolbar = el("div", "card-toolbar");
    var editLink = el("button", "edit-link", "Edytuj kartę");
    editLink.type = "button";
    editLink.addEventListener("click", function(){ editing = true; render(); });
    toolbar.appendChild(editLink);
    root.appendChild(toolbar);

    // ---- review row (dimmed when on front) ----
    var row = el("div", "review-row");
    row.appendChild(reviewButton("rb-again", "Powtórz", "10 min",  "again"));
    row.appendChild(reviewButton("rb-hard",  "Trudne",  "4 godz",  "hard"));
    row.appendChild(reviewButton("rb-good",  "Dobre",   "1 dzień", "good"));
    row.appendChild(reviewButton("rb-easy",  "Łatwe",   "3 dni",   "easy"));
    root.appendChild(row);
    reviewRowEl = row;

    // sync initial visual state (carry-over showingBack should always be false at this point,
    // because we reset to front before content swap; defensive sync anyway)
    syncFlipClasses();
  }

  function syncFlipClasses(){
    if (!flipEl || !reviewRowEl) return;
    if (showingBack) {
      flipEl.classList.add("is-back");
      reviewRowEl.classList.remove("dim");
    } else {
      flipEl.classList.remove("is-back");
      reviewRowEl.classList.add("dim");
    }
  }

  function toggleFlip(){
    showingBack = !showingBack;
    syncFlipClasses();
  }

  function appendBackSection(parent, label, body){
    var sec = el("div", "back-section");
    sec.appendChild(el("div", "back-label", label));
    sec.appendChild(el("div", "back-body", body || "—"));
    parent.appendChild(sec);
  }

  function reviewButton(cls, label, interval, action){
    var b = el("button", "review-btn " + cls);
    b.type = "button";
    b.appendChild(el("span", "lbl", label));
    b.appendChild(el("span", "ivl", interval));
    b.addEventListener("click", function(ev){
      ev.stopPropagation();
      applyReview(action);
    });
    return b;
  }

  function renderEndOfSession(){
    var box = el("div", "empty");
    var h = el("h2", null, "Wszystko zrobione na teraz.");
    box.appendChild(h);
    var nd = nextDueMs();
    var p = el("p", null, nd < 0 ? "Brak zaplanowanych powtórek." :
              ("Następna karta " + prettyUntil(nd) + "."));
    box.appendChild(p);

    var showAllBtn = el("button", "btn secondary", "Pokaż wszystkie i tak");
    showAllBtn.type = "button";
    showAllBtn.addEventListener("click", function(){
      showAll = true;
      current = pickNext();
      showingBack = false;
      render();
    });
    box.appendChild(showAllBtn);
    root.appendChild(box);
  }

  function renderEdit(){
    var c = eff(current);
    var card = el("div", "edit-card");
    var form = el("form", "edit-form");
    form.addEventListener("submit", function(e){ e.preventDefault(); saveEdit(); });

    form.appendChild(labeledInput("Tytuł", "ed-t", c.t, false));
    form.appendChild(labeledInput("Leki",  "ed-d", c.d, false));
    form.appendChild(labeledInput("Mechanizm",            "ed-m", c.m, true));
    form.appendChild(labeledInput("Wskazania",            "ed-i", c.i, true));
    form.appendChild(labeledInput("Przeciwwskazania",     "ed-c", c.c, true));
    form.appendChild(labeledInput("Działania niepożądane","ed-n", c.n || "", true));

    var actions = el("div", "edit-actions");
    var save = el("button", "btn", "Zapisz");
    save.type = "submit";
    var cancel = el("button", "btn secondary", "Anuluj");
    cancel.type = "button";
    cancel.addEventListener("click", function(){ editing = false; render(); });
    actions.appendChild(save);
    actions.appendChild(cancel);
    form.appendChild(actions);

    card.appendChild(form);
    root.appendChild(card);

    var first = document.getElementById("ed-t");
    if (first) first.focus();
  }

  function labeledInput(labelText, id, value, multiline){
    var wrap = document.createElement("div");
    var lab = el("label", null, labelText);
    lab.setAttribute("for", id);
    wrap.appendChild(lab);
    var inp = document.createElement(multiline ? "textarea" : "input");
    if (!multiline) inp.type = "text";
    inp.id = id;
    inp.value = value || "";
    if (multiline) inp.rows = 4;
    wrap.appendChild(inp);
    return wrap;
  }

  function saveEdit(){
    var t = (document.getElementById("ed-t") || {}).value || "";
    var d = (document.getElementById("ed-d") || {}).value || "";
    var mTxt = (document.getElementById("ed-m") || {}).value || "";
    var iTxt = (document.getElementById("ed-i") || {}).value || "";
    var cTxt = (document.getElementById("ed-c") || {}).value || "";
    var nTxt = (document.getElementById("ed-n") || {}).value || "";
    STATE.edits[current] = { t: t, d: d, m: mTxt, i: iTxt, c: cTxt, n: nTxt };
    saveState();
    editing = false;
    render();
  }

  // ---- review actions ----
  function applyReview(action){
    if (current < 0) return;
    if (!showingBack) return;          // only allow review when back-side visible
    if (pendingAdvance) return;        // ignore rapid double-taps
    var now = Date.now();
    var r = ensureReview(current);
    var ivl = INTERVALS[action] || INTERVALS.good;
    r.due = now + ivl;
    r.last = now;
    if (action === "good" || action === "easy") {
      r.count = (r.count || 0) + 1;
    } else if (action === "again") {
      r.count = Math.max(0, (r.count || 0) - 1);
    } // hard: leave alone
    saveState();

    // Flip back to front, then swap card on transitionend (no flash of previous back)
    pendingAdvance = true;
    showingBack = false;
    if (reviewRowEl) reviewRowEl.classList.add("dim");
    var fe = flipEl;
    if (!fe) { advanceCard(); return; }
    var handled = false;
    var onEnd = function(ev){
      if (ev && ev.propertyName && ev.propertyName !== "transform") return;
      if (handled) return;
      handled = true;
      fe.removeEventListener("transitionend", onEnd);
      advanceCard();
    };
    fe.addEventListener("transitionend", onEnd);
    fe.classList.remove("is-back");
    // safety fallback if transitionend doesn't fire (e.g. reduced motion)
    setTimeout(function(){ if (!handled) { handled = true; fe.removeEventListener("transitionend", onEnd); advanceCard(); } }, 700);
  }

  function advanceCard(){
    current = pickNext();
    showingBack = false;
    pendingAdvance = false;
    render();
  }

  // ---- keyboard ----
  document.addEventListener("keydown", function(e){
    if (editing) return;
    var tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (current < 0) return;
    if (e.code === "Space" || e.key === " "){
      e.preventDefault();
      if (!pendingAdvance) toggleFlip();
      return;
    }
    if (!showingBack) return;          // 1/2/3/4 are no-ops on front
    if (e.key === "1") { e.preventDefault(); applyReview("again"); }
    else if (e.key === "2") { e.preventDefault(); applyReview("hard"); }
    else if (e.key === "3") { e.preventDefault(); applyReview("good"); }
    else if (e.key === "4") { e.preventDefault(); applyReview("easy"); }
  });

  // ---- load ----
  function showError(msg){
    clear(root);
    var box = el("div", null);
    box.id = "error";
    box.textContent = msg;
    root.appendChild(box);
  }

  // ---- card source: window.__INLINE_CARDS (sample route) or /jobs/<id>/cards ----
  function bootstrap(cards){
    if (!Array.isArray(cards)) {
      showError("Nieprawidłowe dane fiszek.");
      return;
    }
    CARDS = cards;
    STATE = loadState();
    for (var i = 0; i < CARDS.length; i++) ensureReview(i);
    current = pickNext();
    showingBack = false;
    render();
  }

  if (Array.isArray(window.__INLINE_CARDS)) {
    bootstrap(window.__INLINE_CARDS);
    return;
  }

  if (!JOB_ID){
    showError("Brak job_id w adresie URL.");
    return;
  }

  fetch("/jobs/" + JOB_ID + "/cards")
    .then(function(r){
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    })
    .then(bootstrap)
    .catch(function(){
      showError("Nie można załadować fiszek. Sprawdź czy zadanie istnieje.");
    });
})();
</script>
</body>
</html>
"""
