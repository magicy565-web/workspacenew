---
name: buyer-magazine-browser-qa
description: Inspect the actual responsive HTML magazine in Chromium, fix layout and interaction defects, and keep visual approval separate from machine test status.
metadata:
  version: 1.1.0
---

# Browser QA Editor

Run the built artifact, not a mockup. Use `scripts/qc.py --site <site> --out <qa> --require-browser` and retain its report, chapter screenshots, desktop/mobile contact sheets and browser console/network errors.

## Machine gates

- All local product images, stylesheets, scripts and bundled fonts resolve.
- HTML structure and CSS syntax are valid; JavaScript parses and executes without uncaught errors.
- No missing images, broken local links, horizontal overflow, clipped controls, invisible text or navigation collisions at desktop and mobile widths.
- Contents navigation, next/previous controls, keyboard navigation, touch behavior and the inquiry action work. When browser clipboard permission is unavailable, the copy control must reveal a selectable manual-copy field and must never claim success or sending.
- Comparison tables are usable on mobile; all essential labels and units remain visible.
- CTA opens only the declared official destination; copy controls copy the exact inquiry text. No claim is made that an email/WhatsApp/RFQ was sent.

## Human visual review

Open the desktop and mobile contact sheets, then inspect at full size:
- cover and first two chapters (does the story hook the intended buyer?);
- the densest product/spec page;
- model matrix and commercial terms;
- the largest image gallery and the smallest gallery;
- manufacturer/compliance pages;
- CTA and source notes.

Review composition, crop, image-product identity, contrast, hierarchy, line length, rhythm, whitespace, visual repetition, actual reading order and sticky navigation overlap. Inspect all chapters, not only the cover. If an issue is found, fix the source, rebuild, rerun browser QA and inspect the new screenshots. Any HTML/CSS/image change invalidates the prior visual review.

## Status labels

- `PASS`: machine gates passed and human visual review passed for all chapters at desktop and mobile.
- `PASS_WITH_NOTES`: no blocking defect, but explicit non-blocking visual or content notes remain.
- `BLOCKED`: browser execution unavailable, missing assets, runtime errors or unresolved visual defects.
- `NOT_PUBLICATION_READY`: rights, critical facts, supplier documents, legal identity or commercial authorization remain unresolved, even if the web page itself looks excellent.

Never equate a code test count with aesthetic quality, factual verification, rights clearance, public deployment or buyer conversion.
