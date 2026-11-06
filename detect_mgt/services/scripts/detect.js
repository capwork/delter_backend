/* Delete-control detector. Runs inside the page via Selenium execute_script.
 *
 * arguments[0] = {mode: "scan" | "triggers" | "expanders" | "hover_targets",
 *                 strong: [...], weak: [...], minScore: 0.5, max: 60, exclude: [selectors]}
 *
 * mode "scan"          -> list of candidate delete controls, each with a score and the reasons for it
 * mode "triggers"      -> visible menu openers ("more", kebab, dropdown toggles)
 * mode "expanders"     -> accordions / tabs / <details> that may hide controls (aggressive mode)
 * mode "hover_targets" -> rows / cards whose actions may only appear on hover (aggressive mode)
 */
const opts = arguments[0];
const CLICKABLE =
  'a[href], button, input[type=button], input[type=submit], input[type=image], [role=button], ' +
  '[role=menuitem], [role=link], [onclick], [data-action], [data-method], [data-confirm], summary';
const MAX_NODES = 6000;

function visible(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return false;
  const s = getComputedStyle(el);
  return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.01;
}

function cssPath(el) {
  const parts = [];
  let node = el;
  while (node && node.nodeType === 1 && node !== document.documentElement) {
    if (node.id && /^[A-Za-z][\w-]*$/.test(node.id)) {
      const sel = '#' + CSS.escape(node.id);
      if (document.querySelectorAll(sel).length === 1) { parts.unshift(sel); break; }
    }
    const tag = node.tagName.toLowerCase();
    if (tag === 'body') { parts.unshift('body'); break; }
    let i = 1, sib = node;
    while ((sib = sib.previousElementSibling)) if (sib.tagName === node.tagName) i++;
    parts.unshift(tag + ':nth-of-type(' + i + ')');
    node = node.parentElement;
  }
  return parts.join(' > ');
}

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const strongRx = new RegExp('(^|[^a-z])(' + opts.strong.map(esc).join('|') + ')(d|s|ed|ing|e)?([^a-z]|$)', 'i');
const weakRx = new RegExp('(^|[^a-z])(' + opts.weak.map(esc).join('|') + ')(d|s|ed|ing|e)?([^a-z]|$)', 'i');
const NEGATIVE = /\b(undo|restore|recover|un-?delete)\b|\bdeleted\b|clear (all )?(filters?|search|selection|text)|no results|remove filter/i;

// split camelCase / kebab / snake into words so "fa-trash-o" and "deleteBtn" both match
const words = (s) => (s || '').replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[-_./:]+/g, ' ');
function weight(str) {
  const w = words(str);
  if (strongRx.test(w)) return 1.0;
  if (weakRx.test(w)) return 0.6;
  return 0;
}
const attr = (el, n) => (el.getAttribute(n) || '').trim();

function textOf(el) {
  let t = (el.innerText || el.value || '').trim().replace(/\s+/g, ' ');
  return t.length > 80 ? t.slice(0, 80) : t;
}

function iconTokens(el) {
  const bits = [el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className];
  el.querySelectorAll('i, svg, span, img, use, path').forEach((c, idx) => {
    if (idx > 12) return;
    bits.push(c.className && c.className.baseVal !== undefined ? c.className.baseVal : c.className);
    bits.push(c.getAttribute('data-icon'), c.getAttribute('data-feather'), c.getAttribute('name'));
    bits.push(c.getAttribute('href'), c.getAttribute('xlink:href'), c.getAttribute('alt'), c.getAttribute('src'));
  });
  el.querySelectorAll('svg title').forEach((t) => bits.push(t.textContent));
  return bits.filter((b) => typeof b === 'string').join(' ');
}

function clickableOf(el) {
  return el.closest(CLICKABLE) || el;
}

function score(el) {
  const reasons = [];
  let total = 0;
  const add = (signal, detail, w) => { reasons.push({signal, detail, weight: Math.round(w * 100) / 100}); total += w; };

  const text = textOf(el);
  if (text && NEGATIVE.test(text)) return {score: 0, reasons: [{signal: 'negative_text', detail: text, weight: 0}]};
  let w;
  if (text.length <= 60 && (w = weight(text))) add('text', text, 0.6 * w);
  for (const n of ['aria-label', 'title', 'data-tooltip', 'data-original-title', 'data-bs-original-title']) {
    const v = attr(el, n);
    if (v && NEGATIVE.test(v)) return {score: 0, reasons: [{signal: 'negative_' + n, detail: v, weight: 0}]};
    if (v && (w = weight(v)) && v !== text) { add(n, v, 0.5 * w); break; }
  }
  const alt = el.querySelector('img[alt]');
  if (alt && (w = weight(alt.alt))) add('img_alt', alt.alt, 0.5 * w);
  const icon = iconTokens(el);
  if ((w = weight(icon))) add('icon', icon.trim().replace(/\s+/g, ' ').slice(0, 80), 0.4 * w);
  const idname = attr(el, 'id') + ' ' + attr(el, 'name');
  if ((w = weight(idname))) add('id_name', idname.trim(), 0.2 * w);
  for (const a of el.attributes) {
    if (!a.name.startsWith('data-') || ['data-confirm', 'data-tooltip', 'data-original-title'].includes(a.name)) continue;
    if ((w = weight(a.name + ' ' + a.value))) { add('data_attr', a.name + '=' + a.value.slice(0, 40), 0.4 * w); break; }
  }
  const href = attr(el, 'href') || attr(el, 'formaction');
  const form = el.closest('form');
  const action = href || (form && form.getAttribute('action')) || '';
  if (action && (w = weight(action.split('?')[0]))) add('url', action.slice(0, 80), 0.4 * w);
  const method = (attr(el, 'data-method') || attr(el, 'data-http-method') || (form && attr(form, 'method')) || '').toLowerCase();
  const hiddenMethod = form && form.querySelector('input[name=_method]');
  if (method === 'delete' || (hiddenMethod && /delete/i.test(hiddenMethod.value))) add('http_delete', 'DELETE method', 0.6);
  const onclick = attr(el, 'onclick');
  if (onclick && (w = weight(onclick))) add('onclick', onclick.slice(0, 60), 0.3 * w);

  const row = el.closest('tr, li, [role=row], [class*=row i], [class*=item i], [class*=card i]');
  if (row && total > 0) add('in_repeated_row', row.tagName.toLowerCase(), 0.1);
  return {score: Math.min(1, total), reasons};
}

function hints(el) {
  const out = [];
  if (/confirm\s*\(/.test(attr(el, 'onclick'))) out.push('inline_confirm_call');
  if (el.hasAttribute('data-confirm')) out.push('data_confirm_attr');
  if (el.hasAttribute('data-bs-toggle') && attr(el, 'data-bs-toggle') === 'modal') out.push('bootstrap_modal_trigger');
  if (el.hasAttribute('data-toggle') && attr(el, 'data-toggle') === 'modal') out.push('bootstrap_modal_trigger');
  return out;
}

function clickables() {
  const set = new Set();
  document.querySelectorAll(CLICKABLE).forEach((e) => set.add(e));
  // icon-only controls styled as pointers (div/span/i/svg) that have no semantic role
  const all = document.querySelectorAll('div, span, i, svg, img, li, td');
  const limit = Math.min(all.length, MAX_NODES);
  for (let i = 0; i < limit; i++) {
    const e = all[i];
    if (e.closest(CLICKABLE) && e.closest(CLICKABLE) !== e) continue;
    if (getComputedStyle(e).cursor === 'pointer' && e.children.length < 6) set.add(clickableOf(e));
  }
  return [...set];
}

const excluded = new Set(opts.exclude || []);

if (opts.mode === 'scan') {
  const out = [];
  const seen = new Set();
  for (const el of clickables()) {
    if (!visible(el)) continue;
    const sel = cssPath(el);
    if (seen.has(sel)) continue;
    seen.add(sel);
    const {score: sc, reasons} = score(el);
    if (sc < opts.minScore) continue;
    const r = el.getBoundingClientRect();
    out.push({
      selector: sel, tag: el.tagName.toLowerCase(), text: textOf(el),
      aria_label: attr(el, 'aria-label'), title: attr(el, 'title'),
      href: attr(el, 'href') || null, score: Math.round(sc * 100) / 100, reasons, hints: hints(el),
      html: el.outerHTML.slice(0, 700),
      rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
    });
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, opts.max);
}

if (opts.mode === 'triggers') {
  const TEXT = /^(more|actions?|options?|menu|manage|settings|⋮|…|\.\.\.|•••|⋯)$/i;
  const CLS = /kebab|ellipsis|three-?dots?|meatball|overflow|action-?menu|dropdown-toggle|more-?(actions|options)?|dots/i;
  const out = [];
  const seen = new Set();
  for (const el of clickables()) {
    if (!visible(el)) continue;
    const sel = cssPath(el);
    if (seen.has(sel) || excluded.has(sel)) continue;
    seen.add(sel);
    if (score(el).score >= opts.minScore) continue;      // it is a delete control itself, not an opener
    const label = textOf(el) || attr(el, 'aria-label') || attr(el, 'title');
    const popup = attr(el, 'aria-haspopup');
    const toggle = attr(el, 'data-bs-toggle') || attr(el, 'data-toggle');
    const cls = (typeof el.className === 'string' ? el.className : '') + ' ' + iconTokens(el);
    const isTrigger =
      (popup && popup !== 'false' && popup !== 'dialog') || toggle === 'dropdown' ||
      TEXT.test(label) || CLS.test(cls) ||
      (attr(el, 'aria-expanded') === 'false' && /menu|more|action|option/i.test(label + ' ' + cls));
    if (isTrigger) out.push({selector: sel, label});
  }
  return out.slice(0, opts.max);
}

if (opts.mode === 'expanders') {
  const out = [];
  const seen = new Set();
  document.querySelectorAll('summary, [aria-expanded=false], [role=tab], .accordion-button, [data-bs-toggle=collapse], [data-bs-toggle=tab], [data-toggle=collapse], [data-toggle=tab]').forEach((el) => {
    if (!visible(el)) return;
    if (el.tagName === 'SUMMARY' && el.parentElement.open) return;
    if (el.getAttribute('aria-selected') === 'true') return;
    if (el.matches('a[href]') && !(attr(el, 'href').startsWith('#') || el.getAttribute('role') === 'tab')) return;
    const sel = cssPath(el);
    if (seen.has(sel) || excluded.has(sel)) return;
    seen.add(sel);
    out.push({selector: sel, label: textOf(el)});
  });
  return out.slice(0, opts.max);
}

if (opts.mode === 'hover_targets') {
  const out = [];
  document.querySelectorAll('tr, li, [role=row], [class*=row i], [class*=item i], [class*=card i], [class*=list-group-item i]').forEach((el) => {
    if (!visible(el) || out.length >= opts.max) return;
    if (el.querySelectorAll('tr, li, [role=row]').length > 3) return;       // containers, not rows
    out.push({selector: cssPath(el), label: textOf(el).slice(0, 40)});
  });
  return out;
}
return [];
