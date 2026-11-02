---
name: buyer-magazine-research-editor
description: Research a supplier website and build a traceable buyer brief that separates product facts, supplier claims, independent evidence and open commercial questions.
metadata:
  version: 1.1.0
---

# Research Editor

The output is a source-tagged editorial brief, not a general company summary. Read the official site as a buyer would: products, product variants, collections, manufacturer/wholesale pages, policies, company identity, support, reviews and downloadable documents.

## Source order

1. Official product pages and machine-readable catalog endpoints (Shopify JSON/Storefront API where public and accessible).
2. Official manufacturer, wholesale, About, shipping, returns, warranty and after-sales pages.
3. Official manuals, test reports, certificates, factory profiles and other downloadable documents.
4. Independent market and competitor sources, retailer listings, public review sources and marketplace observations.
5. Search snippets are leads only. Open the underlying page before using a claim.

## Required distinctions

- Product record ≠ variant ≠ SKU. Count variants separately; disclose pagination, filters and incomplete endpoints. Never infer a full catalog count from a product listing page.
- Public retail price ≠ wholesale quote ≠ landed cost. Record currency, market, date, variant and promotional status.
- Supplier claim ≠ independent evidence. Use labels such as `supplier-stated`, `brand-reported`, `observed-on-page`, `document-provided`, `independently-verified` and `not-provided`.
- “Can support testing” ≠ tested product. A standard name or badge ≠ product-specific report. Record report holder, manufacturer, factory, model, revision, destination market, standard, laboratory, number, date, result and validity when documents exist.
- Brand entity ≠ contracting entity ≠ invoicing entity ≠ exporter ≠ actual factory. Map these separately. Explain mismatched dates or legal names as questions, not as a resolved accusation.
- Reviews on the seller's site are seller-hosted. If reviews are grouped across related products, state it. Do not infer sell-through, return rate or product reliability from review count alone.
- A customer anecdote is not clinical evidence. Do not make therapeutic, disease, medical-device or safety claims from marketing copy or testimonials.

## Buyer evidence to collect

- Product identity: model/SKU, variant, materials, dimensions, weight, features, included accessories, packaging and manual.
- Commercial terms: wholesale price and tiers, MOQ by SKU/order type, sample price/availability, lead time and trigger, Incoterm with named place, payment milestones, freight, warranty, defect/RMA, spare parts and quote validity.
- Manufacturer identity: legal entities, actual production location, factory profile, process flow, capacity evidence, QC records, subcontracting and audit options.
- Compliance: destination market, age grading, applicable standards, exact product-specific reports/certificates, battery and transport documents.
- Market evidence: comparable products, observable public prices, channel/retailer fit, independent sales or demand signals where available. If unavailable, state that the market opportunity is not yet quantified.

## Output contract

Every material claim must map to a source ID and observation date. Every missing decision-critical fact must become a specific buyer question. The art director should receive: buyer thesis, product shortlist, strongest verified differentiators, product specifications, model comparison, price basis, supplier claims, market/competitor evidence, trade terms, compliance status, risk/unknowns, image provenance and a recommended next action.

Do not write marketing copy that hides material gaps. Do not pad the brief to fill a page plan. If evidence is thin, recommend a short research brief or mark the magazine `DRAFT`/`BLOCKED` rather than inventing content.
