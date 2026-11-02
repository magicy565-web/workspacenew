#!/usr/bin/env python3
"""Structural + real Chromium browser QA for Buyer Magazine Studio."""
import argparse
import base64
import mimetypes
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import subprocess
import threading
from urllib.parse import unquote, urlsplit


class AuditHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.ids=set(); self.images=[]; self.links=[]; self.anchors=[]; self.headings=[]; self.scripts=[]; self.styles=[]
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'): self.ids.add(attrs['id'])
        if tag == 'img': self.images.append(attrs)
        if tag == 'a': self.links.append(attrs.get('href', '')); self.anchors.append(attrs)
        if tag in ('h1','h2','h3'): self.headings.append(tag)
        if tag == 'script' and attrs.get('src'): self.scripts.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet' and attrs.get('href'): self.styles.append(attrs['href'])


def local_reference(site, path):
    url = urlsplit(path)
    if path.startswith('//') or url.scheme or url.netloc or url.path.startswith('/'):
        return None
    dest = (site / unquote(url.path)).resolve()
    if not dest.is_relative_to(site.resolve()):
        raise ValueError(f'Path traversal in local ref: {path}')
    return dest


def structural_checks(site):
    root=site/'index.html'; problems=[]
    if not root.is_file(): return ['index.html absent'], {}, None
    parser=AuditHTML(); parser.feed(root.read_text(encoding='utf-8'))
    if parser.headings.count('h1') != 1: problems.append('Expected exactly one h1 in rendered magazine')
    if len(parser.headings)<3: problems.append('Missing narrative headings')
    if not parser.images: problems.append('No images')
    if not any('primary-cta' in v.get('class','').split() and v.get('href','').startswith('https://') for v in parser.anchors): problems.append('No HTTPS primary CTA found')
    for i in parser.images:
        if not i.get('alt'): problems.append('Image without meaningful alt: '+str(i.get('src')))
    for value in (i.get('src','') for i in parser.images):
        try:
            path=local_reference(site,value)
            if not path or not path.is_file(): problems.append(f'Missing or external-only image: {value}')
        except ValueError as error: problems.append(str(error))
    for value in parser.styles+parser.scripts:
        try:
            path=local_reference(site,value)
            if not path or not path.is_file(): problems.append(f'Missing local script/stylesheet: {value}')
        except ValueError as error: problems.append(str(error))
    for value in parser.links:
        if value.startswith('#'):
            if len(value)>1 and value[1:] not in parser.ids: problems.append(f'Unresolved local anchor: {value}')
        elif value:
            u=urlsplit(value)
            if u.scheme not in ('https','mailto'):
                problems.append(f'Unsafe/missing scheme on anchor: {value}')
    for css in parser.styles:
        path=local_reference(site,css)
        if path and path.is_file():
            source=path.read_text(encoding='utf-8')
            if source.count('{')!=source.count('}'):
                problems.append(f'Unbalanced CSS curly braces in {css}')
            for font_ref in re.findall(r'url\(["\']?([^"\')]+)',source):
                if font_ref.startswith(('data:','https://')): continue
                try:
                    font_path=local_reference(site,font_ref)
                    if not font_path or not font_path.is_file(): problems.append(f'Missing local font asset: {font_ref}')
                except ValueError as error: problems.append(str(error))
    for js in parser.scripts:
        path=local_reference(site,js)
        if path and path.is_file():
            try:
                r=subprocess.run(['node','--check',str(path)], capture_output=True,text=True,timeout=10)
                if r.returncode: problems.append(f'JavaScript syntax error in {js}: {r.stderr[:350]}')
            except (OSError,subprocess.TimeoutExpired) as error:
                problems.append(f'JavaScript syntax check unavailable: {error}')
    return problems,{'images':len(parser.images),'headings':len(parser.headings),'chapters':len(re.findall(r'class="chapter ',root.read_text(encoding='utf-8'))),'external_sources':len([v for v in parser.links if v.startswith('https://')])},parser


@contextmanager
def web_server(root):
    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(root),**kwargs)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
    th=threading.Thread(target=server.serve_forever,daemon=True);th.start()
    try: yield f'http://127.0.0.1:{server.server_address[1]}/index.html'
    finally: server.shutdown();server.server_close()


def inline_site_for_restricted_browser(site):
    """Render the *same* site HTML/CSS/JS/photos without file:// or loopback navigation.

    Some managed Chromium environments block all URL navigation; page.set_content is
    still capable of executing the real page scripts and laying out the actual assets.
    This mode tests layout/interactions but does not claim network delivery was tested.
    """
    page_html = (site / 'index.html').read_text(encoding='utf-8')
    css = (site / 'editorial.css').read_text(encoding='utf-8')
    js = (site / 'editorial.js').read_text(encoding='utf-8')
    if '</style' in css.lower() or '</script' in js.lower():
        raise ValueError('Unsafe closing element in audited local stylesheet/script')
    css_tag = '<link rel="stylesheet" href="editorial.css">'
    js_tag = '<script src="editorial.js" defer></script>'
    if page_html.count(css_tag) != 1 or page_html.count(js_tag) != 1:
        raise ValueError('Unexpected stylesheet or script tags in fallback document')
    def font_ref(match):
        filename=match.group(1)
        path=local_reference(site,filename)
        if path is None or not path.is_file(): raise ValueError('Unresolved local font: '+filename)
        suffix=path.suffix.lower()
        mime={'.woff':'font/woff','.woff2':'font/woff2','.otf':'font/otf','.ttf':'font/ttf'}.get(suffix)
        if not mime: raise ValueError('Unsupported local font format: '+suffix)
        return 'url("data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode('ascii')+'")'
    css = re.sub(r'url\(["\']?(fonts/[A-Za-z0-9_.\-]+)["\']?\)',font_ref,css)
    page_html = page_html.replace(css_tag, '<style>'+css+'</style>')
    page_html = page_html.replace(js_tag, '<script>'+js+'</script>')
    def image_ref(match):
        filename = match.group(1)
        path = local_reference(site, 'images/' + filename)
        if path is None or not path.is_file(): raise ValueError('Unresolved local photo: ' + filename)
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        if mime not in ('image/jpeg','image/png','image/webp','image/avif'):
            raise ValueError('Unsupported local image encoding')
        return 'src="data:'+mime+';base64,'+base64.b64encode(path.read_bytes()).decode('ascii')+'"'
    page_html = re.sub(r'src="images/([A-Za-z0-9][A-Za-z0-9_.\-]*\.(?:jpe?g|png|webp|avif))"',image_ref,page_html,flags=re.I)
    if 'src="images/' in page_html:
        raise ValueError('Unresolved local image during in-memory browser render')
    return page_html


def browser_check(site, out, *, require_browser):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if require_browser: raise RuntimeError('Playwright not installed, cannot truthfully pass browser QC')
        return {'status':'not_run','reason':'Playwright unavailable'},[]
    problems=[]; results={}
    # Chromium in sandboxes can reject file:// navigation; use an ephemeral loopback HTTP server.
    with web_server(site) as url, sync_playwright() as p:
        browser_args=['--no-sandbox','--disable-dev-shm-usage','--no-proxy-server']
        render_mode = 'local_http'
        try:
            import os
            executable=os.environ.get('CHROMIUM_PATH')
            if not executable and Path('/usr/bin/chromium').exists(): executable='/usr/bin/chromium'
            browser = p.chromium.launch(headless=True,executable_path=executable,args=browser_args)
        except Exception as ex:
            if require_browser: raise RuntimeError(f'Failed to launch actual Chromium: {str(ex)[:400]}')
            return {'status':'not_run','reason':str(ex)[:300]},[]
        try:
            for screen,width,height in [('desktop',1440,900),('mobile',390,844)]:
                page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1,has_touch=screen=='mobile',is_mobile=screen=='mobile')
                errors=[]
                page.on('pageerror',lambda ex: errors.append(str(ex)))
                page.on('console',lambda m: errors.append('console: '+m.text) if m.type=='error' else None)
                if render_mode == 'embedded_in_memory':
                    page.set_content(inline_site_for_restricted_browser(site),wait_until='load',timeout=30000)
                else:
                    try:
                        page.goto(url,wait_until='load',timeout=30000)
                    except Exception as exc:
                        if 'ERR_BLOCKED_BY_ADMINISTRATOR' not in str(exc): raise
                        render_mode='embedded_in_memory'
                        page.set_content(inline_site_for_restricted_browser(site),wait_until='load',timeout=30000)
                # Eagerly decode lazy images so offscreen media is not falsely reported broken.
                page.evaluate("() => { document.documentElement.style.scrollBehavior='auto'; document.querySelectorAll('img').forEach(img => img.loading='eager'); }")
                try: page.wait_for_function('Array.from(document.images).every(i => i.complete)',timeout=12000)
                except Exception: pass
                page.wait_for_timeout(350)
                info=page.evaluate('''() => {
                  const chapters=[...document.querySelectorAll('main .chapter')];
                  const images=[...document.images];
                  const broken=images.filter(x=>!x.complete||x.naturalWidth<=0).map(x=>x.alt+' ['+(x.getAttribute('src')||'').slice(0,90)+']');
                  const tooSmall=[...document.querySelectorAll('.primary-cta,.masthead-actions button,.pager button,.copy-brief')].filter(e=>{const r=e.getBoundingClientRect();return r.width<40||r.height<40;}).map(e=>e.className||e.id);
                  const overflow=Math.max(0,document.documentElement.scrollWidth-window.innerWidth);
                  return {title:document.title,chapters:chapters.length,images:images.length,broken_images:broken,overflow_px:overflow,
                  too_small_tap_targets:tooSmall,has_cta:!!document.querySelector('.primary-cta[href^="https://"]'),nav_visible:!!document.querySelector('#chapter-menu'),
                  body_min_width:parseFloat(getComputedStyle(document.body).minWidth)||0};}''')
                page.screenshot(path=str(out/f'{screen}_cover.png'),full_page=False)
                # Section overview: viewport at each chapter top, not just DOM lint.
                for j in range(info['chapters']):
                    if j>=18: break
                    page.evaluate('(j)=>{const s=document.querySelectorAll("main .chapter")[j];s.scrollIntoView({behavior:"instant",block:"start"});}',j)
                    page.wait_for_timeout(220)
                    page.screenshot(path=str(out/f'{screen}_{j+1:02d}.png'),full_page=False,animations='disabled')
                page.evaluate('window.scrollTo({top:0,behavior:"instant"})')
                page.wait_for_timeout(350)
                page.screenshot(path=str(out/f'{screen}_full.png'),full_page=True,animations='disabled')
                # Actual UI interactions. External target is inspected, not visited.
                page.locator('.chapter-toggle').click()
                info['menu_opens']=page.locator('.chapter-menu').is_visible()
                page.locator('.chapter-toggle').click()
                info['menu_closes']=not page.locator('.chapter-menu').is_visible()
                page.locator('#next-page').click()
                page.wait_for_timeout(800)
                info['next_button_updates_counter']=page.locator('#current-page').inner_text().startswith('02')
                # Clipboard is a user-gesture behavior and should never claim to send.
                page.locator('.copy-brief').first.scroll_into_view_if_needed()
                page.locator('.copy-brief').first.click()
                page.wait_for_timeout(160)
                info['copy_feedback']=page.locator('.copy-brief').first.inner_text()
                info['copy_fallback_visible']=page.locator('.copy-fallback').first.is_visible()
                info['copy_fallback_has_text']=bool(page.locator('.copy-fallback textarea').first.input_value())
                if info['overflow_px']>2: problems.append(f'{screen}: horizontal overflow {info["overflow_px"]} px')
                if info['broken_images']: problems.append(f'{screen}: broken images {info["broken_images"]}')
                if info['too_small_tap_targets']: problems.append(f'{screen}: too small click targets: {info["too_small_tap_targets"]}')
                if not info['has_cta']: problems.append(f'{screen}: no verified HTTPS primary CTA')
                for k in ('menu_opens','menu_closes','next_button_updates_counter'):
                    if not info[k]: problems.append(f'{screen}: interaction failed: {k}')
                if info['copy_feedback'].startswith('Select the brief below') and not (info['copy_fallback_visible'] and info['copy_fallback_has_text']):
                    problems.append(f'{screen}: inquiry manual-copy fallback is missing')
                if errors: problems.append(f'{screen}: JavaScript errors: {errors[:4]}')
                info['console_errors']=errors;results[screen]=info
                page.close()
        finally: browser.close()
    from PIL import Image,ImageDraw
    for screen in results:
        pics=sorted(out.glob(f'{screen}_[0-9][0-9].png'))
        thumbs=[]
        for p in pics:
            img=Image.open(p).convert('RGB');img.thumbnail((540,340))
            thumbs.append((p.name,img.copy()))
        cols=3 if screen=='desktop' else 5; cw=570 if screen=='desktop' else 260;ch=390 if screen=='desktop' else 370
        canvas=Image.new('RGB',(cols*cw,((len(thumbs)+cols-1)//cols)*ch),'#e5e1da');d=ImageDraw.Draw(canvas)
        for j,(name,img) in enumerate(thumbs):
            if screen=='mobile': img.thumbnail((245,340))
            x=j%cols*cw+10;y=j//cols*ch+23
            canvas.paste(img,(x,y));d.text((x,y-16),name,fill='#28231c')
        canvas.save(out/f'{screen}_contact.jpg',quality=90)
    return {'status':'pass' if not problems else 'fail','render_mode':render_mode,'screens':results},problems


def main():
    p=argparse.ArgumentParser();p.add_argument('--site',required=True);p.add_argument('--out',required=True);p.add_argument('--require-browser',action='store_true')
    args=p.parse_args();site=Path(args.site).resolve();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    problems,counts,_=structural_checks(site)
    browser={}
    try: browser, browser_problems=browser_check(site,out,require_browser=args.require_browser);problems+=browser_problems
    except Exception as error:
        problems.append(f'Browser QC failed to execute: {error}');browser={'status':'blocked','reason':str(error)[:600]}
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(), 'index_html':str(site/'index.html'),
            'file_bytes':(site/'index.html').stat().st_size if (site/'index.html').exists() else 0,
            'checks':counts,'browser':browser,'problems':problems,
            'status':'PASS' if not problems and browser.get('status')=='pass' else 'FAIL',
            'manual_visual_acceptance':'pending_independent_inspection',
            'business_authorization':'not_assessed',
            'external_publication':'not_performed'}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'problems':problems,'screenshots':[f'{s}_contact.jpg' for s in ('desktop','mobile') if (out/f'{s}_contact.jpg').exists()]},ensure_ascii=False))
    return 0 if report['status']=='PASS' else 1
if __name__=='__main__': raise SystemExit(main())
