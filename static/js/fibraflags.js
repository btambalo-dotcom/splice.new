// v168 — caixas feitas com fibra errada (só admin). Marca vermelha no mapa + "✅ Resolvido".
(function () {
  const MID = window.CABOS_MAP_ID;
  let layer = null, flags = [], showResolved = false, on = false;
  const esc = (x) => String(x == null ? '' : x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const btn = () => document.getElementById('fflagBtn');
  function icon(res) {
    return L.divIcon({ className: '', iconSize: [34, 34], iconAnchor: [17, 40],
      html: '<div style="width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:19px;' +
        (res ? 'background:#16a34a;border:3px solid #fff;' : 'background:#dc2626;border:3px solid #fde047;box-shadow:0 0 0 4px rgba(220,38,38,.35);') +
        '">' + (res ? '✅' : '⚠️') + '</div>' });
  }
  function popup(f) {
    const d = f.resolved_at ? new Date(f.resolved_at).toLocaleString('pt-BR') : '';
    return '<div style="font-size:14px;max-width:260px"><b style="color:#dc2626">⚠️ Fibra errada</b><br><b>' + esc(f.device) + '</b><br>' +
      esc(f.note).replace(/\n/g, '<br>') +
      (f.resolved_at ? '<div style="color:#16a34a;margin-top:4px">✅ Resolvido por ' + esc(f.resolved_by) + ' · ' + esc(d) + '</div>' +
        '<button class="btn btn-sm btn-outline-secondary mt-2" onclick="fflagResolve(' + f.id + ',true)">↩ Reabrir</button>'
        : '<button class="btn btn-sm btn-success mt-2" onclick="fflagResolve(' + f.id + ')">✅ Marcar como resolvido</button>') + '</div>';
  }
  function draw() {
    if (layer) { layer.remove(); layer = null; }
    const open = flags.filter((f) => !f.resolved_at).length;
    const b = btn(); if (b) { b.textContent = '⚠️ Fibras (' + open + ')'; b.style.display = flags.length ? '' : 'none'; b.classList.toggle('active', on); }
    if (!on || typeof leafletMap === 'undefined' || !leafletMap) return;
    layer = L.layerGroup().addTo(leafletMap);
    flags.forEach((f) => {
      if (f.lat == null || (f.resolved_at && !showResolved)) return;
      L.marker([f.lat, f.lng], { icon: icon(!!f.resolved_at), zIndexOffset: 2000 }).bindPopup(popup(f)).addTo(layer);
    });
  }
  async function load() {
    try {
      const j = await fetch('/api/maps/' + MID + '/fiber-flags').then((r) => r.json());
      flags = j.flags || [];
      if (flags.some((f) => !f.resolved_at)) on = true;
      draw();
    } catch (e) {}
  }
  window.fflagToggle = function () {
    if (!on) { on = true; showResolved = false; }
    else if (!showResolved && flags.some((f) => f.resolved_at)) { showResolved = true; }
    else { on = false; showResolved = false; }
    draw();
    const b = btn(); if (b) b.title = !on ? 'Mostrar caixas com fibra errada' : showResolved ? 'Mostrando também as resolvidas' : 'Mostrando só as pendentes (toque de novo p/ ver resolvidas)';
  };
  window.fflagResolve = async function (id, undo) {
    if (!undo && !confirm('Marcar esta caixa como resolvida?')) return;
    const r = await fetch('/api/fiber-flags/' + id + '/resolve', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ undo: !!undo }) }).then((x) => x.json());
    if (r.ok) { flags = flags.map((f) => (f.id === id ? r.flag : f)); if (leafletMap) leafletMap.closePopup(); draw(); }
  };
  const start = () => setTimeout(load, 1500);
  if (document.readyState === 'complete') start(); else window.addEventListener('load', start);
})();
