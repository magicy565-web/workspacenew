#!/usr/bin/env python3
"""Safe, deterministic baseline renderer for a designer-authored, source-tagged editorial JSON."""
import argparse
import html
import hashlib
import ipaddress
import json
import pathlib
import re
import shutil
import sys
from urllib.parse import urlsplit

HERE = pathlib.Path(__file__).resolve().parent
STYLES = HERE.parent / 'assets' / 'editorial.css'
SCRIPT = HERE.parent / 'assets' / 'editorial.js'
FONTS = HERE.parent / 'assets' / 'fonts'
SUPPORTED = ('cover', 'thesis', 'editorial', 'feature', 'spec_grid', 'model_matrix', 'duo', 'gallery', 'merchandising', 'review_signals', 'line_sheet', 'maker', 'compliance', 'terms', 'closing')


def e(value):
    return html.escape(str(value or ''), quote=True)


def safe_url(value, *, allow_email=False):
    if not isinstance(value, str) or not value:
        raise ValueError('Empty or nonstring URL')
    p = urlsplit(value)
    if p.scheme == 'https' and p.hostname and not p.username and not p.password and p.port in (None, 443) and '\\' not in value:
        host=p.hostname.lower().rstrip('.')
        if any(ord(c) < 32 for c in value) or host == 'localhost' or host.endswith(('.local','.internal','.test','.localhost')) or '.' not in host:
            raise ValueError('Private-looking URL rejected')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('IP address URLs are not allowed')
        query = p.query.lower()
        if any(token in query for token in ('token=','access_key=','api_key=','password=','secret=')):
            raise ValueError('Credential-looking link query is forbidden')
        return e(value)
    if allow_email and p.scheme == 'mailto' and re.fullmatch(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+', p.path):
        return e(value)
    raise ValueError('Unsafe URL: expected external https://')


def local_name(filename):
    if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.\-]*\.(?:jpg|jpeg|png|webp|avif)', filename, re.I):
        raise ValueError(f'Unsafe or missing local image filename: {filename!r}')
    return filename


def verify_brief(issue):
    design = issue.get('design', {})
    if not isinstance(design, dict) or design.get('theme', 'classic') not in ('classic', 'modern', 'bold'):
        raise ValueError('Unsupported design theme')
    for req in ('brand', 'title', 'edition', 'buyer_question', 'images', 'sources', 'chapters', 'contact_url'):
        if req not in issue or not issue[req]:
            raise ValueError(f'Missing required editorial field: {req}')
    safe_url(issue['contact_url'])
    if not isinstance(issue['images'], list) or not isinstance(issue['sources'], list) or not isinstance(issue['chapters'], list):
        raise ValueError('images, sources and chapters must be lists')
    ids = set()
    for source in issue['sources']:
        if not source.get('id') or not source.get('url') or source['id'] in ids:
            raise ValueError('Each source needs unique id and URL')
        ids.add(source['id']); safe_url(source['url'])
    files = set()
    for image in issue['images']:
        name = local_name(image.get('file'))
        if name in files: raise ValueError(f'Duplicate image declaration: {name}')
        files.add(name)
        if image.get('role') not in ('product_reference', 'authorized_product_original', 'illustration'):
            raise ValueError(f'Missing safe image role for {name}')
        if not image.get('rights_status'):
            raise ValueError(f'Image permission status is required for {name}')
    for chapter in issue['chapters']:
        if chapter.get('kind') not in SUPPORTED:
            raise ValueError(f'Unsupported section kind: {chapter.get("kind")}')
        def search(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == 'image' and v not in files: raise ValueError(f'Undeclared image: {v}')
                    if k == 'source' and v and v not in ids: raise ValueError(f'Unknown citation source id: {v}')
                    search(v)
            elif isinstance(obj, list):
                for v in obj: search(v)
        search(chapter)
    return True


def photo(filename, alt, cls='', *, loading='lazy'):
    return f'<img class="{e(cls)}" src="images/{e(local_name(filename))}" alt="{e(alt)}" loading="{e(loading)}" decoding="async">'


def ref(id, class_='src-tag'):
    return f' <a class="{e(class_)}" href="#source-{e(id)}" aria-label="Source {e(id)}">[{e(id)}]</a>' if id else ''


def nav_label(chapter):
    return chapter.get('nav') or chapter.get('title') or chapter.get('eyebrow') or chapter.get('kind', 'Chapter').replace('_', ' ').title()


def render_chapter(c, i, count, issue):
    kind = c['kind']; no = f'{i+1:02d}'; brand = e(issue['brand'])
    tag = f'<span class="chapter-index">{no} <span class="dash">—</span> {e(c.get("eyebrow", "Buyer’s edit"))}</span>'
    label = f'<div class="folio">{brand} <span> / </span> {no} — {count:02d}</div>'
    title = e(c.get('title', ''))
    subtitle = e(c.get('lead', ''))
    if kind == 'cover':
        if c.get('layout') == 'full_bleed':
            content = f'''<figure class="cover-bleed-visual">{photo(c['image'], c.get('alt','Featured product photograph'), 'cover-bleed-photo', loading='eager')}</figure>
              <div class="cover-bleed-scrim" aria-hidden="true"></div>
              <div class="cover-bleed-brand">{brand} <span>/ BUYER EDIT</span></div>
              <div class="cover-bleed-copy"><span class="cover-kicker">{e(c.get('eyebrow','SOURCING NOTES'))}</span>
              <h1 id="title-{i+1}">{title}<em>{e(c.get('accent',''))}</em></h1>
              <p class="cover-lead">{subtitle}</p>
              <a class="cover-bleed-link" href="#chapter-2">Open the edit <span aria-hidden="true">↘</span></a></div>
              <div class="cover-bleed-footer"><span>{e(c.get('footer_left','PRODUCT-LED SOURCING · EVIDENCE FIRST'))}</span><span>{e(issue['edition'])}</span></div>
              <span class="image-badge">{e(c.get('image_note','Product photograph · rights pending'))}</span>'''
            return f'<section class="chapter cover cover--bleed" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
        content = f'''<div class="cover-words">
          <div class="editionline">{e(issue['edition'])} <span>•</span> Independent buyer edit</div>
          <div class="cover-block"><span class="cover-kicker">{e(c.get('eyebrow','SOURCING NOTES'))}</span>
          <h1 id="title-{i+1}">{title}<em>{e(c.get('accent',''))}</em></h1>
          <p class="cover-lead">{subtitle}</p>
          <a class="link-arrow" href="#chapter-2">Explore the edit <span aria-hidden="true">↗</span></a></div>
          <div class="cover-footer"><span>{e(c.get('footer_left', 'Product-led sourcing, with evidence.'))}</span><span>Scroll to explore&nbsp; ↓</span></div>
        </div><figure class="cover-visual">{photo(c['image'], c.get('alt','Featured product'), 'cover-photo', loading='eager')}<figcaption class="image-badge">{e(c.get('image_note','Product photograph · reference asset'))}</figcaption></figure>'''
        return f'<section class="chapter cover" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'thesis':
        reasons = ''.join(f'<article class="thesis-reason"><div class="reason-num">0{j+1}</div><h3>{e(r["title"])}</h3><p>{e(r["body"])}</p>{ref(r.get("source"))}</article>' for j,r in enumerate(c.get('reasons',[])))
        visual = photo(c['image'],c.get('alt','Selected product detail'),'thesis-photo') if c.get('image') else ''
        content = f'''<div class="sheet-narrow">{tag}<div class="thesis-head"><h2 id="title-{i+1}">{title}</h2><p class="thesis-lede">{subtitle}</p></div>
          <div class="thesis-underline"><span>THE BUYER QUESTION</span><strong>{e(issue['buyer_question'])}</strong></div>
          <div class="thesis-reasons">{reasons}</div>
        </div><div class="thesis-strip">{visual}<p>{e(c.get('aside','Our view is an editorial hypothesis; no demand or sales forecast is implied.'))}</p></div>{label}'''
        return f'<section class="chapter thesis" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'editorial':
        stats = ''.join(f'''<div class="editorial-stat"><span>{e(x.get('label',''))}</span><strong>{e(x.get('value',''))}</strong><small>{e(x.get('note',''))}{ref(x.get('source'))}</small></div>''' for x in c.get('stats',[]))
        content = f'''<div class="editorial-spread"><figure class="editorial-visual">{photo(c['image'],c.get('alt','Product reference image'),'editorial-photo')}<figcaption>{e(c.get('image_note','Product image · not facility evidence'))}</figcaption></figure>
          <div class="editorial-copy">{tag}<h2 id="title-{i+1}">{title}</h2><p class="editorial-lede">{subtitle}</p>
          <blockquote>{e(c.get('quote',''))}</blockquote><div class="editorial-stats">{stats}</div>
          <p class="editorial-note">{e(c.get('note','Brand-published statements require independent confirmation.'))}</p></div></div>{label}'''
        return f'<section class="chapter editorial" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'feature':
        specs = ''.join(f'<div class="spec-pair"><dt>{e(s["label"])}</dt><dd>{e(s["value"])}{ref(s.get("source"))}</dd></div>' for s in c.get('facts',[]))
        content = f'''<div class="feature-photo-wrap">{photo(c['image'],c.get('alt', title),'feature-photo')}<div class="feature-graphic">{e(c.get('marker', f'EDIT {no}'))}</div></div>
          <div class="feature-copy">{tag}<h2 id="title-{i+1}">{title}</h2><p class="feature-intro">{subtitle}</p>
          <div class="feature-rule"></div><p class="feature-body">{e(c.get('body',''))}</p>
          <dl class="spec-list">{specs}</dl>
          <p class="micro-note">{e(c.get('note','Product depiction is a user-supplied reference asset; sourcing terms require confirmation.'))}</p>
          </div>{label}'''
        return f'<section class="chapter feature" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'spec_grid':
        specs = ''.join(f'''<div class="spec-metric"><span>{e(x.get('label',''))}</span><strong>{e(x.get('value',''))}</strong><small>{e(x.get('note',''))}{ref(x.get('source'))}</small></div>''' for x in c.get('specs',[]))
        content = f'''<div class="spec-grid-layout"><figure class="spec-grid-visual">{photo(c['image'],c.get('alt','Product detail reference'),'spec-grid-photo')}</figure>
          <div class="spec-grid-copy">{tag}<h2 id="title-{i+1}">{title}</h2><p class="spec-grid-lede">{subtitle}</p><div class="spec-metrics">{specs}</div>
          <p class="spec-grid-note">{e(c.get('note','Seller-published specifications; verify against the sample and current manual.'))}</p></div></div>{label}'''
        return f'<section class="chapter spec-grid" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'model_matrix':
        models = c.get('models',[])
        heads = ''.join(f'''<th scope="col"><span>{e(m.get('name',''))}</span><strong>{e(m.get('price',''))}</strong><small>{e(m.get('basis','Public retail listing'))}</small></th>''' for m in models)
        rows = ''.join(f'''<tr><th scope="row">{e(r.get('feature',''))}</th>{''.join(f'<td class="{"yes" if v else "no"}">{"✓" if v else "—"}</td>' for v in r.get('values',[]))}</tr>''' for r in c.get('rows',[]))
        content = f'''<div class="matrix-wrap"><div class="matrix-head">{tag}<h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p></div>
          <div class="matrix-table-wrap" role="region" aria-label="Product model comparison" tabindex="0"><table class="model-table"><thead><tr><th scope="col">FEATURE</th>{heads}</tr></thead><tbody>{rows}</tbody></table></div>
          <div class="matrix-foot"><span>{e(c.get('note','Prices are public retail references, not wholesale quotations.'))}{ref(c.get('source'))}</span><span>{e(c.get('observed_label',''))}</span></div></div>{label}'''
        return f'<section class="chapter model-matrix" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'duo':
        cards = ''.join(f'''<figure class="duo-photo">{photo(r['image'],r.get('alt',r.get('title','Product')))}<figcaption><strong>{e(r.get('title',''))}</strong><span>{e(r.get('caption',''))}{ref(r.get('source'))}</span></figcaption></figure>''' for r in c.get('cards',[]))
        content = f'''<div class="duo-intro">{tag}<h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p></div><div class="duo-grid">{cards}</div>
          <div class="duo-bottom"><span>{e(c.get('note','Illustrative merchandising pairing; buyer-fit remains untested.'))}</span><a href="#chapter-{min(count,i+2)}">Next chapter ↘</a></div>{label}'''
        return f'<section class="chapter duo" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'gallery':
        cards=''.join(f'''<figure class="gallery-item gallery-item--{(j%5)+1}">{photo(r['image'],r.get('alt',r.get('title','Product')))}<figcaption><strong>{e(r.get('title',''))}</strong><span>{e(r.get('caption',''))}</span></figcaption></figure>''' for j,r in enumerate(c.get('items',[])))
        pair_class = ' gallery--pair' if len(c.get('items',[])) == 2 else ''
        content=f'''<div class="gallery-header">{tag}<div class="gallery-header-inner"><h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p></div></div><div class="gallery-grid{pair_class}">{cards}</div>
        <div class="gallery-note">{e(c.get('note','Representative product imagery only; exact models and availability to confirm.'))}</div>{label}'''
        return f'<section class="chapter gallery" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'merchandising':
        cards = ''.join(f'''<article class="merch-item merch-item--{j+1}"><figure>{photo(x['image'],x.get('alt',x.get('title','Product')))}<span>0{j+1}</span></figure><div class="merch-copy"><small>{e(x.get('role','ASSORTMENT ROLE'))}</small><h3>{e(x.get('title',''))}</h3><p>{e(x.get('body',''))}</p><b>{e(x.get('test',''))}</b></div></article>''' for j,x in enumerate(c.get('items',[])))
        content = f'''<div class="merch-head">{tag}<div><h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p></div></div><div class="merch-grid">{cards}</div>
          <p class="merch-caveat">{e(c.get('note','Merchandising roles are editorial hypotheses for buyer testing, not proven demand or sales forecasts.'))}</p>{label}'''
        return f'<section class="chapter merchandising" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'review_signals':
        positives = ''.join(f'<li>{e(x)}</li>' for x in c.get('positive',[]))
        questions = ''.join(f'<li>{e(x)}</li>' for x in c.get('questions',[]))
        distribution=''.join(f'''<div class="review-row"><span>{e(x.get('stars',''))}★</span><div class="review-bar"><i style="width:{max(0,min(100,int(x.get('percent',0))))}%"></i></div><strong>{e(x.get('count',''))}</strong><small>{e(x.get('percent',''))}%</small></div>''' for x in c.get('distribution',[]))
        content = f'''<div class="review-layout"><div class="review-score"><span class="chapter-index">{no} <span class="dash">—</span> {e(c.get('eyebrow','ON-PAGE CUSTOMER SIGNALS'))}</span><strong>{e(c.get('rating',''))}</strong><small>{e(c.get('rating_label','site-displayed rating'))}</small><div class="review-count"><b>{e(c.get('count',''))}</b><span>{e(c.get('count_label','reviews shown'))}</span></div><div class="review-distribution">{distribution}</div><p>{e(c.get('score_note','Brand-hosted review data; not independently audited.'))}{ref(c.get('source'))}</p></div>
          <div class="review-copy"><h2 id="title-{i+1}">{title}</h2><p class="review-lede">{subtitle}</p><div class="review-columns"><section><span>WHAT REVIEWERS MENTION</span><ul>{positives}</ul></section><section><span>WHAT A BUYER SHOULD TEST</span><ul>{questions}</ul></section></div>
          <p class="review-caveat">{e(c.get('note','A page-level sample is directional only. It cannot establish frequency, defect rate or representative customer satisfaction.'))}</p></div></div>{label}'''
        return f'<section class="chapter review-signals" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'compliance':
        standards = ''.join(f'<li>{e(x)}</li>' for x in c.get('standards',[]))
        checks = ''.join(f'<div class="compliance-check"><span>{e(x.get("label",""))}</span><strong>{e(x.get("ask",""))}</strong></div>' for x in c.get('checks',[]))
        content = f'''<div class="compliance-layout"><div class="compliance-title">{tag}<h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p><div class="standards-label">TESTING SUPPORT NAMED BY THE SUPPLIER</div><ul class="standards-list">{standards}</ul></div>
          <div class="compliance-evidence"><span class="evidence-kicker">THE DOCUMENT IS THE EVIDENCE</span><p>{e(c.get('body','A standards list is not a certificate.'))}</p>{checks}<div class="compliance-warning">{e(c.get('note','No product-specific certificate or test report was supplied in the reviewed pages.'))}{ref(c.get('source'))}</div></div></div>{label}'''
        return f'<section class="chapter compliance" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'line_sheet':
        headings = ''.join(f'<th scope="col">{e(t)}</th>' for t in c.get('columns', []))
        rows = ''.join('<tr>' + ''.join(f'<td>{e(t)}</td>' for t in row) + '</tr>' for row in c.get('rows',[]))
        content = f'''<div class="sheet-narrow sheet-light">{tag}<div class="line-title"><h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p></div>
          <div class="table-scroll" role="region" aria-label="Selected assortment line sheet" tabindex="0"><table><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table></div>
          <p class="line-caveat">{e(c.get('note','Retail references are not wholesale pricing. This is not a full inventory export.'))}{ref(c.get('source'))}</p>
          <a class="link-arrow line-link" href="{safe_url(issue['contact_url'])}" target="_blank" rel="noopener noreferrer">Request a SKU-specific line sheet <span aria-hidden="true">↗</span></a>
        </div>{label}'''
        return f'<section class="chapter line-sheet" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'maker':
        facts=''.join(f'<div class="maker-fact"><span>{e(r["label"])}</span><strong>{e(r["value"])}</strong>{ref(r.get("source"))}</div>' for r in c.get('facts',[]))
        if c.get('image'):
            visual = f'''<div class="maker-visual">{photo(c['image'], c.get('alt','Featured product detail'),'maker-photo')}<span class="maker-stamp">{e(c.get('stamp','SOURCE CHECK'))}</span></div>'''
        else:
            steps=''.join(f'''<li><span>0{j+1}</span><strong>{e(x)}</strong></li>''' for j,x in enumerate(c.get('process_steps',[])))
            visual = f'''<div class="maker-visual maker-visual--claims"><span class="maker-stamp">{e(c.get('stamp','SUPPLIER-REPORTED PROCESS'))}</span><div class="maker-claim-number">{e(c.get('visual_headline','Process'))}</div><p>{e(c.get('visual_label','Supplier-reported information'))}</p><ol class="maker-steps">{steps}</ol></div>'''
        content = f'''{visual}<div class="maker-body">{tag}<h2 id="title-{i+1}">{title}</h2><p class="maker-intro">{subtitle}</p>
          <p>{e(c.get('body',''))}</p><div class="maker-facts">{facts}</div><p class="source-warning">{e(c.get('note','Supplier claims have not been independently verified. Certifications and exact factory identity need documents.'))}</p>
        </div>{label}'''
        return f'<section class="chapter maker" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'terms':
        terms=''.join(f'''<div class="terms-row"><div class="term-name">{e(r["label"])}</div><div class="term-info"><strong>{e(r["value"])}</strong><p>{e(r.get('note',''))}{ref(r.get('source'))}</p></div><span class="term-arrow" aria-hidden="true">↗</span></div>''' for r in c.get('items',[]))
        content=f'''<div class="terms-head">{tag}<h2 id="title-{i+1}">{title}</h2><p>{subtitle}</p></div><div class="terms-list">{terms}</div>
          <p class="terms-fine">{e(c.get('note','Seller self-report is not a signed quote. Retail policies do not establish B2B conditions.'))}</p>{label}'''
        return f'<section class="chapter terms" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    if kind == 'closing':
        summary=e(c.get('body',''))
        item_text = '\\n'.join(c.get('inquiry_lines', []))
        fallback_text = e(item_text.replace('\\n','\n'))
        terms_summary = ''.join(f'''<div class="closing-term"><span>{e(x.get('label',''))}</span><strong>{e(x.get('value',''))}</strong></div>''' for x in c.get('terms_summary',[]))
        # Store plain text as a safe JSON string, not executable HTML or JS.
        content = f'''<div class="closing-visual">{photo(c['image'],c.get('alt','Selected product'),'closing-photo')}</div>
          <div class="closing-words">{tag}<h2 id="title-{i+1}">{title}</h2><p class="closing-lead">{subtitle}</p><p class="closing-summary">{summary}</p>
            <div class="closing-terms">{terms_summary}</div>
            <a class="primary-cta" href="{safe_url(issue['contact_url'])}" target="_blank" rel="noopener noreferrer">{e(issue.get('cta','See official wholesale information'))}<span aria-hidden="true">↗</span></a>
            <button type="button" class="copy-brief" data-inquiry="{e(item_text)}">Copy a buyer inquiry brief <span aria-hidden="true">⧉</span></button>
            <div class="copy-fallback" hidden><label for="inquiry-fallback-{i+1}">Copy manually — this brief has not been sent</label><textarea id="inquiry-fallback-{i+1}" readonly rows="8">{fallback_text}</textarea></div>
            <small class="closing-disclaimer">{e(c.get('note','A click opens an external official contact page; no message is sent by this magazine.'))}</small>
          </div>{label}'''
        return f'<section class="chapter closing" id="chapter-{i+1}" data-nav="{e(nav_label(c))}" aria-labelledby="title-{i+1}">{content}</section>'
    raise AssertionError(kind)


def render(issue):
    verify_brief(issue)
    theme = issue.get('design', {}).get('theme', 'classic')
    count = len(issue['chapters']) + 1
    chapters = '\n'.join(render_chapter(c,i,count,issue) for i,c in enumerate(issue['chapters']))
    sources = '\n'.join(f'<li id="source-{e(s["id"])}"><span class="source-no">{e(s["id"])}</span><div><a href="{safe_url(s["url"])}" target="_blank" rel="noopener noreferrer">{e(s["label"])}</a><p>{e(s.get("scope", "Official brand page · self-reported"))} · Observed {e(s.get("observed","date unknown"))}</p></div></li>' for s in issue['sources'])
    nav='\n'.join(f'<a href="#chapter-{i+1}" aria-label="Chapter {i+1}: {e(nav_label(c))}"><span>{i+1:02d}</span><i></i><em>{e(nav_label(c))}</em></a>' for i,c in enumerate(issue['chapters']))
    nav+=f'\n<a href="#chapter-{count}" aria-label="Sources and limitations"><span>{count:02d}</span><i></i><em>Sources</em></a>'
    sources_page=f'''<section class="chapter sources-page" id="chapter-{count}" data-nav="Evidence and limits" aria-labelledby="title-{count}"><div class="sheet-narrow">
       <span class="chapter-index">{count:02d} — EVIDENCE NOTES</span><h2 id="title-{count}">Sources, <em>not slogans.</em></h2>
       <p class="sources-preface">{e(issue.get('source_note','All citations refer to pages published by the brand unless explicitly stated otherwise. Website publication and photo-use rights are not verified. This is not a supplier audit or sales forecast.'))}</p>
       <ol class="sources-list">{sources}</ol>
       <div class="copyright">{e(issue['brand'])} / Buyer editorial working copy · {e(issue['edition'])} <span>Not an offer, authorized quotation or publication approval.</span></div>
      </div><div class="folio">{e(issue['brand'])} / {count:02d} — {count:02d}</div></section>'''
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(issue['brand'])} — {e(issue['title'])}</title><meta name="description" content="{e(issue.get('description','An evidence-bound procurement lookbook for independent retail buyers.'))}">
<link rel="stylesheet" href="editorial.css"></head><body data-theme="{e(theme)}">
<a class="skip-link" href="#chapter-1">Skip to magazine</a><div class="read-progress" id="read-progress"></div>
<header class="masthead"><a class="masthead-brand" href="#chapter-1">{e(issue['brand'])}<span> / Buyer Edit</span></a><span class="masthead-issue">{e(issue['edition'])}</span>
<div class="masthead-actions"><button class="chapter-toggle" type="button" aria-expanded="false" aria-controls="chapter-menu">Contents <b aria-hidden="true">☰</b></button><a class="masthead-cta" href="{safe_url(issue['contact_url'])}" target="_blank" rel="noopener noreferrer">Wholesale <b aria-hidden="true">↗</b></a></div></header>
<nav id="chapter-menu" class="chapter-menu" aria-label="Table of contents" hidden><p>Inside this edition</p>{nav}</nav>
<div class="side-rail" aria-hidden="true"><span>BUYER EDIT / FIELD NOTES</span><i></i><span>SCROLL TO EXPLORE</span></div>
<main id="magazine">{chapters}{sources_page}</main>
<div class="pager" role="navigation" aria-label="Chapter navigation"><button type="button" id="previous-page" aria-label="Previous chapter">←</button><output id="current-page" aria-live="polite">01 / {count:02d}</output><button type="button" id="next-page" aria-label="Next chapter">→</button></div>
<script src="editorial.js" defer></script></body></html>'''


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--brief',required=True);p.add_argument('--out',required=True);p.add_argument('--assets',help='Photo folder; defaults to directory sibling "images" next to brief')
    args=p.parse_args(argv)
    brief_path=pathlib.Path(args.brief).resolve(); issue=json.loads(brief_path.read_text(encoding='utf-8'))
    source=pathlib.Path(args.assets).resolve() if args.assets else brief_path.parent / 'images'
    dest=pathlib.Path(args.out).resolve()
    if not source.is_dir(): raise ValueError(f'Image directory missing: {source}')
    verify_brief(issue)
    for item in issue['images']:
        path=source / local_name(item['file'])
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f'Unsafe or missing image: {path}')
        if path.stat().st_size > 8*1024*1024: raise ValueError('Image exceeds 8MB local limit')
    if dest==source or dest.is_relative_to(source): raise ValueError('Output cannot overwrite input image directory')
    dest.mkdir(parents=True,exist_ok=True); (dest/'images').mkdir(exist_ok=True)
    for item in issue['images']:
        shutil.copy2(source/local_name(item['file']), dest/'images'/local_name(item['file']))
    (dest/'index.html').write_text(render(issue),encoding='utf-8')
    shutil.copy2(STYLES,dest/'editorial.css');shutil.copy2(SCRIPT,dest/'editorial.js')
    shutil.copytree(FONTS,dest/'fonts',dirs_exist_ok=True)
    photo_manifest=[]
    for img in issue['images']:
        item=dict(img)
        image_bytes=(dest/'images'/img['file']).read_bytes()
        item['sha256']=hashlib.sha256(image_bytes).hexdigest()
        item['bytes']=len(image_bytes)
        photo_manifest.append(item)
    font_manifest=[]
    for font in sorted((dest/'fonts').glob('*')):
        if font.is_file():
            font_manifest.append({'file':font.name,'bytes':font.stat().st_size,'sha256':hashlib.sha256(font.read_bytes()).hexdigest(),'license_file':font.name.endswith('LICENSE.txt')})
    (dest/'source-manifest.json').write_text(json.dumps({'brand':issue['brand'],'sources':issue['sources'],'images':photo_manifest,'fonts':font_manifest,'status':'local_editorial_preview_not_authorized_for_live_publication'},indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'built':str(dest/'index.html'),'chapters':len(issue['chapters'])+1,'images':len(issue['images']),'status':'local_preview'},ensure_ascii=False))

if __name__=='__main__': sys.exit(main())
