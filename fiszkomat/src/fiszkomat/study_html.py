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
.stats .hint{display:inline-block;width:14px;height:14px;line-height:13px;
             text-align:center;border:1px solid var(--ink-muted);
             border-radius:50%;color:var(--ink-muted);font-size:9px;
             cursor:pointer;margin-left:2px;letter-spacing:0;
             font-family:Inter,system-ui,Arial,sans-serif;
             vertical-align:1px;}
.stats .hint:hover{color:var(--accent);border-color:var(--accent);}

/* ---- toolbar (list toggle, reset) ---- */
.toolbar{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
.toolbar-btn{flex:1;min-width:140px;padding:10px 14px;min-height:42px;
             background:transparent;border:1px solid var(--rule);
             font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
             font-size:11px;letter-spacing:.15em;text-transform:uppercase;
             color:var(--ink-soft);cursor:pointer;}
.toolbar-btn:hover{background:#fff;color:var(--ink);border-color:var(--ink-soft);}
.toolbar-btn.is-active{background:var(--ink);color:#fff;border-color:var(--ink);}
.toolbar-btn.danger:hover{color:var(--accent);border-color:var(--accent);}

/* ---- list view (all cards overview) ---- */
.list-view{background:var(--bg-paper);border:1px solid var(--rule);
           box-shadow:var(--shadow);}
.list-row{display:grid;grid-template-columns:28px 42px 1fr auto;gap:10px;
          padding:12px 14px;border-bottom:1px solid var(--rule);
          cursor:pointer;align-items:baseline;}
.list-row:last-child{border-bottom:none;}
.list-row:hover{background:#fff;}
.list-row:focus{outline:none;background:#fff;box-shadow:inset 3px 0 0 var(--accent);}
.list-row:focus-visible{outline:none;background:#fff;box-shadow:inset 3px 0 0 var(--accent);}
.list-row.excluded{opacity:0.45;}
.list-row.excluded:hover{opacity:0.8;}
.list-row .chk{display:flex;align-items:center;justify-content:center;
               align-self:center;}
.list-row .chk input{width:16px;height:16px;cursor:pointer;margin:0;
                     accent-color:var(--accent);}
.list-row .idx{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
               font-size:11px;color:var(--ink-muted);letter-spacing:.05em;}
.list-row .who{font-family:'Cormorant Garamond',Georgia,serif;
               font-size:17px;color:var(--ink);line-height:1.3;}
.list-row .who small{display:block;font-size:13px;color:var(--ink-soft);
                     font-style:italic;margin-top:2px;}
.list-row .row-action{display:inline-block;margin-top:6px;
                      font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
                      font-size:10px;letter-spacing:.1em;text-transform:uppercase;
                      color:var(--ink-muted);cursor:pointer;
                      background:none;border:none;padding:2px 0;}
.list-row .row-action:hover{color:var(--accent);text-decoration:underline;}
.list-row .row-action.is-split{color:var(--gold);}
.list-row .state{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
                 font-size:10px;letter-spacing:.1em;text-transform:uppercase;
                 white-space:nowrap;color:var(--ink-muted);text-align:right;}
.list-row .state.due{color:var(--gold);}
.list-row .state.mastered{color:var(--green);}
.list-row .state.fresh{color:var(--ink-soft);}

/* ---- show-all-mode banner (above card when bypassing the due filter) ---- */
.mode-banner{display:flex;flex-wrap:wrap;gap:10px;align-items:center;
             justify-content:space-between;margin-bottom:12px;padding:8px 12px;
             background:#fff;border-left:3px solid var(--gold);
             font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
             font-size:11px;letter-spacing:.08em;color:var(--ink-soft);}
.mode-banner .mb-label{text-transform:uppercase;letter-spacing:.14em;color:var(--gold);}
.mode-banner .mb-exit{background:transparent;border:1px solid var(--rule);
                      padding:6px 10px;font-family:inherit;font-size:10px;
                      letter-spacing:.12em;text-transform:uppercase;
                      color:var(--ink-soft);cursor:pointer;}
.mode-banner .mb-exit:hover{background:var(--bg-paper);color:var(--ink);
                            border-color:var(--ink-soft);}

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
           /* Mobile scroll fixes (Paulina feedback 2026-05-18): on iOS Safari
              the .flip parent's transform-style:preserve-3d interferes with
              touch-scroll on the front face (back face worked because it's
              already rotated and gets different touch handling). These three
              declarations enable momentum-scroll and isolate the touch from
              the parent's click handler. */
           -webkit-overflow-scrolling:touch;
           touch-action:pan-y;
           overscroll-behavior:contain;
           display:flex;flex-direction:column;}
.card-face.back{transform:rotateY(180deg);}
/* Desktop scroll fix (operator feedback 2026-05-18 post-restart): the
   hidden face still occupies the same screen rect after 3D projection and
   intercepts wheel/click events even though backface-visibility:hidden
   makes it invisible. Forcing pointer-events:none on the face that's
   currently rotated away routes events to the visible face — mouse wheel
   now scrolls the front when front is up, and the back when back is up.
   touch-action above already worked because touchstart/end land directly
   on the visible face on mobile, but wheel needs this explicit gate. */
.flip:not(.is-back) > .card-face.back{pointer-events:none;}
.flip.is-back > .card-face.front{pointer-events:none;}
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

/* ---- MCQ-style cards (mikrobio sample deck) ---- */
.mcq-stem{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;
          font-size:22px;line-height:1.3;color:var(--ink);
          margin-bottom:16px;}
.mcq-options{list-style:none;padding:0;margin:0;
             font-family:'Cormorant Garamond',Georgia,serif;
             font-size:17px;color:var(--ink);}
.mcq-option{display:flex;align-items:baseline;gap:8px;
            padding:6px 10px;margin:3px 0;line-height:1.4;
            border-left:2px solid transparent;}
.mcq-letter{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
            font-size:12px;color:var(--ink-muted);
            min-width:18px;text-align:right;
            letter-spacing:.05em;}
.mcq-body{flex:1;}
.mcq-back .mcq-option{font-size:15px;padding:4px 10px;margin:2px 0;
                      color:var(--ink-soft);}
.mcq-correct{font-family:'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;
             font-size:11px;letter-spacing:.18em;text-transform:uppercase;
             color:var(--green);margin-bottom:8px;}
.mcq-correct-row{background:rgba(126,168,99,0.15);
                 border-left-color:var(--green);
                 color:var(--ink);}
.mcq-correct-row .mcq-letter{color:var(--green);font-weight:600;}

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
  <span><b id="st-mastered">0</b> opanowane <span id="mastered-hint" class="hint" title="Karta jest opanowana po 3 dobrych powtórkach (przyciski 'Dobre' lub 'Łatwe').">?</span></span>
</div>

<div id="toolbar" class="toolbar" style="display:none">
  <button id="tb-view" class="toolbar-btn" type="button">Lista fiszek</button>
  <button id="tb-shuffle" class="toolbar-btn" type="button">Tasuj</button>
  <button id="tb-split-all" class="toolbar-btn" type="button" style="display:none">Rozdziel wszystkie leki</button>
  <button id="tb-reset" class="toolbar-btn danger" type="button">Resetuj postęp</button>
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

  var CARDS = [];            // raw .cards.json array (immutable post-bootstrap)
  var VCARDS = [];           // virtual cards layer (= CARDS expanded by splits)
  var STATE = { reviews: {}, edits: {}, splits: {}, excluded: {} };
  var current = null;        // vid (string) of card being shown, or null = none
  var showingBack = false;   // is the flip currently rotated to back?
  var editing = false;
  var showAll = false;       // bypass due filter
  var showList = false;      // show all-cards list view instead of single-card review
  var flipEl = null;         // reference to current .flip element
  var reviewRowEl = null;    // reference to current .review-row
  var pendingAdvance = false;// true while waiting for flip-back transition before swapping card

  // displayOrder is a permutation of [0..CARDS.length-1] giving the play
  // sequence; defaults to identity (insertion order from the .cards.json).
  // STATE.reviews is keyed by the CARDS index, NOT displayPos — shuffling
  // never touches review history. Per-session only (no persistence).
  var displayOrder = [];
  var displayPos = -1;       // current position within displayOrder
  var shuffled = false;      // is displayOrder a non-identity permutation?

  var INTERVALS = {
    again: 10 * 60 * 1000,
    hard:  4 * 60 * 60 * 1000,
    good:  24 * 60 * 60 * 1000,
    easy:  3 * 24 * 60 * 60 * 1000
  };

  // ---- storage helpers (try/catch wrapped) ----
  function loadState(){
    function defaults(){
      return { reviews: {}, edits: {}, splits: {}, excluded: {} };
    }
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return defaults();
      var p = JSON.parse(raw);
      if (!p || typeof p !== "object") return defaults();
      if (!p.reviews  || typeof p.reviews  !== "object") p.reviews  = {};
      if (!p.edits    || typeof p.edits    !== "object") p.edits    = {};
      if (!p.splits   || typeof p.splits   !== "object") p.splits   = {};
      if (!p.excluded || typeof p.excluded !== "object") p.excluded = {};
      return p;
    } catch (e) {
      return defaults();
    }
  }
  function saveState(){
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(STATE));
    } catch (e) { /* private mode / quota — ignore */ }
  }

  // ---- virtual cards layer (vid = "5" for unsplit, "5.0/5.1/..." for split) ----
  function parseDrugs(d){
    if (!d) return [];
    var parts = String(d).split(/\s*,\s*/);
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var s = parts[i].trim();
      if (s.length > 0) out.push(s);
    }
    return out;
  }

  function rebuildVCARDS(){
    VCARDS = [];
    for (var i = 0; i < CARDS.length; i++){
      var drugs = parseDrugs(CARDS[i].d || "");
      if (STATE.splits[i] && drugs.length > 1) {
        for (var s = 0; s < drugs.length; s++) {
          VCARDS.push({ vid: i + "." + s, origIdx: i, subIdx: s });
        }
      } else {
        VCARDS.push({ vid: String(i), origIdx: i, subIdx: -1 });
      }
    }
  }

  // ---- exclusion bookkeeping (propagation + orphan cleanup) ----
  function cleanupExcludedOrphans(){
    var live = {};
    for (var i = 0; i < VCARDS.length; i++) live[VCARDS[i].vid] = true;
    for (var vid in STATE.excluded){
      if (STATE.excluded.hasOwnProperty(vid) && !live[vid]) {
        delete STATE.excluded[vid];
      }
    }
  }
  function splitWithPropagation(origIdx){
    // If origVid was excluded, all new sub-vids inherit exclusion.
    if (STATE.excluded[String(origIdx)]) {
      var drugs = parseDrugs(CARDS[origIdx].d || "");
      for (var s = 0; s < drugs.length; s++) {
        STATE.excluded[origIdx + "." + s] = true;
      }
      delete STATE.excluded[String(origIdx)];
    }
    STATE.splits[origIdx] = true;
  }
  function unsplitWithPropagation(origIdx){
    // If ALL sub-vids were excluded, propagate to the rejoined origVid.
    // Mixed (some-yes-some-no) → rejoined card is included by default
    // (more permissive; user can re-uncheck if needed).
    var drugs = parseDrugs(CARDS[origIdx].d || "");
    var allExcluded = drugs.length > 0;
    for (var s = 0; s < drugs.length; s++) {
      if (!STATE.excluded[origIdx + "." + s]) { allExcluded = false; break; }
    }
    if (allExcluded) STATE.excluded[String(origIdx)] = true;
    delete STATE.splits[origIdx];
  }

  // ---- master split helpers (toolbar "Rozdziel wszystkie leki") ----
  function splittableMultiCards(){
    // origIdx of cards with >1 comma-separated drug. MCQ cards (mikrobio
    // deck) are excluded — their `d` is a question stem with natural
    // commas, not a drug list, and splitting it would be nonsense.
    var out = [];
    for (var i = 0; i < CARDS.length; i++) {
      var card = CARDS[i];
      if (Array.isArray(card.options) && card.options.length > 0) continue;
      if (parseDrugs(card.d || "").length > 1) out.push(i);
    }
    return out;
  }
  function multiSplitState(){
    // Returns "none" (no multi-drug cards exist → button hidden),
    // "split" (at least one multi-drug card not split → button = Rozdziel),
    // "unsplit" (all multi-drug cards are split → button = Połącz).
    var multi = splittableMultiCards();
    if (multi.length === 0) return "none";
    for (var k = 0; k < multi.length; k++) {
      if (!STATE.splits[multi[k]]) return "split";
    }
    return "unsplit";
  }
  function applyMasterSplit(){
    // Split every multi-drug card whose origVid is NOT excluded.
    // Skipping excluded matches user intent: "odznaczonych nie rozdzielać".
    var multi = splittableMultiCards();
    for (var k = 0; k < multi.length; k++) {
      var i = multi[k];
      if (STATE.excluded[String(i)]) continue;
      splitWithPropagation(i);   // no-op for excluded propagation since we just skipped them
    }
  }
  function applyMasterUnsplit(){
    var multi = splittableMultiCards();
    for (var k = 0; k < multi.length; k++) {
      var i = multi[k];
      if (STATE.splits[i]) unsplitWithPropagation(i);
    }
  }

  // ---- effective card view (edits overlay original; split overrides drug field) ----
  function eff(vid){
    var parts = String(vid).split(".");
    var origIdx = parseInt(parts[0], 10);
    var subIdx = parts.length > 1 ? parseInt(parts[1], 10) : -1;
    var c = CARDS[origIdx] || {};
    var e = STATE.edits[vid] || {};
    var d_value;
    if ("d" in e) {
      d_value = e.d;
    } else if (subIdx >= 0) {
      var drugs = parseDrugs(c.d || "");
      d_value = drugs[subIdx] != null ? drugs[subIdx] : (c.d || "");
    } else {
      d_value = c.d || "";
    }
    return {
      z: c.z,
      t: ("t" in e) ? e.t : (c.t || ""),
      d: d_value,
      m: ("m" in e) ? e.m : (c.m || ""),
      i: ("i" in e) ? e.i : (c.i || ""),
      c: ("c" in e) ? e.c : (c.c || ""),
      n: ("n" in e) ? e.n : (c.n || ""),
      // MCQ extension (mikrobio sample deck). Empty list = classic pharma
      // card; non-empty triggers MCQ rendering on front + back.
      options: Array.isArray(c.options) ? c.options : [],
      correct_letter: typeof c.correct_letter === "string" ? c.correct_letter : ""
    };
  }

  function ensureReview(vid){
    var key = String(vid);
    var r = STATE.reviews[key];
    if (!r) {
      r = { due: 0, count: 0, last: 0 };
      STATE.reviews[key] = r;
    }
    return r;
  }

  // ---- order management (insertion-order vs shuffled; vids over VCARDS) ----
  function initOrder(){
    displayOrder = [];
    for (var i = 0; i < VCARDS.length; i++) displayOrder.push(VCARDS[i].vid);
    displayPos = -1;
    shuffled = false;
  }
  function shuffleOrder(){
    // Fisher-Yates on displayOrder; review state untouched (keyed by vid)
    for (var i = displayOrder.length - 1; i > 0; i--){
      var j = Math.floor(Math.random() * (i + 1));
      var t = displayOrder[i];
      displayOrder[i] = displayOrder[j];
      displayOrder[j] = t;
    }
    displayPos = -1;
    shuffled = true;
  }

  // ---- selection ----
  function pickNext(){
    var now = Date.now();
    var len = displayOrder.length;
    if (len === 0) return null;
    var startPos = (displayPos < 0 ? -1 : displayPos);
    if (showAll) {
      for (var k = 1; k <= len; k++){
        var pos = (startPos + k) % len;
        var vid = displayOrder[pos];
        if (STATE.excluded[vid]) continue;
        displayPos = pos;
        return vid;
      }
      return null;
    }
    // Due mode: walk displayOrder forward, skip excluded + non-due.
    // Honors shuffle (if user clicked Tasuj) and exclusion (if user
    // unchecked rows in list view).
    for (var k = 1; k <= len; k++){
      var pos = (startPos + k) % len;
      var vid = displayOrder[pos];
      if (STATE.excluded[vid]) continue;
      var r = ensureReview(vid);
      if (r.due <= now){
        displayPos = pos;
        return vid;
      }
    }
    return null;
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
    for (var i = 0; i < VCARDS.length; i++){
      var vid = VCARDS[i].vid;
      if (STATE.excluded[vid]) continue;
      var r = ensureReview(vid);
      if (r.due > now && r.due < best) best = r.due;
    }
    if (best === Infinity) return -1;
    return best - now;
  }

  // ---- stats ----
  function refreshStats(){
    var startToday = (function(){
      var n = new Date();
      return new Date(n.getFullYear(), n.getMonth(), n.getDate(), 0, 0, 0, 0).getTime();
    })();
    var total = 0, todayCount = 0, mastered = 0;
    for (var i = 0; i < VCARDS.length; i++){
      var vid = VCARDS[i].vid;
      if (STATE.excluded[vid]) continue;       // wyłączone fiszki nie liczą się
      total++;
      var r = ensureReview(vid);
      if ((r.last || 0) >= startToday) todayCount++;
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
    var viewBtn = document.getElementById("tb-view");
    if (viewBtn) {
      viewBtn.textContent = showList ? "Wróć do powtórek" : "Lista fiszek";
      viewBtn.classList.toggle("is-active", showList);
    }
    var shuffleBtn = document.getElementById("tb-shuffle");
    if (shuffleBtn) {
      shuffleBtn.textContent = shuffled ? "Tasuj ponownie" : "Tasuj";
      shuffleBtn.classList.toggle("is-active", shuffled);
    }
    var splitAllBtn = document.getElementById("tb-split-all");
    if (splitAllBtn) {
      var st = multiSplitState();
      if (st === "none") {
        splitAllBtn.style.display = "none";
      } else {
        splitAllBtn.style.display = "";
        splitAllBtn.textContent = (st === "split")
          ? "Rozdziel wszystkie leki"
          : "Połącz wszystkie leki";
        splitAllBtn.classList.toggle("is-active", st === "unsplit");
      }
    }
    clear(root);
    flipEl = null;
    reviewRowEl = null;
    pendingAdvance = false;

    if (showList){
      renderList();
      return;
    }

    if (editing){
      renderEdit();
      return;
    }

    if (current == null){
      renderEndOfSession();
      return;
    }

    // Show-all-mode banner: discoverable way out of "Pokaż wszystkie i tak"
    // without resetting progress (Resetuj postęp wipes ALL reviews).
    if (showAll) {
      var banner = el("div", "mode-banner");
      banner.appendChild(el("span", "mb-label", "Tryb: wszystkie fiszki"));
      var exitBtn = el("button", "mb-exit", "Wróć do powtórek");
      exitBtn.type = "button";
      exitBtn.addEventListener("click", function(){
        showAll = false;
        current = pickNext();
        showingBack = false;
        render();
      });
      banner.appendChild(exitBtn);
      root.appendChild(banner);
    }

    var c = eff(current);

    // ---- flip scene ----
    var scene = el("div", "flip-scene");
    var flip = el("div", "flip");
    flip.setAttribute("role", "button");
    flip.setAttribute("tabindex", "0");
    flip.setAttribute("aria-label", "Odwróć fiszkę");

    var isMCQ = Array.isArray(c.options) && c.options.length > 0;

    // ---- front face ----
    var front = el("div", "card-face front");
    front.appendChild(el("div", "tag", "Zajęcia " + (c.z != null ? c.z : "?")));
    if (isMCQ) {
      // MCQ front: question + lettered options A-E (mikrobio deck format).
      // The reader picks an answer mentally before flipping.
      front.appendChild(el("div", "mcq-stem", c.d || "(brak pytania)"));
      var olist = el("ol", "mcq-options");
      for (var oi = 0; oi < c.options.length; oi++) {
        var li = el("li", "mcq-option");
        var letter = String.fromCharCode(97 + oi);  // 'a','b','c',...
        var letterSpan = el("span", "mcq-letter", letter + ".");
        var bodySpan = el("span", "mcq-body", c.options[oi] || "");
        li.appendChild(letterSpan);
        li.appendChild(document.createTextNode(" "));
        li.appendChild(bodySpan);
        olist.appendChild(li);
      }
      front.appendChild(olist);
    } else {
      // Classic pharma front: drugs only (the prompt). Showing the group
      // name here gives away the answer (med-student feedback 2026-05-13).
      front.appendChild(el("div", "title", c.d || "(brak leków)"));
    }
    front.appendChild(el("div", "front-spacer"));
    front.appendChild(el("div", "flip-hint", "kliknij · spacja"));

    // ---- back face ----
    var back = el("div", "card-face back");
    back.appendChild(el("div", "tag", "Zajęcia " + (c.z != null ? c.z : "?")));
    if (isMCQ) {
      // MCQ back: prominent correct-letter banner + answer headline +
      // re-listed options (correct highlighted), then the explanation sections.
      // Empty correct_letter = bank parser didn't tag a correct option;
      // operator+Paulina decision 2026-05-18: ship the question anyway,
      // reviewer flags it as "nieoznaczona" so the student knows to
      // consult Murray.
      var cl = (c.correct_letter || "").toLowerCase();
      var correctIdx = cl ? cl.charCodeAt(0) - 97 : -1;
      back.appendChild(el("div", "mcq-correct",
        cl ? ("Odpowiedź: " + cl.toUpperCase())
           : "Odpowiedź: nieoznaczona w bazie — sprawdź w źródle"));
      back.appendChild(el("div", "title", c.t || "(bez oznaczonej odpowiedzi)"));
      var blist = el("ol", "mcq-options mcq-back");
      for (var bi = 0; bi < c.options.length; bi++) {
        var bli = el("li", "mcq-option" + (bi === correctIdx ? " mcq-correct-row" : ""));
        var bletter = String.fromCharCode(97 + bi);
        bli.appendChild(el("span", "mcq-letter", bletter + "."));
        bli.appendChild(document.createTextNode(" "));
        bli.appendChild(el("span", "mcq-body", c.options[bi] || ""));
        blist.appendChild(bli);
      }
      back.appendChild(blist);
      // For MCQ cards, skip empty m/i/c sections — bank-derived cards
      // often have no curated explanation, only the answer + source citation.
      // Pharma cards still always show all sections (their layout below).
      if (c.m) appendBackSection(back, "Dlaczego",            c.m);
      if (c.i) appendBackSection(back, "Kontekst kliniczny",  c.i);
      if (c.c) appendBackSection(back, "Różnicowanie",        c.c);
      if (c.n) appendBackSection(back, "Źródło",              c.n);
    } else {
      // Classic pharma back: group title + mechanism / indications / etc.
      back.appendChild(el("div", "title", c.t || "(bez tytułu)"));
      appendBackSection(back, "Mechanizm",          c.m);
      appendBackSection(back, "Wskazania",          c.i);
      appendBackSection(back, "Przeciwwskazania",   c.c);
      if (c.n) appendBackSection(back, "Działania niepożądane", c.n);
    }

    flip.appendChild(front);
    flip.appendChild(back);
    scene.appendChild(flip);
    root.appendChild(scene);
    flipEl = flip;

    // Tap-vs-scroll discrimination (Paulina feedback 2026-05-18). On mobile
    // the user's swipe-to-scroll was being interpreted as a tap-to-flip,
    // making the front face un-scrollable when content overflowed (the back
    // face happened to work, hence the asymmetric bug report). We record
    // the touch-down position + time, then only fire toggleFlip on touch-up
    // if total Δy is small and total duration is short — a real tap.
    var touchStartY = null;
    var touchStartT = 0;
    flip.addEventListener("touchstart", function(e){
      if (!e.touches || e.touches.length !== 1) { touchStartY = null; return; }
      touchStartY = e.touches[0].clientY;
      touchStartT = Date.now();
    }, {passive: true});
    flip.addEventListener("touchend", function(e){
      if (pendingAdvance || touchStartY === null) { touchStartY = null; return; }
      var endY = (e.changedTouches && e.changedTouches[0])
                  ? e.changedTouches[0].clientY : touchStartY;
      var dy = Math.abs(endY - touchStartY);
      var dt = Date.now() - touchStartT;
      touchStartY = null;
      // Real tap = small drag + short duration. Numbers picked to feel
      // forgiving (8px slop, 250ms) without colliding with deliberate flicks.
      if (dy < 8 && dt < 250) {
        toggleFlip();
      }
    }, {passive: true});

    // Desktop / mouse path. The click event fires after touchend on most
    // mobile browsers, so we suppress it briefly if a touchend just decided
    // not to flip — but if a true mouse click happens (no touch), this fires.
    // The pendingAdvance gate also still applies.
    flip.addEventListener("click", function(e){
      if (pendingAdvance) return;
      // If this click was synthesized from a touchend, the touch handler
      // already decided. Heuristic: detect non-zero clientX from a mouse vs
      // 0 from synthetic click. Simpler: just let click also flip — duplicate
      // toggleFlip on touchend+click is benign because each toggle is its own
      // state change; the bigger risk was no-flip on scroll, which we've fixed.
      // For desktop with mouse only (no touchend), this path runs naturally.
      if (e.pointerType === "touch") return;  // pointer events (Chrome Android etc.)
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

  function renderList(){
    var view = el("div", "list-view");
    var now = Date.now();
    for (var vi = 0; vi < VCARDS.length; vi++){
      (function(vIdx){
        var vc = VCARDS[vIdx];
        var vid = vc.vid;
        var origIdx = vc.origIdx;
        var subIdx = vc.subIdx;
        var c = eff(vid);
        var r = ensureReview(vid);
        var isExcluded = !!STATE.excluded[vid];
        var row = el("div", "list-row" + (isExcluded ? " excluded" : ""));
        row.setAttribute("role", "button");
        row.setAttribute("tabindex", "0");

        // ---- checkbox: exclude this card from study session ----
        var chkCell = el("div", "chk");
        var chk = document.createElement("input");
        chk.type = "checkbox";
        chk.checked = !isExcluded;
        chk.setAttribute("aria-label", "Włącz fiszkę w sesję nauki");
        chk.addEventListener("change", function(){
          if (chk.checked) {
            delete STATE.excluded[vid];
          } else {
            STATE.excluded[vid] = true;
          }
          saveState();
          row.classList.toggle("excluded", !chk.checked);
          refreshStats();
        });
        // Prevent checkbox interactions from bubbling to row's openRow handler.
        chk.addEventListener("click", function(e){ e.stopPropagation(); });
        chk.addEventListener("keydown", function(e){
          if (e.key === " " || e.code === "Space") e.stopPropagation();
        });
        chkCell.appendChild(chk);
        chkCell.addEventListener("click", function(e){ e.stopPropagation(); });
        row.appendChild(chkCell);

        // ---- index label: #05 (unsplit) or #05.1 (split sub-card) ----
        var baseNum = origIdx + 1;
        var basePad = baseNum < 10 ? ("0" + baseNum) : String(baseNum);
        var label = subIdx >= 0 ? ("#" + basePad + "." + (subIdx + 1)) : ("#" + basePad);
        row.appendChild(el("div", "idx", label));

        // ---- content: drug list + title + optional split/unsplit action ----
        var who = el("div", "who");
        who.appendChild(document.createTextNode(c.d || "(brak leków)"));
        who.appendChild(el("small", null, c.t || ""));

        var origCard = CARDS[origIdx] || {};
        var isMCQOrig = Array.isArray(origCard.options) && origCard.options.length > 0;
        var canSplit = !isMCQOrig && parseDrugs(origCard.d || "").length > 1;
        var isSplitNow = !!STATE.splits[origIdx];
        if (canSplit) {
          var actionBtn = el("button",
            "row-action" + (isSplitNow ? " is-split" : ""),
            isSplitNow ? "Połącz leki" : "Rozdziel leki");
          actionBtn.type = "button";
          actionBtn.setAttribute("aria-label",
            isSplitNow ? "Połącz fiszki tego leku w jedną kartę"
                       : "Rozdziel kartę na pojedyncze leki");
          actionBtn.addEventListener("click", function(ev){
            ev.stopPropagation();
            if (isSplitNow) {
              unsplitWithPropagation(origIdx);
            } else {
              splitWithPropagation(origIdx);
            }
            rebuildVCARDS();
            cleanupExcludedOrphans();
            saveState();
            var wasShuffled = shuffled;
            initOrder();
            if (wasShuffled) shuffleOrder();
            // Try to keep current pointing at something sensible after the rebuild.
            if (current != null) {
              var stillThere = -1;
              for (var k = 0; k < displayOrder.length; k++) {
                if (displayOrder[k] === current) { stillThere = k; break; }
              }
              if (stillThere >= 0) {
                displayPos = stillThere;
              } else {
                // Was on a vid that no longer exists (e.g. was on "5.1" and just
                // unsplit card 5 — vid "5.1" gone). Pick fresh next.
                current = pickNext();
                showingBack = false;
              }
            }
            render();
          });
          actionBtn.addEventListener("keydown", function(e){
            if (e.key === " " || e.code === "Space") e.stopPropagation();
          });
          who.appendChild(actionBtn);
        }
        row.appendChild(who);

        // ---- state badge ----
        var stateText, stateCls;
        if ((r.count || 0) >= 3) {
          stateText = "opanowana"; stateCls = "state mastered";
        } else if (!r.last) {
          stateText = "nowa"; stateCls = "state fresh";
        } else if (r.due <= now) {
          stateText = "do powtórki"; stateCls = "state due";
        } else {
          stateText = prettyUntil(r.due - now); stateCls = "state";
        }
        row.appendChild(el("div", stateCls, stateText));
        row.setAttribute("aria-label",
          "Karta " + label + ": " + (c.d || "brak leków") +
          " — " + (c.t || "bez tytułu") + " — " + stateText +
          (isExcluded ? " — wyłączona z sesji" : ""));

        var openRow = function(){
          // Excluded cards can't be opened from the list — they'd be skipped
          // by pickNext immediately and the user would be confused. Re-enable
          // first via the checkbox.
          if (STATE.excluded[vid]) return;
          showList = false;
          current = vid;
          // Align displayPos with the chosen vid so "next card" advances
          // naturally from this position rather than jumping.
          for (var k = 0; k < displayOrder.length; k++) {
            if (displayOrder[k] === vid) { displayPos = k; break; }
          }
          showingBack = false;
          render();
        };
        row.addEventListener("click", openRow);
        row.addEventListener("keydown", function(e){
          if (e.key === "Enter" || e.key === " " || e.code === "Space"){
            e.preventDefault();
            // Stop bubble: document keydown also handles Space (flip card).
            // Without this, Space on a focused row would open AND immediately
            // flip the just-opened card to its back side.
            e.stopPropagation();
            openRow();
          }
        });
        view.appendChild(row);
      })(vi);
    }
    root.appendChild(view);
  }

  function resetProgress(){
    var msg = "Wyczyścić cały postęp powtórek? (Edycje kart zostają nietknięte.)";
    if (typeof window.confirm === "function" && !window.confirm(msg)) return;
    STATE.reviews = {};
    saveState();
    showAll = false;
    showList = false;
    current = pickNext();
    showingBack = false;
    render();
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
  // Anki-parity: 1-4 always grades. If pressed on the front, we commit the
  // grade and advance straight to the next card (no front→back→front bounce
  // — the user has already decided, so animating the answer would just delay
  // them). If pressed on the back, we play the existing back→front flip and
  // swap card on transitionend so there's no flash of the previous back.
  // Mouse-clicks on the review buttons only happen when the row isn't dimmed,
  // i.e. on the back — so the front-side path is keyboard-only in practice.
  function applyReview(action){
    if (current == null) return;
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

    pendingAdvance = true;
    if (reviewRowEl) reviewRowEl.classList.add("dim");

    // Front-side grade (keyboard 1/2/3/4 before flip): skip the flip-back
    // animation entirely — there's no back to hide.
    if (!showingBack) {
      advanceCard();
      return;
    }

    // Back-side grade: flip back to front, swap card on transitionend.
    showingBack = false;
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
    if (editing || showList) return;
    var tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (current == null) return;
    if (e.code === "Space" || e.key === " "){
      e.preventDefault();
      if (!pendingAdvance) toggleFlip();
      return;
    }
    // 1/2/3/4 always grade (Anki-parity): on back, normal flip-back-then-advance;
    // on front, applyReview detects the front-side state and advances directly
    // without a back→front animation.
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
    rebuildVCARDS();
    for (var i = 0; i < VCARDS.length; i++) ensureReview(VCARDS[i].vid);
    initOrder();
    current = pickNext();
    showingBack = false;

    var toolbar = document.getElementById("toolbar");
    var viewBtn = document.getElementById("tb-view");
    var shuffleBtn = document.getElementById("tb-shuffle");
    var splitAllBtn = document.getElementById("tb-split-all");
    var resetBtn = document.getElementById("tb-reset");
    var masteredHint = document.getElementById("mastered-hint");
    if (toolbar) toolbar.style.display = "flex";
    if (viewBtn) viewBtn.addEventListener("click", function(){
      showList = !showList;
      if (!showList && current == null) {
        current = pickNext();
        showingBack = false;
      }
      render();
    });
    if (shuffleBtn) shuffleBtn.addEventListener("click", function(){
      shuffleOrder();
      // If we're mid-session, advance to a fresh first card from the new
      // order. End-of-session view also re-renders so the stats refresh.
      if (!showList && !editing) {
        current = pickNext();
        showingBack = false;
      }
      render();
    });
    if (splitAllBtn) splitAllBtn.addEventListener("click", function(){
      var st = multiSplitState();
      if (st === "split") {
        applyMasterSplit();
      } else if (st === "unsplit") {
        applyMasterUnsplit();
      } else {
        return;  // "none" — nothing to do, button should be hidden anyway
      }
      rebuildVCARDS();
      cleanupExcludedOrphans();
      saveState();
      var wasShuffled = shuffled;
      initOrder();
      if (wasShuffled) shuffleOrder();
      // current vid likely no longer exists after a bulk op — refresh.
      current = pickNext();
      showingBack = false;
      render();
    });
    if (resetBtn) resetBtn.addEventListener("click", resetProgress);
    if (masteredHint) masteredHint.addEventListener("click", function(){
      window.alert("Karta jest opanowana po 3 dobrych powtórkach (przyciski 'Dobre' lub 'Łatwe'). Klik 'Powtórz' cofa licznik.");
    });

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
