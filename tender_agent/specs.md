# tender_agent — Polish Public Procurement Bidding Assistant

**Roboczo:** *Asystent Przetargowy 2.0*
**Status:** spec v0.1 — pre-build
**Owner:** solo operator
**Date authored:** 2026-05-13
**Goal of this doc:** enough context for a fresh Claude instance to pick this up cold and start building.

---

## 0. Bootstrap — for a Claude instance picking this up cold

If you're reading this fresh, read sections 0–4 before doing anything. Section 8 is the validation phase — **design-partner build with the named V1 firm** (a Polish IT public-procurement company), not a cold-email demand test. The demand question is already settled: customer #1 exists, and the partner's professional network supplies the warm-intro pipeline for customers 2–10. The validation gate is product-quality, not market-demand.

### What this project is

A subscription SaaS for Polish SMEs that bid on government tenders. Existing tools in this market (oferent.pl, pressy.pl, asystent-przetargowy.pl) are **read-only monitoring** — they alert you to new tenders. This product goes one critical step further: it **drafts the bid response, generates the required attachments, and flags eligibility blockers** using a multi-step agentic loop over the firm's profile + the tender's full TED/BZP XML.

### Why this and not consensus AI-agent ideas

This is moat-anchored. The moats:
1. **Native Polish + Polish bureaucracy fluency.** Anglo LLMs cannot draft `formuła oferty` text that reads as written by a Polish bidding specialist. They can't reliably parse SIWZ (Specyfikacja Istotnych Warunków Zamówienia) or generate compliant JEDZ entries.
2. **Already-built domain knowledge** from `invoice_idp` on Polish regulatory document handling (XML schemas, XSD validation, e-podpis flows, JPK exports).
3. **Public lead list.** Every prior tender winner's full contact details are published in the official UZP announcement. A 100% cold-email-driven recluse-friendly distribution is available immediately.

### Operator profile

Same as the playspace (see `E:\CLAUDE CODE PLAYSPACE\invoice_idp\SPEC.md` §0 for the full operator brief). Polish-speaking, Windows 11, Python 3.11, recluse with no community/industry access, async-only distribution preference.

### Required env vars (commit `.env.example`, never `.env`)

| Phase | Variables |
|---|---|
| Phase 1 (extraction prototype) | `ANTHROPIC_API_KEY`, `BIGGA` (Discord webhook — **separate** from invoice_idp and trading bots) |
| Phase 2 (data pipeline) | + `DATABASE_URL`, `UZP_API_KEY` (if/when UZP exposes one), `TED_API_KEY` (EU-level tenders) |
| Phase 3 (production) | + `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_BASIC`, `STRIPE_PRICE_ID_PRO`, `POSTMARK_API_TOKEN`, `SESSION_SECRET`, `CSRF_SECRET` |
| Phase 4 (e-signature) | + `EPODPIS_PROVIDER_API_KEY` (Szafir, Sigillum, or similar) |

---

## 1. Market

**Volume:** ~200,000 tenders/year published in Poland (BZP + TED). ~60,000 of those have at least one Polish SME bidder.

**Stack (where tenders live):**
- **BZP** (Biuletyn Zamówień Publicznych) — Polish-only, sub-EU-threshold tenders. Public, structured XML feed.
- **TED** (Tenders Electronic Daily) — EU-wide, above-threshold. Public API.
- **e-Zamówienia** (ezamowienia.gov.pl) — unified submission platform since 2021. Where bids are actually filed.

**Existing competitors:**
| Tool | What it does | Wedge against it |
|---|---|---|
| oferent.pl | Monitoring + email alerts | Read-only. No drafting. |
| pressy.pl | Monitoring + filters | Read-only. |
| asystent-przetargowy.pl | Monitoring + opportunity scoring | Read-only. |
| ezamowienia.gov.pl | Official submission platform | Government-built, slow, no bidder-side automation. |

**The wedge:** drafting. Every competitor stops at "here's a tender that matches your profile." This product produces: a first-draft opening letter, a populated JEDZ document, an eligibility-blocker checklist scored against the firm's actual KRS/REGON data, and a budget skeleton with reasonable line items.

## 2. Target customer

**V1 entry vertical (confirmed via design partner):** Polish IT consultancies bidding on government IT projects. The design-partner firm operates in exactly this segment — it serves as design partner during build, paying customer #1 at launch, and the source of warm-intro pipeline for customers 2–10.

**Primary ICP:** Polish SMEs (10–100 employees) that already bid on government contracts but find the paperwork load painful. Specifically:
- Polish IT consultancies bidding on government IT projects ← **V1 focus**
- Polish construction firms bidding on roboty budowlane ← V2 expansion
- Polish consulting firms bidding on usługi doradcze ← V2 expansion
- Polish equipment suppliers bidding on dostawy ← V2 expansion

**Buyer:** the bidding lead at the SME — usually the kierownik działu sprzedaży / dyrektor handlowy / sometimes the właściciel. Not the office accountant.

**Their current workflow (before the product):** 1–3 staff hours per tender to read SIWZ + check eligibility + draft opening letter + assemble attachments. At 5–20 tenders/month, that's 5–60 hours/month at ~80 PLN/hr loaded cost = **400–4800 PLN/month** in displaced labor.

**Why they buy:** the loaded labor displacement justifies even the 1999 PLN/mo Pro tier. The first tender they win pays for years of subscription.

## 3. What the agent actually does

Pipeline orchestrated by Claude Code SDK with sub-agents per stage:

1. **Ingest.** Subscriber configures their firm profile once: KRS number, REGON, NIP, prior tender wins (auto-pulled from UZP), preferred CPV codes (Common Procurement Vocabulary), revenue tier, headcount, certifications (ISO 9001, ISO 27001, etc.), key personnel CVs (uploaded).

2. **Monitor.** Daily cron pulls new BZP + TED publications matching the firm's CPV codes and revenue eligibility. Sub-agent ranks each by fit (`(profile match score) × (estimated win probability) / (effort to bid)`).

3. **Score eligibility.** For each tender that scores above threshold:
   - Parse SIWZ requirements (procurement specification)
   - Cross-reference each requirement against firm profile
   - Flag blockers ("requires 5 years of similar projects — firm has 3")
   - Flag near-misses ("requires ISO 27001 — firm has ISO 9001 but no 27001")

4. **Draft.** For eligible tenders the user marks "go":
   - **Opening letter** in formal Polish business register (Pan/Pani forms, proper salutation, kancelaryjny style)
   - **JEDZ document** — populated with firm data, structured to TED's XML schema
   - **Załączniki list** — auto-generates oświadczenie o braku podstaw do wykluczenia, oświadczenie o spełnieniu warunków, etc.
   - **Cena oferty** — budget skeleton based on historical winning bids for similar CPV codes
   - **Doświadczenie referencyjne** — pulls 3 most-relevant prior wins from firm's history, formats per tender's reference template

5. **Verify.** Sub-agent reviews the draft against SIWZ requirements one more time; flags any missing required content.

6. **Hand off.** User reviews in the dashboard, edits, exports as PDF/DOCX + structured XML for e-Zamówienia upload. (We do NOT auto-submit bids in V1 — too much liability.)

7. **Track.** Post-submission, monitors tender status (open → evaluation → result). If the firm wins, captures it as a reference for future bids; if it loses, captures the winning bid's price (public after award) for future pricing intel.

## 4. Why agentic > one-shot LLM

A single LLM call cannot:
- Reliably parse a 60-page SIWZ PDF (these are scanned, badly OCR'd, full of cross-references)
- Cross-check eligibility against the firm's KRS/REGON data without tool use
- Generate a JEDZ that validates against TED's XML schema (requires schema-aware iteration)
- Draft an opening letter in the formal kancelaryjny register without iteration against a Polish-business-language critic
- Match prior wins to current tender's reference requirements without semantic ranking over the firm's history

The loop is: **read SIWZ → extract requirements → check firm profile → flag blockers → if go: draft each section → verify against requirements → re-draft on mismatch → emit final**. That's 10–30 tool-use iterations per tender. One-shot fails; agentic succeeds.

## 5. Pricing & unit economics

| Tier | Price | Scope |
|---|---|---|
| Free | 0 PLN | Monitoring only, 1 firm profile, no drafting |
| Basic | **499 PLN/mo** | Drafting on up to 5 tenders/month, 1 firm profile |
| Pro | **1,499 PLN/mo** | Unlimited drafting, 3 firm profiles, e-Zamówienia upload helper |
| Agency | **4,999 PLN/mo** | 10 firm profiles, white-label drafts, priority support |

**Cost per drafted tender:**
- Anthropic API (Claude Sonnet for drafting + verification, Haiku for monitoring): ~3–8 PLN per tender
- Data fetches (KRS, REGON, UZP): free (public APIs) or ~0.10 PLN per query at scale
- **All-in COGS per Basic subscriber drafting 5 tenders/month: ~25 PLN** → **gross margin ~95%**

**Revenue ladder (in PLN MRR; convert ~0.25 USD):**
- 4 Basic subscribers = ~2k PLN MRR (~$500 USD)
- 10 Basic + 3 Pro = 9.5k PLN MRR (~$2.4k USD)
- 20 Basic + 10 Pro + 2 Agency = 34.9k PLN MRR (~$8.7k USD)

**Path to $10k USD MRR:** ~40 PLN MRR-equivalent. Achievable at ~30 Pro subscribers OR ~25 Pro + 3 Agency in 12 months if the cold-email channel converts at 1%+.

**Target CAC:** <100 PLN (cold email + Postmark transactional only). First month's subscription is the payback.

## 6. Tech stack (week 1, opinionated, minimal)

- **Agent runtime:** Claude Code SDK with sub-agents (monitor / score / draft-letter / draft-JEDZ / verify).
- **Polish-language critic sub-agent:** prompt-cache the kancelaryjny-register style guide (5–10K tokens of business-Polish patterns + bad-Polish anti-examples).
- **Data sources:** BZP feed, TED API, KRS API (Ministerstwo Sprawiedliwości), REGON API (GUS), e-Zamówienia public API, CEIDG.
- **Backend:** FastAPI (same as invoice_idp — operator already has the patterns), Postgres + Alembic, hosted on AWS `eu-central-1` for RODO + co-residency.
- **Frontend:** lean Next.js dashboard, Polish UI from day 1. Polaris-equivalent component library not needed; basic Tailwind.
- **Billing:** Stripe (PLN as primary currency, EUR fallback for cross-border).
- **Email:** Postmark for transactional. Cold-email sending via Instantly with a warmed `.pl` domain.

Skip in V1: bid auto-submission, e-podpis integration (move to Phase 4), multi-firm switching UI polish.

## 7. Distribution — five channels, V1 runs entirely on warm intros

### Channel 0: Warm-intro pipeline (design-partner network) (V1 primary, recluse-perfect)

The design-partner firm is a Polish IT public-procurement company. This unlocks:

- **Customer #1** — the partner firm itself, used as both design partner during build and first paid (or beta-discounted) customer at launch.
- **Customers 2–10** — warm intros from the partner's professional network of other Polish IT-procurement firm owners. These are *not* cold contacts; the introduction itself bypasses the trust-gap problem that normally kills V1 sales for new vendors in conservative Polish B2B.
- **Recluse-perfect:** introductions happen via the partner, not via the operator showing up at a meetup. First contact with each new prospect can still be async — email + Loom + Stripe link. No calls required.

**Channels A–D below are V2 scaling channels** for when the warm-intro pipeline saturates (likely 6–12 months in). Do not invest build time in Channels A–D until then — they're documented here only so a future operator (or future Claude session) can pick them up cold without re-deriving the distribution plan.

### Channel A: Cold email to past tender winners (V2 only)
**The killer move.** Every prior winning bidder's full name + email + NIP is in the public UZP award announcement (`ogłoszenie o udzieleniu zamówienia`). Scrape the last 24 months of awards.

- ~60k unique Polish SME emails reachable
- Email in Polish, deeply personalized: *"Zauważyłem, że Państwa firma wygrała przetarg [SIWZ name] w [date]. Na obecnie otwartych przetargach widzę 14 ogłoszeń pasujących do Państwa profilu (CPV codes [X, Y, Z]). Mogę wysłać próbny draft oferty na jeden z nich — bezpłatnie."*
- Volume: 200 personalized sends/day from a warmed `.pl` inbox = ~6k/month

### Channel B: SEO on Polish tender keywords
- `"jak napisać ofertę przetargową"`, `"JEDZ szablon"`, `"oświadczenie o spełnieniu warunków przetarg"`, `"przetarg [branża]"` — these are long-tail, low-competition, high-intent.
- 30 programmatic pages, one per major CPV category or document type.

### Channel C: Sponsored content on Polish business publications
- Bankier.pl, Money.pl, Rzeczpospolita Cyfrowa, MyCompanyPolska — sponsored articles aimed at SME owners. ~3–8k PLN per placement. Skip until Channel A validates.

### Channel D: Partner channel — Polish business associations
- Polski Związek Pracodawców Budownictwa, Polska Izba Informatyki i Telekomunikacji — they want member-benefit tools. Partnership = listed on their site, member discount. **No calls required** — partnership terms negotiable async via email. Operator never has to show up to a meetup.

## 8. Phase 0 validation — design-partner build (the partner firm)

**Demand is settled.** The design-partner firm is a Polish IT public-procurement company; the firm is paying customer #1 and design partner. The validation question is no longer "will anyone pay?" — it is "**does the agent's output actually pass a real bidding specialist's eye test?**" That gates whether to push for paid customers 2–10 via the warm-intro pipeline.

### Phase 0 objectives (folded into the 4-week build — see §9)

1. **Profile the partner firm.** KRS, REGON, NIP, certifications, key personnel, past wins (operator self-extracts from public UZP records — no need to bother the partner for this). Preferred CPV codes.
2. **Retrospective ranking test.** Pull last 6 months of tenders matching the firm's profile from BZP + TED archives. Run the monitor + ranking sub-agent on this historical window. Compare the agent's top-20 ranked list to the tenders the firm actually bid on. **Pass:** agent surfaces ≥6 of 10 of the firm's real recent bids inside its top-20 recommendations.
3. **Blind replay of 5 prior bids.** Take 5 tenders the firm has already submitted (with the partner's permission to use as test data). Have the agent produce its full draft — opening letter, JEDZ, attachments, budget skeleton. The partner (or their bidding lead) reviews each blind. **Pass:** partner rates ≥3 of 5 drafts as *"I'd start from this rather than a blank page"* or stronger.
4. **Live run on 2 currently-open tenders.** End-to-end agent draft for 2 currently-open tenders the firm intends to bid on. The partner's team uses the agent's drafts as starting points. Measure person-hours from agent-draft to final submission vs. their prior baseline. **Pass:** ≥30% reduction in person-hours.

### Pass → proceed to paid V1 with the partner firm + 2–3 warm-intro customers
### Fail → diagnose which sub-agent is the bottleneck; iterate; do NOT scale to warm-intro outreach until pass

### Why this is the right validation now

- Cold-email validation answers "is there demand?" — but the partner firm has been paying staff hours for this work for years, and so has every other firm in the segment. Demand is empirically settled.
- The real risk now is **product quality**. A draft that reads as machine-translated kills the brand instantly in Polish B2B; a draft that misses a tender requirement gets the bid rejected at evaluation. Phase 0 tests these failure modes against a real bidder before any external customer sees the product.
- Design partner as such gives 10× richer feedback than any cold-email signal would — actual workflow, real edge cases, measured time savings.

### Budget

$0–$20 USD (Anthropic API tokens only; data sources are public APIs). The 2–3 weeks of operator time is the real cost.

## 9. 4-week MVP build plan — executes Phase 0 validation against the partner firm

Each week ends with a §8 pass-criterion check. Do not advance to the next week's build until the prior week's criterion passes — quality compounds; bugs in week 1 cost 4× to fix in week 4.

| Week | Deliverable | §8 milestone |
|---|---|---|
| 1 | BZP + TED feed parser → Postgres ingestion. Firm-profile model populated with the partner firm data. KRS/REGON enrichment. Monitor + ranking sub-agent producing daily ranked tender list. Run on last 6 months historical to test ranking quality. | §8.1 + §8.2 |
| 2 | Eligibility-blocker scoring sub-agent + drafting pipeline scaffolded. Replay first 2 of 5 prior bids end-to-end; partner reviews blind. | §8.3 (2 of 5) |
| 3 | Drafting sub-agents complete — opening letter (Polish kancelaryjny critic loop), JEDZ XML generation, attachment auto-population. Replay remaining 3 prior bids. | §8.3 (3 of 5) |
| 4 | First 2 currently-open tenders run end-to-end. The partner's team uses drafts as starting point; measure time-to-submission reduction. Stripe billing wired only after Phase 0 pass. First warm-intro prospect outreach via partner at week 4 close. | §8.4 |

## 10. Top 3 risks and kill conditions

1. **Quality bar is brutal for formal Polish business correspondence.** A draft that reads as machine-translated kills the brand instantly in Polish B2B. Mitigation: kancelaryjny-register critic sub-agent + operator hand-review of every draft for first 50 paying customers. **Kill if:** operator review time exceeds 45 minutes per draft after customer #50.

2. **Public-sector procurement rules change frequently.** The Polish tender system was overhauled in 2021 (new PZP law), partially restructured in 2023. Schema/process changes mid-build are real. Mitigation: keep the agent's rule library version-tagged, monitor UZP newsletter, allocate 4 hours/month for schema maintenance. **Kill if:** UZP announces another full restructure with <12-month transition window in V1.

3. **Trust gap for new vendor in conservative Polish B2B — V2 risk only.** Bidding firms are wary of letting a new software vendor touch their tender process. **V1 sidesteps this entirely** — the partner firm + warm intros from his network bypass the cold-trust problem. The risk reappears in V2 when scaling beyond warm intros via Channels A–D. Mitigation at V2: lead with the partner firm as a publicly-citable case study (subject to his consent); free first-tender drafting for cold prospects; clear refund. **Kill V2 expansion if:** after 90 days of cold-outreach effort and 50 cold-email-validated leads, conversion-to-paid is <2%. **This kill criterion does not apply to V1** — V1 success runs on the warm-intro pipeline only.

## 11. Definition of success (90-day)

Adjusted upward vs. a cold-email projection — warm-intro distribution makes the customer count more aggressive than it would be on a pure-outbound basis.

- Partner's firm as paying customer #1 (Basic or Pro tier)
- 3–5 additional paying customers via the partner's professional network warm intros (= 4–6 paying total)
- At a realistic Basic/Pro mix: **~4–8k PLN MRR (~$1k–2k USD)** — comfortably past the $1k MRR / 3-month bar
- 100+ tenders drafted across all customers combined
- Customer-reported win rate uplift documented for at least 3 customers (anecdotal is fine for V1)
- Operator time per draft trending to <30 minutes by draft #50
- 1+ publicly-citable case study (the partner firm if he consents; otherwise the highest-volume referred customer)

**If hit:** the warm-intro pipeline is the moat — keep mining it for V1 scale. Begin V2 channel buildout (cold email, SEO, partner associations) only when warm-intro lead flow drops below ~1 new qualified prospect/week.

**If missed:** the warm-intro pipeline didn't deliver as expected. Diagnose: the partner's professional network smaller than estimated? A product-quality issue blocking referrals? Reputational risk concern preventing introductions? Pivot the offer, adjust the introduction script, or kill.

## 12. Open questions for operator

These need decisions before building, not guesses by Claude:

1. **Hosting region** — AWS `eu-central-1` (Frankfurt, same as invoice_idp) or Polish data center (e.g., Polcom, Atman) for stronger RODO + government-sector trust signaling?
2. **Currency** — PLN-only billing in V1 (simpler) or PLN + EUR (for cross-border bidders)? Probably PLN-only V1.
3. **VAT** — operator will need to register as a Polish VAT taxpayer once revenue crosses 200k PLN/year. Plan for it; don't structure under JDG indefinitely.
4. **Bid auto-submission** — V1 explicitly says NO (liability). When does V2 introduce it, and what does the legal review look like?
5. **Polish-business critic sub-agent training data** — where do we source 100–500 examples of "good" vs "bad" Polish business letters? Possible sources: UZP archive of submitted bids (FOIA?), Polish legal text corpora, hand-curated by operator.
6. **Partner's firm — pricing for V1.** Free (because design partner), discounted (skin in the game), or full price (most honest)? Recommendation: 50% discount for first 6 months in exchange for case-study rights + 3 named referrals; full price thereafter.
7. **Partner's consent for public case study.** Will partner agree to be a publicly-citable customer (firm name, anonymized financials)? If yes, the single most valuable social-proof asset for warm-intro pipeline expansion. If no, structure the case study around a referred customer instead.
8. **Conflict-of-interest disclosure.** When prospects ask "have you used this on real tenders?" the answer must transparently include the partner connection. Bake the disclosure norm into the sales template now, not after the first awkward question.

## 13. Files referenced

- This spec: `E:\CLAUDE CODE PLAYSPACE\tender_agent\specs.md`
- Sibling project (shared patterns): `E:\CLAUDE CODE PLAYSPACE\invoice_idp\SPEC.md`
- Playspace README: `E:\CLAUDE CODE PLAYSPACE\README.md`
- Memory pointer to playspace: `C:\Users\Abdul\.claude\projects\E--CLAUDE-CODE-PLAYSPACE\memory\MEMORY.md`
