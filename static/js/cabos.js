/* v162 — Cabo passado no mapa
   Verde = cabo passado, vermelho = falta passar. A Construção toca no trecho para marcar.
   Em cima do mapa: quem fez a última atualização e quando. */
(function () {
  const MAP_ID = window.CABOS_MAP_ID;
  const API = '/api/maps/' + MAP_ID + '/cables';
  let layer = null, data = null, on = false, addMode = false, addFirst = null, busy = false;
  const sel = new Set();   // trechos selecionados (só lança depois de confirmar)
  const fmtDT = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  };
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const lm = () => (typeof leafletMap !== 'undefined' ? leafletMap : null);
  const recs = () => window._mapRecords || [];
  const recById = (id) => recs().find((r) => r.id === id);
  const hav = (a, b) => {
    const R = 6371000, t = Math.PI / 180;
    const dp = (b[0] - a[0]) * t, dl = (b[1] - a[1]) * t;
    const x = Math.sin(dp / 2) ** 2 + Math.cos(a[0] * t) * Math.cos(b[0] * t) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(x)));
  };
  const lenOf = (c) => { let s = 0; for (let i = 1; i < c.length; i++) s += hav(c[i - 1], c[i]); return s; };
  const ft = (m) => Math.round(m * 3.28084).toLocaleString('pt-BR') + ' ft';

  // ---------- banner / barra ----------
  function banner() {
    let b = document.getElementById('cabos-banner');
    if (!b) {
      b = document.createElement('div');
      b.id = 'cabos-banner';
      b.style.cssText = 'position:absolute;top:8px;left:56px;right:8px;z-index:800;background:rgba(17,24,39,.9);color:#fff;border:1px solid #374151;border-radius:10px;padding:4px 8px;font-size:12px;box-shadow:0 4px 14px #0006;line-height:1.3;max-width:520px;margin-left:auto';
      document.querySelector('.mv-map-wrap').appendChild(b);
    }
    return b;
  }
  let expanded = false;
  window.cabosExpand = function () { expanded = !expanded; renderBanner(); };
  function renderBanner() {
    const b = banner();
    if (!on || !data) { b.style.display = 'none'; return; }
    const segs = data.segments;
    const done = segs.filter((s) => s.status === 'done');
    const mDone = done.reduce((a, s) => a + (s.length_m || 0), 0);
    const mAll = segs.reduce((a, s) => a + (s.length_m || 0), 0);
    const pct = mAll ? Math.round(100 * mDone / mAll) : 0;
    const last = data.last;
    const btn = (cls, js, txt) => '<button type="button" class="btn btn-sm ' + cls + '" style="padding:2px 8px;font-size:12px;line-height:1.3" onclick="' + js + '">' + txt + '</button>';
    // linha compacta (não cobre o mapa no celular)
    let h = '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
      '<span>🧵 <b>' + done.length + '/' + segs.length + '</b> · <b style="color:#4ade80 !important">' + pct + '%</b></span>' +
      '<span style="color:#d1d5db !important">· ' + (last ? esc(last.by || '?') + ' ' + fmtDT(last.at).slice(0, 16) : 'sem atualização') + '</span>' +
      '<span style="flex:1"></span>';
    if (data.can_edit && !sel.size) h += btn(addMode ? 'btn-warning' : 'btn-outline-light', 'window.cabosAddMode()', addMode ? '✖' : '➕');
    h += btn('btn-outline-secondary', 'window.cabosExpand()', expanded ? '▴' : '▾') + '</div>';
    if (data.can_edit && sel.size) {
      const segsSel = data.segments.filter((x) => sel.has(x.id));
      const nDone = segsSel.filter((x) => x.status !== 'done').length, nUndo = segsSel.length - nDone;
      h += '<div style="margin-top:5px;padding:5px 6px;border-radius:9px;background:#422006;border:1px solid #f59e0b;display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
        '<span style="color:#fde68a !important"><b>' + sel.size + '</b> selec.' + (nDone ? ' · ' + nDone + ' ✅' : '') + (nUndo ? ' · ' + nUndo + ' ↩️' : '') + '</span><span style="flex:1"></span>' +
        btn('btn-success', 'window.cabosLancar()', '✅ Lançar') + btn('btn-outline-light', 'window.cabosLimpar()', '✖') +
        (data.is_admin ? btn('btn-outline-danger', 'window.cabosApagarSel()', '🗑️') : '') + '</div>';
    }
    if (addMode) h += '<div style="color:#fbbf24 !important;margin-top:4px">' + (addFirst ? 'Toque onde o trecho TERMINA' : 'Toque onde o trecho COMEÇA') + '</div>';
    if (expanded) {
      h += '<div style="margin-top:5px;text-align:left;line-height:1.4">' +
        '<b style="color:#4ade80 !important">━ verde</b> = passado · <b style="color:#f87171 !important">┅ vermelho</b> = falta<br>' +
        (segs.length ? ft(mDone) + ' de ' + ft(mAll) : 'Nenhum trecho cadastrado ainda.') + '<br>' +
        'Última atualização: ' + (last ? '<b>' + esc(last.by || '?') + '</b> · ' + fmtDT(last.at) : 'nenhuma') +
        (data.can_edit ? '<br><span style="color:#9ca3af !important">Toque nos trechos para selecionar e depois em ✅ Lançar.</span>' : '') +
        (data.is_admin ? '<div style="margin-top:5px">' + btn('btn-outline-info', 'window.cabosGerar()', '⚙️ Gerar trechos dos cabos') + '</div>' : '') +
        '</div>';
    }
    b.innerHTML = h;
    b.style.display = '';
  }

  // ---------- desenho ----------
  function segPopup(s) {
    const a = s.a && recById(s.a), b = s.b && recById(s.b);
    let h = '<div style="min-width:210px"><b>' + (s.status === 'done' ? '🟢 Cabo passado' : '🔴 Falta passar') + '</b>';
    if (s.cable) h += '<br>🧵 ' + esc(s.cable);
    if (a || b) h += '<br><span style="font-size:12px">' + esc(a ? a.device : '?') + ' → ' + esc(b ? b.device : '?') + '</span>';
    h += '<br>📏 ' + ft(s.length_m || 0);
    if (s.updated_at) h += '<br><span style="font-size:12px">Atualizado por <b>' + esc(s.updated_by || '?') + '</b> em ' + fmtDT(s.updated_at) + '</span>';
    if (data && data.can_edit) {
      h += '<div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap">';
      if (s.status === 'done') h += '<button class="btn btn-sm btn-outline-danger" onclick="window.cabosSet(' + s.id + ',\'pending\')">↩️ Não passado</button>';
      else h += '<button class="btn btn-sm btn-success" onclick="window.cabosSet(' + s.id + ',\'done\')">✅ Cabo passado</button>';
      if (data.is_admin) h += '<button class="btn btn-sm btn-outline-secondary" onclick="window.cabosDel(' + s.id + ')">🗑️</button>';
      h += '</div>';
    }
    return h + '</div>';
  }
  function draw() {
    const m = lm(); if (!m) return;
    if (!layer) layer = L.layerGroup();
    layer.clearLayers();
    if (!on || !data) { if (m.hasLayer(layer)) m.removeLayer(layer); renderBanner(); return; }
    // vermelhos embaixo, verdes por cima
    const segs = data.segments.slice().sort((x, y) => (sel.has(x.id) ? 1 : 0) - (sel.has(y.id) ? 1 : 0) || (x.status === 'done') - (y.status === 'done'));
    for (const s of segs) {
      if (!s.coords || s.coords.length < 2) continue;
      // selecionado já aparece na cor nova (verde), mas só grava depois de ✅ Lançar
      const done = sel.has(s.id) ? s.status !== 'done' : s.status === 'done';
      const color = done ? '#22c55e' : '#ef4444';
      // linha grossa invisível para facilitar o toque no celular
      const isSel = sel.has(s.id);
      const hit = L.polyline(s.coords, { color: '#000', opacity: 0.01, weight: 24 });
      const casing = L.polyline(s.coords, { color: isSel ? '#facc15' : '#111', opacity: isSel ? 0.9 : 0.55, weight: isSel ? 11 : 9, interactive: false });
      const line = L.polyline(s.coords, { color, opacity: 0.95, weight: 6, dashArray: done ? null : '10 7' });
      if (data.can_edit) {
        [hit, line].forEach((pl) => pl.on('click', (ev) => {
          if (addMode) return;
          L.DomEvent.stop(ev);
          if (sel.has(s.id)) sel.delete(s.id); else sel.add(s.id);
          draw();
        }));
      } else {
        [hit, line].forEach((pl) => pl.bindPopup(() => segPopup(s)));
      }
      casing.addTo(layer); line.addTo(layer); hit.addTo(layer);
    }
    if (!m.hasLayer(layer)) layer.addTo(m);
    ponAreas(false);
    renderBanner();
  }
  async function load() {
    try {
      const r = await fetch(API, { cache: 'no-store' });
      data = await r.json();
    } catch (e) { data = data || { segments: [], last: null, can_edit: false }; }
    draw();
  }

  // ---------- ações ----------
  window.cabosSet = async function (id, status) {
    if (busy) return; busy = true;
    try {
      const r = await fetch('/api/cables/' + id + '/status', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
      const d = await r.json();
      if (!d.ok) { alert(d.error || 'Erro'); return; }
      const i = data.segments.findIndex((s) => s.id === id);
      if (i >= 0) data.segments[i] = d.segment;
      data.last = { by: d.segment.updated_by, at: d.segment.updated_at };
      if (lm()) lm().closePopup();
      draw();
    } catch (e) { alert('Sem conexão. Tente de novo.'); }
    finally { busy = false; }
  };
  // confirmação na própria tela (o pessoal pode tocar errado)
  function ask(html, okTxt) {
    return new Promise((res) => {
      const o = document.createElement('div');
      o.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;padding:16px';
      o.innerHTML = '<div style="background:#111827;color:#fff;border:1px solid #374151;border-radius:14px;max-width:420px;width:100%;padding:16px;font-size:15px;line-height:1.4">' +
        html + '<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">' +
        '<button type="button" class="btn btn-outline-light" data-r="0">Cancelar</button>' +
        '<button type="button" class="btn btn-success" data-r="1">' + okTxt + '</button></div></div>';
      o.addEventListener('click', (e) => {
        const r = e.target.getAttribute && e.target.getAttribute('data-r');
        if (r !== null && r !== undefined) { o.remove(); res(r === '1'); }
      });
      document.body.appendChild(o);
    });
  }
  window.cabosLimpar = function () { sel.clear(); draw(); };
  window.cabosLancar = async function () {
    if (!sel.size || busy) return;
    const segsSel = data.segments.filter((x) => sel.has(x.id));
    const toDone = segsSel.filter((x) => x.status !== 'done'), toPend = segsSel.filter((x) => x.status === 'done');
    const mDone = toDone.reduce((a, x) => a + (x.length_m || 0), 0);
    let h = '<b>Confirmar lançamento?</b><br>';
    if (toDone.length) h += '<br>🟢 <b>' + toDone.length + '</b> trecho(s) como <b>CABO PASSADO</b> (' + ft(mDone) + ')';
    if (toPend.length) h += '<br>🔴 <b>' + toPend.length + '</b> trecho(s) voltam para <b>NÃO PASSADO</b>';
    h += '<br><br><span style="color:#9ca3af;font-size:13px">Confira no mapa: os trechos selecionados estão com borda amarela. Se cancelar, nada é gravado.</span>';
    if (!(await ask(h, '✅ Sim, lançar'))) return;
    busy = true;
    try {
      const r = await fetch(API + '/status-bulk', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ done: toDone.map((x) => x.id), pending: toPend.map((x) => x.id) }) });
      const d = await r.json();
      if (!d.ok) { alert(d.error || 'Erro'); return; }
      for (const sg of d.segments) { const i = data.segments.findIndex((x) => x.id === sg.id); if (i >= 0) data.segments[i] = sg; }
      if (d.last) data.last = d.last;
      sel.clear();
      draw();
    } catch (e) { alert('Sem conexão. Nada foi lançado — tente de novo.'); }
    finally { busy = false; }
  };
  window.cabosApagarSel = async function () {
    if (!sel.size) return;
    if (!(await ask('<b>Apagar ' + sel.size + ' trecho(s) do mapa?</b><br><span style="color:#9ca3af;font-size:13px">Use só para trecho que não é caminho do cabo.</span>', '🗑️ Apagar'))) return;
    for (const id of Array.from(sel)) {
      const r = await fetch('/api/cables/' + id + '/delete', { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (d.ok) { data.segments = data.segments.filter((x) => x.id !== id); sel.delete(id); }
    }
    draw();
  };
  window.cabosDel = async function (id) {
    if (!confirm('Apagar este trecho do mapa?')) return;
    const r = await fetch('/api/cables/' + id + '/delete', { method: 'POST' });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) { alert(d.error || 'Erro'); return; }
    data.segments = data.segments.filter((s) => s.id !== id);
    if (lm()) lm().closePopup();
    draw();
  };

  // rota pela rua (OSRM a pé); se a rota ficar estranha, usa linha reta
  async function route(a, b) {
    const straight = [a, b];
    try {
      const u = 'https://routing.openstreetmap.de/routed-foot/route/v1/driving/' + a[1] + ',' + a[0] + ';' + b[1] + ',' + b[0] + '?overview=full&geometries=geojson';
      const r = await fetch(u);
      const j = await r.json();
      const g = j && j.routes && j.routes[0] && j.routes[0].geometry && j.routes[0].geometry.coordinates;
      if (!g || g.length < 2) return straight;
      const pts = g.map((p) => [p[1], p[0]]);
      const d0 = hav(a, b), dr = lenOf(pts);
      if (dr > d0 * 2.2 + 60) return straight;          // desvio grande: rua não existe no mapa base
      // liga as pontas certinho nas caixas
      if (hav(a, pts[0]) > 3) pts.unshift(a);
      if (hav(b, pts[pts.length - 1]) > 3) pts.push(b);
      return pts;
    } catch (e) { return straight; }
  }

  // trecho novo: toca em 2 caixas (ou 2 pontos no mapa)
  window.cabosAddMode = function () {
    addMode = !addMode; addFirst = null;
    renderBanner();
  };
  function nearestRec(ll) {
    let best = null, bd = 1e9;
    for (const r of recs()) {
      if (r.lat == null || r.lng == null) continue;
      const d = hav([ll.lat, ll.lng], [r.lat, r.lng]);
      if (d < bd) { bd = d; best = r; }
    }
    return bd <= 25 ? best : null;
  }
  async function addPoint(pt, rec) {
    if (!addFirst) { addFirst = { pt, rec }; renderBanner(); return; }
    const first = addFirst; addFirst = null; addMode = false;
    renderBanner();
    const b = banner(); b.insertAdjacentHTML('beforeend', '<div style="color:#93c5fd">Traçando pela rua…</div>');
    const coords = await route(first.pt, pt);
    const cable = (rec && cableOf(rec)) || (first.rec && cableOf(first.rec)) || '';
    const r = await fetch(API + '/add', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ a: first.rec ? first.rec.id : null, b: rec ? rec.id : null, coords, cable, status: 'pending' })
    });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) { alert(d.error || 'Erro'); renderBanner(); return; }
    if (d.created && d.created.length) {
      data.segments.push(...d.created);
      d.created.forEach((x) => sel.add(x.id));   // entra selecionado: só fica verde depois de ✅ Lançar
    } else alert('Esse trecho já existe.');
    draw();
  }
  function hookClicks() {
    const m = lm(); if (!m) return;
    m.on('click', (e) => { if (!addMode) return; setTimeout(() => { try { m.closePopup(); } catch (x) {} }, 0); addPoint([e.latlng.lat, e.latlng.lng], nearestRec(e.latlng)); });
    if (typeof markersLayer !== 'undefined') {
      markersLayer.on('click', (e) => {
        if (!addMode) return;
        const ll = e.latlng || (e.layer && e.layer.getLatLng && e.layer.getLatLng());
        const rec = nearestRec(ll) ;
        setTimeout(() => { try { m.closePopup(); } catch (x) {} }, 0);
        addPoint(rec ? [rec.lat, rec.lng] : [ll.lat, ll.lng], rec);
      });
    }
  }

  // ---------- gerar trechos planejados (admin): liga as caixas de cada cabo pela menor distância ----------
  function cableOf(r) {
    const m = String(r.info || '').match(/CABO\s+([^|]+)/i);
    return m ? m[1].trim() : '';
  }
  window.cabosGerar = async function () {
    const rs = recs().filter((r) => r.lat != null && r.lng != null && r.is_active !== false);
    const groups = {};
    for (const r of rs) {
      const k = cableOf(r) || (r.pon_name ? 'PON ' + r.pon_name : 'MAPA');
      (groups[k] = groups[k] || []).push(r);
    }
    const edges = [];
    for (const [k, g] of Object.entries(groups)) {
      if (g.length < 2) continue;
      // árvore de menor distância (Prim)
      const inT = new Set([0]);
      while (inT.size < g.length) {
        let best = null;
        for (const i of inT) for (let j = 0; j < g.length; j++) {
          if (inT.has(j)) continue;
          const d = hav([g[i].lat, g[i].lng], [g[j].lat, g[j].lng]);
          if (!best || d < best.d) best = { i, j, d };
        }
        inT.add(best.j);
        edges.push({ a: g[best.i], b: g[best.j], cable: k.startsWith('PON ') || k === 'MAPA' ? '' : k, d: best.d });
      }
    }
    const have = new Set(data.segments.filter((s) => s.a && s.b).map((s) => Math.min(s.a, s.b) + '-' + Math.max(s.a, s.b)));
    const todo = edges.filter((e) => e.d > 0.5 && !have.has(Math.min(e.a.id, e.b.id) + '-' + Math.max(e.a.id, e.b.id)));
    if (!todo.length) { alert('Todos os trechos já existem.'); return; }
    if (!confirm('Criar ' + todo.length + ' trecho(s) planejado(s) (vermelho) ligando as caixas de cada cabo pela rua?')) return;
    const b = banner(); const out = [];
    for (let i = 0; i < todo.length; i++) {
      b.innerHTML = '⚙️ Traçando trechos pela rua… ' + (i + 1) + ' de ' + todo.length;
      const e = todo[i];
      const coords = await route([e.a.lat, e.a.lng], [e.b.lat, e.b.lng]);
      out.push({ a: e.a.id, b: e.b.id, coords, cable: e.cable, status: 'pending' });
      await new Promise((r) => setTimeout(r, 350));
    }
    const r = await fetch(API + '/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ segments: out }) });
    const d = await r.json().catch(() => ({}));
    if (!d.ok) { alert(d.error || 'Erro'); renderBanner(); return; }
    data.segments.push(...(d.created || []));
    draw();
    alert((d.created || []).length + ' trecho(s) criado(s). Confira e apague (🗑️) o que não for caminho do cabo.');
  };

  // ---------- liga/desliga ----------
  // com os cabos ligados, as áreas coloridas de PON somem (atrapalham marcar no celular)
  function ponAreas(show) {
    window.CABOS_ON = !show;
    if (typeof markersLayer === 'undefined') return;
    markersLayer.eachLayer((l) => {
      if (l instanceof L.Path && !(l instanceof L.CircleMarker)) {   // área + contorno colorido da PON
        if (!l._cabosOrig) l._cabosOrig = { opacity: l.options.opacity, fillOpacity: l.options.fillOpacity };
        l.setStyle(show ? l._cabosOrig : { opacity: 0, fillOpacity: 0 });
        const el = l.getElement && l.getElement(); if (el) el.style.pointerEvents = show ? '' : 'none';
      }
    });
  }
  window.cabosToggle = function (force) {
    on = typeof force === 'boolean' ? force : !on;
    ponAreas(!on);
    const btn = document.getElementById('cabosBtn');
    if (btn) btn.classList.toggle('active', on);
    if (on) load(); else { addMode = false; sel.clear(); draw(); }
  };
  function start() {
    if (!lm()) { setTimeout(start, 300); return; }
    hookClicks();
    if (window.CABOS_AUTO_ON) window.cabosToggle(true);
    setInterval(() => { if (on && !document.hidden && !busy && !addMode && !sel.size) load(); }, 60000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
