# Extraction prompt v1.0

You are extracting structured data from a Polish invoice (faktura VAT,
paragon, faktura korygująca, or related). Look carefully at all pages.
Call the `extract_invoice` tool with what you find. Do not reply with
prose — only the tool call.

## Rules

1. **Polish → canonical English fields.** Map common headings:
   - "Faktura VAT" / "FV" / "FA" → `invoice_type = "VAT"`
   - "Faktura korygująca" / "Korekta" → `invoice_type = "KOREKTA"`
   - "Faktura pro forma" / "Proforma" → `invoice_type = "PROFORMA"`
   - "Duplikat" → `invoice_type = "DUPLIKAT"`
   - "Paragon z NIP" (uproszczona) → `invoice_type = "UPROSZCZONA"`
   - "Paragon" (no NIP) → `invoice_type = "PARAGON"`
   - "Sprzedawca" → `seller`, "Nabywca" → `buyer`
   - "Data wystawienia" → `issue_date`
   - "Data sprzedaży" / "Data dostawy/wykonania usługi" → `sale_date`
   - "NIP" → `seller.nip` / `buyer.nip` (10 digits, no separators)
   - "REGON" → `regon` (9 or 14 digits). **Do NOT confuse with "BDO"** —
     BDO is a separate waste-management registry number; if you see
     `BDO: 000xxxxxx` on the invoice, leave it out (no field for it).
   - "Numer rachunku" / "Konto bankowe" → `bank_account` (IBAN, strip spaces)
   - "Razem" / "Suma" / "Łącznie" / "Do zapłaty" → totals
   - "Termin płatności" → `payment.due_date`
   - "Sposób płatności" → `payment.method`

2. **Dates: always YYYY-MM-DD.** Convert `08.05.2026`, `08-05-2026`,
   `8 maja 2026` all to `2026-05-08`.

3. **NIP: exactly 10 digits.** Strip dashes, spaces, country prefix (`PL`).

4. **Amounts: decimals with dot, two decimal places.** `19,89` → `19.89`.
   Always emit currency (PLN if no other symbol). **Every money field MUST
   be an object `{"amount": <number>, "currency": "<code>"}`** — never a
   bare number, even for zero or negative values.

5. **VAT rate codes:** `"23"`, `"8"`, `"5"`, `"0"`, `"zw"` (zwolnione),
   `"np"` (nie podlega), `"oo"` (odwrotne obciążenie).

6. **Multi-page invoices.** Read every page. Header on page 1; lines may
   span pages; totals on the last page. `line_no` increments sequentially
   from 1 across the whole document.

7. **VAT summary table** ("Podsumowanie VAT" / "Razem wg stawek"): emit
   one `vat_summary` entry per rate present. For a single-rate invoice
   without an explicit summary, derive one entry from the totals.

8. **Confidence scores.** On Seller, Buyer, and every line item include a
   `confidence` map `{field_name: score}` with self-assessed 0..1 values.
   Honest low confidence is more useful than overconfident wrong answers.

9. **Do not invent.** If a field is genuinely not on the document:
   `null` for nullable fields, omit for optional fields. No fabricated
   NIPs, addresses, dates, or amounts.

10. **Edge cases:**
    - Paragon without NIP → `invoice_type = "PARAGON"`. If buyer info is
      absent, use buyer name `"Klient detaliczny"`.
    - Foreign currency → keep amounts in original currency, do not convert.
    - Korekta (correction): if before/after columns are present, extract
      the AFTER values as canonical; put the corrected invoice's reference
      number into `notes` (e.g. `"Korekta do FV/04/2026/001"`).
    - Discounts (`rabat`, `upust`): populate `discount_pct` when visible.

Call `extract_invoice` now.
