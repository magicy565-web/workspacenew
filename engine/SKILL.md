---
name: buyer-magazine-studio
description: Turn a real brand or factory website into a buyer-facing, image-led editorial HTML magazine with sourced commercial facts, bespoke art direction, a safe next action, and actual browser QC.
metadata:
  version: 1.1.0+studio.2
---

# Buyer Magazine Studio 1.1

**One-sentence input:** “Turn this factory website into an English buyer magazine.” The output is a complete responsive HTML publication, not a PDF report, mockup, dashboard or generic card template.

## Workspace and agent integration

When this skill is used from the standalone Buyer Magazine Studio repository, read `../AGENTS.md` and the repository `README.md` for the MCP, HTTP and CLI interfaces. The calling agent supplies research and model execution; the local workspace stores the brief and renders it. The CHONGKER reference images and screenshots from the original delivery are intentionally omitted from the repository because their republication rights remain unverified. The repository's example is fictional and must not be used as factual research.

The optional `collect_shopify.py` helper must only run in an appropriately isolated environment. Its hostname checks do not prove that DNS resolves to a public address and should not be treated as an SSRF defense for an Internet-facing service. The local workspace itself never fetches a submitted URL.

## Three focused editorial roles

1. **Research Editor** — read `skills/buyer-magazine-research-editor/SKILL.md`. Research the official website, sitemap, product catalog where accessible, wholesale/manufacturer pages, policies and buyer-relevant evidence. Preserve source snapshots, retrieval time and gaps. Distinguish product records from variants, retail prices from wholesale quotes, supplier claims from independent evidence, and a product listing from proof of demand.
2. **Art Director + Publisher** — read `skills/buyer-magazine-editorial-director/SKILL.md`. Define a brand-specific visual language before writing pages: image treatment, typography, palette, grid, page rhythm and mobile behavior. Compose different page structures for different editorial jobs. Use real product photos unchanged; decorative imagery must never impersonate product, factory, testing or certification evidence. Generate the actual HTML/CSS/JS, not a design specification alone.
3. **Visual QA Editor** — read `skills/buyer-magazine-browser-qa/SKILL.md`. Run `scripts/qc.py` with real Chromium. Inspect every chapter on desktop and mobile, fix actual visual defects and rerun. A machine PASS is not an aesthetic or business-claim approval.

## Editorial design rules

- Do not force a fixed page count. Build only pages that serve a buyer decision; a substantial lookbook may have 12–18 chapters, while a focused product brief may need fewer.
- Avoid repetitive cards. Vary the visual grammar: full-bleed cover, typographic thesis, editorial company story, hero product, technical specification spread, model comparison, offset product duo, mixed-image gallery, assortment/merchandising proposal, customer-review signal page, maker capability page, compliance document page, commercial terms and a specific CTA.
- The first read should answer: what is the product, why might a buyer care, who might it suit, what makes it different and what is the next sensible action?
- Data must be designed as data: show units, basis, date and caveats beside the figure. Do not draw a trend chart when only a static price or a few anecdotes exist. Use a comparison table when it genuinely helps a buying decision.
- Buyer-facing copy should be concise and confident, not overloaded with internal QA jargon. Put caveats near the claims they qualify; use a short source note section for traceability.
- The mobile edition is a composed reading experience, not a shrunken desktop. Reorder images and text deliberately; keep type readable and controls touch-sized.

## Evidence and safety gates

- Never invent sales, sell-through, demand, market share, margins, SKU counts, wholesale prices, MOQ, certificates, factory identity, capacity, delivery promises, buyer testimonials or customer conversion.
- Treat `products.json` rows as product records, not SKU count. Count variants separately; disclose pagination and endpoint completeness. A blocked or partial endpoint must remain visibly incomplete.
- Public retail prices are not wholesale quotations. Supplier statements are not independent verification. “Can support testing” is not certification. “Lab verified” without a product-specific report is a claim that needs the report.
- Product photos must remain unchanged. User-supplied or publicly visible assets are not automatically cleared for republication. Track origin and rights status. Never use AI-generated or reconstructed sellable products as product evidence; never use generated factory scenes, customer scenes, tests or certificates as factual proof.
- Distinguish brand age from team manufacturing experience. Map website operator, contracting entity, invoicing entity, exporter and actual factory before describing a manufacturer as verified.
- Review content is directional unless the complete dataset and grouping logic are available. If the source says reviews are grouped across related products, say so. Do not infer defect rates or representative satisfaction from visible examples.
- Do not make medical, therapeutic, disease-outcome or safety claims beyond what a current, product-specific document supports. For companion products, do not convert customer anecdotes into clinical efficacy.
- A CTA may open an official public destination or copy an inquiry brief. Do not claim a message, WhatsApp, RFQ, email, publication or order was sent unless a real authorized tool reports success.
- Reject unsafe URL schemes, private-network URLs, credential-bearing links and local path traversal. Escape untrusted website text before inserting it into HTML.
- An interactive HTML website is not an HTML email. Email-client-safe output and sending require a separate adaptation and authorization.

## Build path

```sh
python scripts/collect_shopify.py --site https://example.com --out work/research
python scripts/aggregate_catalog.py --input work/research/products.json --out work/research/catalog-summary.json
# The Agent authors a source-tagged editorial.json and chooses a brand-specific art direction.
python scripts/build.py --brief work/editorial.json --out work/site
python scripts/qc.py --site work/site --out work/qa --require-browser
python scripts/export_portable_preview.py --site work/site --out work/preview.html
python -m unittest discover -s tests -v
```

`build.py` is a deterministic, secure baseline, not a fixed design ceiling. It supports the editorial blocks used in the CHONGKER case: `cover`, `thesis`, `editorial`, `feature`, `spec_grid`, `model_matrix`, `duo`, `gallery`, `merchandising`, `review_signals`, `line_sheet`, `maker`, `compliance`, `terms` and `closing`. An Agent may hand-author a bespoke HTML treatment when the brand needs a layout outside these blocks, then run the same safety and browser gates.

## Release evidence required

Deliver `index.html`, local assets and fonts, source/image manifest with hashes, QA report, desktop/mobile screenshots/contact sheets, test output, explicit evidence gaps and publication/rights status. Every post-QA edit invalidates the previous PASS. If browser execution or manual visual review is unavailable, mark it blocked or pending; never infer completion from a test count.

The CHONGKER example is an internal design and research exercise based on official pages observed 2 November 2026 plus user-supplied product-reference images. The sample image rights are unverified, public claims remain seller-reported, no full Shopify catalog export or purchase data was obtained, and the preview is not authorized for live publication.
