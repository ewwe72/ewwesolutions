# XSD schemas

XSDs live here when needed; this directory is git-tracked but the XSDs
themselves are gitignored (large + redistributed under Ministry of
Finance terms, not ours).

## JPK_FA(4)

**Status:** XSD not bundled. The `src/pipeline/export/jpk_fa.py` builder
runs without it; structural validation is implemented in Python.

**To enable strict XSD validation:**

1. Operator: download `Schemat_JPK_FA(4)_v1-0.xsd` from
   `https://www.gov.pl/web/kas/struktury-jpk` (look for the JPK_FA(4)
   row — direct URLs on `podatki.gov.pl` keep churning).
2. Save it here as `jpk_fa_v4.xsd`.
3. The exporter will pick it up automatically next run; failed
   validations surface in the export endpoint response.

**Namespace used by the builder:** `http://crd.gov.pl/wzor/2022/03/03/11455/`
(per SPEC §7.1). If the operator-downloaded XSD has a different
`targetNamespace`, update `JPK_FA_NAMESPACE` in
`src/pipeline/export/jpk_fa.py`.

**Why not bundle it:** the ministry republishes occasionally, and we
don't want a stale schema in git overruling a newer one the operator
downloads. The README + a missing-file check in code is louder than a
silently-stale bundled copy.

## Why not KSeF FA(3)?

That's the *e-invoicing* schema (mandatory from 2026-02-01 for large
filers; small filers got pushed). It's a parallel system to JPK_FA —
JPK_FA is the *audit register*. Customers asking for KSeF integration
are V1.5+ work, not V1.0. The current export is for the JPK_FA(4)
reporting flow.
