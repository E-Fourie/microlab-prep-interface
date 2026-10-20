/* Styled dropdowns: progressively enhances every native <select> in the page (and any added later).
   The native <select> stays in the DOM, hidden, and is kept in sync, so code that reads .value or listens for 'change' keeps working.
   A button shows the current option; a popover (role=listbox) lists the options, with <optgroup>s as headed groups.
   Keyboard: arrows, Home/End, Enter/Space, Escape, Tab, type-ahead. Opt a select out with the class "native". */
'use strict';
(function () {
  let open = null; // { sel, btn, pop, items, active }
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  let uid = 0;

  function entries(sel) { // flat list of { opt, group } for the options, in order
    const out = [];
    for (const n of sel.children) {
      if (n.tagName === 'OPTGROUP') for (const o of n.children) out.push({ opt: o, group: n });
      else if (n.tagName === 'OPTION') out.push({ opt: n, group: null });
    }
    return out;
  }
  const label = o => (o && o.textContent.trim()) || '';

  function paint(sel) {
    const b = sel._btn; if (!b) return;
    const cur = sel.options[sel.selectedIndex];
    b.firstChild.textContent = label(cur) || ' ';
    b.classList.toggle('placeholder', !!cur && cur.value === '');
    b.disabled = sel.disabled;
  }

  function enhance(sel) {
    if (sel._btn || sel.multiple || sel.size > 1 || sel.classList.contains('native')) return;
    const wrap = el('div', 'sel'), btn = el('button', 'sel-btn'), caret = el('span', 'sel-caret');
    btn.type = 'button'; btn.setAttribute('aria-haspopup', 'listbox'); btn.setAttribute('aria-expanded', 'false');
    btn.append(el('span', 'sel-val'), caret);
    const lab = sel.id && document.querySelector(`label[for="${sel.id}"]`);
    if (lab) { lab.id = lab.id || 'sl' + ++uid; btn.setAttribute('aria-labelledby', lab.id); lab.addEventListener('click', e => { e.preventDefault(); btn.focus(); }); }
    else if (sel.getAttribute('aria-label')) btn.setAttribute('aria-label', sel.getAttribute('aria-label'));
    sel.parentNode.insertBefore(wrap, sel); wrap.append(sel, btn);
    sel.classList.add('sel-native'); sel.tabIndex = -1; sel.setAttribute('aria-hidden', 'true');
    sel._btn = btn; paint(sel);
    sel.addEventListener('change', () => paint(sel));
    btn.addEventListener('click', () => (open && open.sel === sel ? close() : show(sel)));
    btn.addEventListener('keydown', e => key(e, sel));
    btn.addEventListener('blur', () => { if (open && open.sel === sel && !open.pop.matches(':hover')) close(); });
  }

  function show(sel) {
    close();
    const btn = sel._btn, pop = el('div', 'sel-pop'), list = el('ul', 'sel-list'), items = [];
    pop.append(list); list.setAttribute('role', 'listbox'); list.id = 'sp' + ++uid;
    let lastGroup = null;
    entries(sel).forEach(({ opt, group }) => {
      if (group && group !== lastGroup) { const h = el('li', 'sel-group', group.label); h.setAttribute('role', 'presentation'); list.append(h); }
      lastGroup = group;
      const li = el('li', 'sel-opt', label(opt)); li.setAttribute('role', 'option'); li.id = list.id + '-' + items.length;
      li.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
      if (opt.disabled || (group && group.disabled)) li.setAttribute('aria-disabled', 'true');
      if (opt.selected) li.classList.add('sel-on');
      if (opt.value === '') li.classList.add('placeholder');
      items.push({ li, opt });
      list.append(li);
    });
    pop.addEventListener('click', e => { e.stopPropagation(); const li = e.target.closest('.sel-opt'); if (li) choose(items.findIndex(x => x.li === li)); });
    pop.addEventListener('mousemove', e => { const li = e.target.closest('.sel-opt'); if (li) mark(items.findIndex(x => x.li === li), false); });
    pop.addEventListener('mousedown', e => e.preventDefault()); // keep focus on the button
    document.body.append(pop);
    open = { sel, btn, pop, items, active: Math.max(0, sel.selectedIndex), typed: '', typedAt: 0 };
    btn.setAttribute('aria-expanded', 'true'); btn.setAttribute('aria-controls', list.id);
    place(); mark(open.active, true);
  }

  function place() {
    if (!open) return;
    const { btn, pop } = open, r = btn.getBoundingClientRect(), vh = innerHeight, vw = innerWidth;
    pop.style.minWidth = r.width + 'px'; pop.style.maxWidth = Math.min(420, vw - 16) + 'px'; pop.style.maxHeight = '';
    const h = Math.min(pop.scrollHeight, 320), below = vh - r.bottom - 8, above = r.top - 8, up = h > below && above > below;
    pop.style.maxHeight = Math.max(120, Math.min(320, up ? above : below)) + 'px';
    pop.classList.toggle('up', up);
    const ph = pop.offsetHeight;
    pop.style.top = (up ? Math.max(8, r.top - ph - 4) : r.bottom + 4) + 'px';
    pop.style.left = Math.max(8, Math.min(r.left, vw - pop.offsetWidth - 8)) + 'px';
  }

  function mark(i, scroll) {
    if (!open || i < 0 || i >= open.items.length) return;
    open.items.forEach((x, n) => x.li.classList.toggle('sel-active', n === i));
    open.active = i; open.btn.setAttribute('aria-activedescendant', open.items[i].li.id);
    if (scroll) open.items[i].li.scrollIntoView({ block: 'nearest' });
  }

  function choose(i) {
    if (!open || i < 0 || open.items[i].opt.disabled || open.items[i].li.getAttribute('aria-disabled')) return;
    const { sel, btn } = open, idx = [...sel.options].indexOf(open.items[i].opt);
    close(); btn.focus();
    if (sel.selectedIndex !== idx) { sel.selectedIndex = idx; sel.dispatchEvent(new Event('input', { bubbles: true })); sel.dispatchEvent(new Event('change', { bubbles: true })); }
    paint(sel);
  }

  function close() {
    if (!open) return;
    open.pop.remove(); open.btn.setAttribute('aria-expanded', 'false'); open.btn.removeAttribute('aria-activedescendant'); open = null;
  }

  function step(from, dir) { // next enabled option from `from` in direction dir
    for (let i = from + dir; i >= 0 && i < open.items.length; i += dir) if (!open.items[i].li.getAttribute('aria-disabled')) return i;
    return from;
  }

  function key(e, sel) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const k = e.key, isOpen = open && open.sel === sel;
    if (!isOpen) {
      if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Enter' || k === ' ') { e.preventDefault(); show(sel); }
      else if (k.length === 1) { e.preventDefault(); show(sel); typeahead(k); }
      return;
    }
    if (k === 'ArrowDown') mark(step(open.active, 1), true);
    else if (k === 'ArrowUp') mark(step(open.active, -1), true);
    else if (k === 'Home') mark(step(-1, 1), true);
    else if (k === 'End') mark(step(open.items.length, -1), true);
    else if (k === 'PageDown') mark(Math.min(open.items.length - 1, open.active + 8), true);
    else if (k === 'PageUp') mark(Math.max(0, open.active - 8), true);
    else if (k === 'Enter' || k === ' ') choose(open.active);
    else if (k === 'Escape') { close(); }
    else if (k === 'Tab') { close(); return; }
    else if (k.length === 1) typeahead(k);
    else return;
    e.preventDefault(); e.stopPropagation(); // Escape must not also close the modal or the machine menu behind
  }

  function typeahead(ch) {
    const now = Date.now(); open.typed = now - open.typedAt > 700 ? ch.toLowerCase() : open.typed + ch.toLowerCase(); open.typedAt = now;
    const n = open.items.length, from = open.typed.length === 1 ? open.active + 1 : open.active; // a repeated first letter cycles
    for (let j = 0; j < n; j++) {
      const i = (from + j) % n, x = open.items[i];
      if (!x.li.getAttribute('aria-disabled') && x.li.textContent.trim().toLowerCase().startsWith(open.typed)) return mark(i, true);
    }
  }

  document.addEventListener('mousedown', e => { if (open && !open.pop.contains(e.target) && !open.btn.contains(e.target)) close(); });
  addEventListener('resize', place);
  addEventListener('scroll', e => { if (open && !open.pop.contains(e.target)) place(); }, true);

  function scan(root) {
    if (root.nodeType !== 1) return;
    if (root.tagName === 'SELECT') enhance(root);
    else root.querySelectorAll && root.querySelectorAll('select').forEach(enhance);
  }
  new MutationObserver(ms => {
    ms.forEach(m => m.addedNodes.forEach(scan));
    if (open && !open.btn.isConnected) close(); // the view re-rendered under an open list
  }).observe(document.documentElement, { childList: true, subtree: true });
  scan(document.body);
  window.Select = { enhance: scan, refresh: root => (root || document).querySelectorAll('select').forEach(s => s._btn && paint(s)) };
})();
