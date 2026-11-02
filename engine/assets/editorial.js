(() => {
  'use strict';
  const sections = Array.from(document.querySelectorAll('main .chapter'));
  const progress = document.getElementById('read-progress');
  const display = document.getElementById('current-page');
  const prev = document.getElementById('previous-page');
  const next = document.getElementById('next-page');
  const menu = document.getElementById('chapter-menu');
  const menuToggle = document.querySelector('.chapter-toggle');
  let selected = 0;
  const total = sections.length;
  function updateSelected(n) {
    selected = Math.max(0, Math.min(total - 1, n));
    display.textContent = `${String(selected + 1).padStart(2,'0')} / ${String(total).padStart(2,'0')}`;
    prev.disabled = selected === 0;
    next.disabled = selected === total - 1;
    document.querySelectorAll('#chapter-menu a').forEach((a,i) => { if (i === selected) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current'); });
  }
  function jump(n) {
    if (n < 0 || n >= total) return;
    sections[n].scrollIntoView({behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
    updateSelected(n);
  }
  prev.addEventListener('click', () => jump(selected-1));
  next.addEventListener('click', () => jump(selected+1));
  // Determine the current section from a fixed viewport reading line. Using only
  // IntersectionObserver entry changes can regress the counter during smooth scroll.
  function syncCurrentChapter() {
    const readingLine = window.innerHeight * .32;
    let active = 0;
    for (let i = 0; i < sections.length; i++) {
      if (sections[i].getBoundingClientRect().top <= readingLine) active = i;
      else break;
    }
    if (active !== selected) updateSelected(active);
  }
  let scrollQueued=false;
  window.addEventListener('scroll', () => {
    if (scrollQueued) return; scrollQueued=true;
    requestAnimationFrame(() => { const max=document.documentElement.scrollHeight-window.innerHeight; progress.style.width=(max>0?Math.max(0,Math.min(100,(window.scrollY/max)*100)):100).toFixed(1)+'%'; syncCurrentChapter(); scrollQueued=false; });
  },{passive:true});
  menuToggle.addEventListener('click', () => {
    const opened = menu.hasAttribute('hidden');
    if (opened) menu.removeAttribute('hidden'); else menu.setAttribute('hidden','');
    menuToggle.setAttribute('aria-expanded',String(opened));
  });
  menu.querySelectorAll('a').forEach(a => a.addEventListener('click',() => { menu.setAttribute('hidden','');menuToggle.setAttribute('aria-expanded','false');}));
  document.addEventListener('keydown', e => {
    const tag=e.target && e.target.tagName;
    if (['INPUT','TEXTAREA','SELECT'].includes(tag) || e.altKey || e.metaKey || e.ctrlKey) return;
    if (e.key==='Escape' && !menu.hasAttribute('hidden')) {menu.setAttribute('hidden','');menuToggle.setAttribute('aria-expanded','false');}
    if (e.key==='ArrowRight') jump(selected+1);
    if (e.key==='ArrowLeft') jump(selected-1);
  });
  document.querySelectorAll('.copy-brief').forEach(btn => btn.addEventListener('click', async () => {
    const value=btn.getAttribute('data-inquiry')||'';
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard not available');
      await navigator.clipboard.writeText(value.replace(/\\n/g,'\n'));
      const orig=btn.innerHTML; btn.textContent='Inquiry copied — not sent';
      btn.setAttribute('aria-label','Inquiry copied, not sent');
      btn.dataset.copied='true';
      btn.addEventListener('blur',()=>{btn.innerHTML=orig;},{once:true});
    } catch (_) {
      // Provide a real manual-copy fallback; never fake clipboard success.
      const fallback=btn.parentElement.querySelector('.copy-fallback');
      const area=fallback?.querySelector('textarea');
      if (fallback && area) {
        fallback.hidden=false;
        area.focus();
        area.select();
        btn.textContent='Select the brief below — not sent';
      } else {
        btn.textContent='Copy unavailable — please use the official contact link';
      }
      btn.dataset.copied='false';
    }
  }));
  updateSelected(0);
  syncCurrentChapter();
})();
