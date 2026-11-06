/* Installed in every new document (CDP Page.addScriptToEvaluateOnNewDocument) before any page script runs.
 *
 * 1. Records and neutralises native dialogs: confirm() answers "Cancel", so nothing can be confirmed.
 * 2. Once armed (right before the probe's click) it blocks and records anything that could change data:
 *    fetch / XHR / sendBeacon with a non-GET method, form submissions and link navigations.
 * 3. After the click, collect() reports what happened, including whether a themed confirmation
 *    (modal / popconfirm) appeared, found by diffing which elements became visible.
 */
(() => {
  if (window.__delter) return;
  const S = {dialogs: [], blocked: [], armed: false, seenVisible: new WeakSet(), target: null, row: null};
  const rec = (arr, o) => { o.t = Date.now(); arr.push(o); };
  const mutating = (m) => !['GET', 'HEAD', 'OPTIONS'].includes(String(m || 'GET').toUpperCase());
  // GraphQL queries are POSTs but read-only; mutations are not
  const graphqlQuery = (b) => typeof b === 'string' && /"query"|^\s*query\b/.test(b) && !/mutation/i.test(b);

  window.confirm = function (msg) { rec(S.dialogs, {type: 'confirm', message: String(msg ?? '')}); return false; };
  window.alert = function (msg) { rec(S.dialogs, {type: 'alert', message: String(msg ?? '')}); };
  window.prompt = function (msg) { rec(S.dialogs, {type: 'prompt', message: String(msg ?? '')}); return null; };

  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function (input, init) {
      if (S.armed) {
        const method = (init && init.method) || (input && input.method) || 'GET';
        const url = typeof input === 'string' ? input : (input && input.url) || String(input);
        if (mutating(method) && !graphqlQuery(init && init.body)) {
          rec(S.blocked, {kind: 'fetch', method: String(method).toUpperCase(), url});
          return new Promise(() => {});
        }
      }
      return origFetch.apply(this, arguments);
    };
  }
  const xOpen = XMLHttpRequest.prototype.open, xSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__d = {method: m, url: String(u)}; return xOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) {
    if (S.armed && this.__d && mutating(this.__d.method) && !graphqlQuery(body)) {
      rec(S.blocked, {kind: 'xhr', method: String(this.__d.method).toUpperCase(), url: this.__d.url});
      return;
    }
    return xSend.apply(this, arguments);
  };
  if (navigator.sendBeacon) {
    const beacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function (url, data) {
      if (S.armed) { rec(S.blocked, {kind: 'beacon', method: 'POST', url: String(url)}); return true; }
      return beacon(url, data);
    };
  }
  const DELETE_URL = /\/(delete|destroy|remove|erase|purge)(\b|\/|_|-|\?|$)/i;
  const blockForm = (f) => {
    const method = String(f.getAttribute('method') || 'get').toUpperCase();
    if (mutating(method) || DELETE_URL.test(f.action || '')) {
      rec(S.blocked, {kind: 'form', method, url: f.action || location.href});
      return true;
    }
    return false;
  };
  window.addEventListener('submit', (e) => { if (S.armed && blockForm(e.target)) e.preventDefault(); }, true);
  const fSubmit = HTMLFormElement.prototype.submit, fReq = HTMLFormElement.prototype.requestSubmit;
  HTMLFormElement.prototype.submit = function () { if (S.armed && blockForm(this)) return; return fSubmit.apply(this, arguments); };
  HTMLFormElement.prototype.requestSubmit = function () { if (S.armed && blockForm(this)) return; return fReq.apply(this, arguments); };
  window.addEventListener('click', (e) => {
    if (!S.armed) return;
    const a = e.target && e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    const href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || /^javascript:/i.test(href)) return;
    rec(S.blocked, {kind: 'navigation', method: 'GET', url: a.href});
    e.preventDefault();
  }, true);

  const vis = (el) => {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0.01;
  };

  const CLASS_RX = /modal|dialog|popup|overlay|backdrop|swal|sweetalert|lightbox|confirm|ReactModal|MuiDialog|ant-modal|el-dialog|cdk-overlay|v-dialog|popconfirm/i;
  const CONFIRM_RX = /^(yes|ok|okay|confirm|delete|remove|proceed|continue|sure|accept|agree|yes[, ].*|delete .*|remove .*)$/i;
  const CANCEL_RX = /^(no|cancel|keep|close|dismiss|never ?mind|go back|not now|no[, ].*|keep .*)$/i;
  const TEXT_RX = /are you sure|confirm|cannot be undone|can'?t be undone|permanently|irreversible|this action|do you want to/i;

  function describe(root) {
    const sig = new Set();
    const nodes = [root, ...Array.from(root.querySelectorAll('*')).slice(0, 60)];
    const vw = innerWidth, vh = innerHeight;
    if (root.matches('[role=dialog],[role=alertdialog],dialog') || root.getAttribute('aria-modal') === 'true' ||
        root.querySelector('[role=dialog],[role=alertdialog],dialog[open],[aria-modal=true]')) sig.add('aria_dialog');
    for (const n of nodes) {
      const cs = getComputedStyle(n);
      if (['fixed', 'absolute', 'sticky'].includes(cs.position)) {
        const r = n.getBoundingClientRect();
        const z = parseInt(cs.zIndex, 10) || 0;
        if (cs.position === 'fixed' && r.width >= vw * 0.9 && r.height >= vh * 0.9) sig.add('backdrop');
        if (z >= 100 || (r.width >= vw * 0.5 && r.height >= vh * 0.2 && cs.position === 'fixed')) sig.add('overlay_position');
      }
      const cls = (typeof n.className === 'string' ? n.className : '') + ' ' + (n.id || '');
      if (CLASS_RX.test(cls)) sig.add('modal_class');
    }
    const buttons = Array.from(root.querySelectorAll('button, [role=button], a, input[type=button], input[type=submit]'))
      .filter(vis).map((b) => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim()).filter(Boolean);
    if (buttons.some((b) => CONFIRM_RX.test(b)) && buttons.some((b) => CANCEL_RX.test(b))) sig.add('confirm_buttons');
    const text = (root.innerText || '').trim().replace(/\s+/g, ' ');
    if (TEXT_RX.test(text)) sig.add('confirmation_text');
    return {signals: [...sig], text: text.slice(0, 400), buttons: buttons.slice(0, 8), html: root.outerHTML.slice(0, 1500), root};
  }

  function selectorOf(el) {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 4; n = n.parentElement) {
      let p = n.tagName.toLowerCase();
      if (n.id) p += '#' + n.id;
      else if (typeof n.className === 'string' && n.className.trim()) p += '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.');
      parts.unshift(p);
    }
    return parts.join(' > ');
  }

  function detectModal() {
    const appeared = [];
    const set = new Set();
    document.querySelectorAll('body *').forEach((e) => { if (vis(e) && !S.seenVisible.has(e)) { appeared.push(e); set.add(e); } });
    const roots = appeared.filter((e) => !set.has(e.parentElement));
    if (!roots.length) return null;
    const infos = roots.map(describe);
    const all = new Set(infos.flatMap((i) => i.signals));
    const best = infos.reduce((a, b) => (b.signals.length > a.signals.length ? b : a), infos[0]);
    const structural = ['aria_dialog', 'overlay_position', 'modal_class', 'backdrop'].filter((s) => all.has(s));
    const content = all.has('confirm_buttons') || all.has('confirmation_text');
    const isConfirmation =
      all.has('aria_dialog') || structural.length >= 2 || (structural.length >= 1 && content) ||
      (all.has('confirm_buttons') && all.has('confirmation_text'));
    return {is_confirmation: isConfirmation, signals: [...all], text: best.text, buttons: best.buttons,
            html: best.html, selector: selectorOf(best.root), appeared_roots: roots.length};
  }

  window.__delter = {
    state: S,
    arm(target) {
      S.seenVisible = new WeakSet();
      document.querySelectorAll('*').forEach((e) => { if (vis(e)) S.seenVisible.add(e); });
      S.target = target || null;
      S.row = target ? target.closest('tr, li, [role=row]') : null;
      S.dialogs = []; S.blocked = []; S.armed = true;
    },
    disarm() { S.armed = false; },
    collect() {
      const gone = (e) => !!e && (!e.isConnected || !vis(e));
      return {
        dialogs: S.dialogs, blocked: S.blocked, url: location.href,
        target_removed: gone(S.target) || gone(S.row),
        modal: detectModal(),
      };
    },
  };
})();
