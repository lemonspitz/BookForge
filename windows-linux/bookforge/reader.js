(function () {
  if (window.bf) return;
  const style = document.createElement('style');
  document.documentElement.appendChild(style);
  const themes = { light: ['#ffffff', '#1d1d1f', '#0a58ca'], sepia: ['#f4ecd8', '#433422', '#8a5a2b'], dark: ['#1e1e1e', '#d9d9d9', '#8ab4ff'] };
  let s = null, page = 0, pages = 1, W = 1, H = 1, vertical = false, rtl = false;
  const post = (m) => console.log('BF' + JSON.stringify(m));
  const se = () => document.scrollingElement || document.documentElement;

  function layout() {
    W = window.innerWidth;
    H = window.innerHeight;
    const pad = Math.max(24, Math.round(W * 0.06)), vpad = 40;
    const cols = (s.spread && W >= 1000 && !vertical) ? 2 : 1;
    const colW = (W - 2 * pad * cols) / cols;
    const [bg, fg, link] = themes[s.theme] || themes.light;
    const family = s.font === 'serif' ? 'Georgia, "Noto Serif", "Yu Mincho", "Noto Serif CJK JP", serif'
                 : s.font === 'sans' ? '"Segoe UI", "Noto Sans", "Yu Gothic UI", "Noto Sans CJK JP", sans-serif' : null;
    let css = `
      html { background: ${bg} !important; font-size: ${s.scale}% !important; overflow: hidden !important;
             margin: 0 !important; padding: 0 !important; height: ${H}px !important; -webkit-text-size-adjust: none; }
      body { background: transparent !important; color: ${fg} !important; margin: 0 !important;
             box-sizing: border-box !important; height: ${H}px !important; width: ${W}px !important;
             max-width: none !important; max-height: none !important; padding: ${vpad}px ${pad}px !important;
             column-fill: auto !important; overflow-wrap: break-word; }
      a, a * { color: ${link} !important; }
      img, svg, video { max-width: 100% !important; max-height: ${H - 2 * vpad}px !important; object-fit: contain;
                        break-inside: avoid; page-break-inside: avoid; }`;
    if (family) css += `body, body p, body div, body span, body li, body blockquote, body td { font-family: ${family} !important; }`;
    if (s.theme !== 'light') css += `body *:not(a) { color: inherit !important; background-color: transparent !important; }`;
    if (vertical) css += `body { column-width: ${H - 2 * vpad}px !important; column-gap: ${2 * vpad}px !important; }`;
    else css += `body { column-width: ${colW}px !important; column-gap: ${2 * pad}px !important; }`;
    style.textContent = css;
  }
  function count() {
    pages = Math.max(1, vertical ? Math.ceil((se().scrollHeight - 4) / H) : Math.ceil((se().scrollWidth - 4) / W));
  }
  function show() {
    if (vertical) se().scrollTop = page * H; else se().scrollLeft = page * W;
    post({ type: 'page', page: page, pages: pages });
  }
  function settle(fn) { requestAnimationFrame(() => requestAnimationFrame(fn)); }
  function pageOf(el) {
    const r = el.getBoundingClientRect();
    const p = vertical ? Math.floor((r.top + se().scrollTop) / H) : Math.floor((r.left + se().scrollLeft) / W);
    return Math.max(0, Math.min(pages - 1, p));
  }

  window.bf = {
    setup(settings, fraction, anchor, pageRTL) {
      s = settings;
      rtl = pageRTL;
      const wm = getComputedStyle(document.body || document.documentElement).writingMode || '';
      vertical = wm.startsWith('vertical');
      layout();
      const go = () => settle(() => {
        count();
        if (anchor) { this.anchor(anchor); return; }
        page = Math.round(fraction * (pages - 1));
        show();
      });
      if (document.readyState === 'complete') go(); else window.addEventListener('load', go, { once: true });
    },
    restyle(settings) {
      if (!s) return;
      const f = pages > 1 ? page / (pages - 1) : 0;
      s = settings;
      layout();
      settle(() => { count(); page = Math.round(f * (pages - 1)); show(); });
    },
    next() { if (page < pages - 1) { page++; show(); } else post({ type: 'edge', dir: 'next' }); },
    prev() { if (page > 0) { page--; show(); } else post({ type: 'edge', dir: 'prev' }); },
    fraction(f) { count(); page = Math.round(f * (pages - 1)); show(); },
    anchor(id) {
      const el = document.getElementById(id) || document.getElementsByName(id)[0];
      page = el ? pageOf(el) : 0;
      show();
    },
  };

  const backwards = () => vertical || rtl;
  document.addEventListener('keydown', (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key;
    if (k === ' ' || k === 'PageDown' || k === 'ArrowDown') { e.shiftKey && k === ' ' ? bf.prev() : bf.next(); }
    else if (k === 'PageUp' || k === 'ArrowUp') bf.prev();
    else if (k === 'ArrowRight') backwards() ? bf.prev() : bf.next();
    else if (k === 'ArrowLeft') backwards() ? bf.next() : bf.prev();
    else return;
    e.preventDefault();
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('a')) return;
    if (String(window.getSelection())) return;
    const x = e.clientX / W;
    if (x < 0.3) backwards() ? bf.next() : bf.prev();
    else if (x > 0.7) backwards() ? bf.prev() : bf.next();
  });
  let acc = 0, locked = false;
  window.addEventListener('wheel', (e) => {
    e.preventDefault();
    if (locked) return;
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? (backwards() ? -e.deltaX : e.deltaX) : e.deltaY;
    acc += d;
    if (Math.abs(acc) > 40) {
      acc > 0 ? bf.next() : bf.prev();
      locked = true;
      setTimeout(() => { locked = false; acc = 0; }, 450);
    }
  }, { passive: false });
  let timer = null;
  window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => s && bf.restyle(s), 120); });
})();
