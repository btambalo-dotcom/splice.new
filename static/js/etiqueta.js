/* SPLICER v149/v150 — Impressão de etiquetas na NIIMBOT D110 (rolo contínuo 12 mm)
   - Só tela: não mexe em lançamentos, valores ou banco.
   - Imprime direto por Bluetooth (Web Bluetooth). No iPad/iPhone isso só funciona
     dentro do navegador Bluefy (o Safari não tem Bluetooth para sites).
   - Sem Bluetooth: gera a imagem da etiqueta para compartilhar com o app NIIMBOT.
   Biblioteca: niimbluelib (MIT) carregada do unpkg na primeira impressão. */
(function () {
  'use strict';

  const LIB_URL = 'https://unpkg.com/@mmote/niimbluelib@0.49.1/dist/umd/niimbluelib.min.js';
  const DOTS_PER_MM = 8;          // 203 dpi
  const HEAD_PX = 96;             // largura da cabeça da D110 = 12 mm
  const CFG_KEY = 'splicer_etiqueta_cfg';

  const DEFAULT_CFG = {
    paper: 'continuous',   // continuous | gaps
    lengthMm: 0,           // 0 = automático (só contínuo)
    density: 3,
    copies: 1,
    marginMm: 3,
  };

  let cfg = loadCfg();
  let client = null;
  let currentRec = null;

  // ---------- configuração ----------
  function loadCfg() {
    try { return Object.assign({}, DEFAULT_CFG, JSON.parse(localStorage.getItem(CFG_KEY) || '{}')); }
    catch (e) { return Object.assign({}, DEFAULT_CFG); }
  }
  function saveCfg() {
    try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); } catch (e) { /* sem storage */ }
  }

  // ---------- leitura do nome do dispositivo ----------
  // "OLT07_85P_6S_SBNGFL / OLT07_85P_7S_SBNGFL" -> [{olt:'OLT07', p:'85P', s:6}, ...]
  function parseSplitters(name) {
    return String(name || '')
      .split('/')
      .map((t) => t.trim())
      .filter(Boolean)
      .map((tok) => {
        const m = tok.match(/OLT\s*0*(\d+)[\s_-]+(\d+)\s*P(?:[\s_-]+(\d+)\s*S)?/i);
        if (!m) return { raw: tok };
        return {
          olt: 'OLT' + String(m[1]).padStart(2, '0'),
          p: m[2] + 'P',
          s: m[3] ? parseInt(m[3], 10) : null,
        };
      });
  }
  function ports(s) { return (4 * s - 3) + '-' + (4 * s); }
  function lineFull(sp) {      // "OLT07 85P 6S 21-24" (1x4) / "OLT07 84P" (1x8)
    if (sp.raw) return sp.raw;
    return sp.s ? `${sp.olt} ${sp.p} ${sp.s}S ${ports(sp.s)}` : `${sp.olt} ${sp.p}`;
  }
  function lineShort(sp) {     // "OLT07 85P 6S"
    if (sp.raw) return sp.raw;
    return sp.s ? `${sp.olt} ${sp.p} ${sp.s}S` : `${sp.olt} ${sp.p}`;
  }

  // v150 — cabo e fibras gravados na informação do pin ("CABO ... | FIBRAS ...")
  function cableInfo(rec) {
    const info = String((rec && rec.info) || '');
    const c = info.match(/CABO\s+([^|]+)/i);
    const f = info.match(/FIBRAS\s+([^|]+)/i);
    return { cabo: c ? c[1].trim() : '', fibras: f ? f[1].trim() : '' };
  }
  function shortFibras(f) {          // "OLT07_85P_SBNGFL 6-8; OLT07_86P_SBNGFL 1" -> ["OLT07 85P F6-8", ...]
    return String(f || '').split(';').map((t) => t.trim()).filter(Boolean).map((t) => {
      const m = t.match(/OLT\s*0*(\d+)(?:_(\d+P))?_SBNGFL\s+([\d-]+)/i);
      if (!m) return t;
      const olt = 'OLT' + String(m[1]).padStart(2, '0');
      return m[2] ? `${olt} ${m[2]} F${m[3]}` : `${olt} F${m[3]}`;
    });
  }

  function twoLines(items) {
    if (items.length <= 2) return items;
    const n1 = Math.floor(items.length / 2);
    return [items.slice(0, n1).join('/'), items.slice(n1).join('/')];
  }

  // Modelos (as opções das fotos de campo)
  function buildText(mode, rec) {
    const sps = parseSplitters(rec.device);
    if (mode === 'cabo') {               // nome do cabo + fibras usadas na caixa
      const ci = cableInfo(rec);
      if (!ci.cabo) return '';
      return [ci.cabo.replace(/\s+(\d+)$/, ' $1F')].concat(shortFibras(ci.fibras)).join('\n');
    }
    if (mode === 'tampa') {                // 1 etiqueta, no máximo 2 linhas (3+ splitters: "A" / "B/C", como na foto)
      return twoLines(sps.map(lineFull)).join('\n');
    }
    if (mode === 'completa') {             // etiqueta do cabo + etiqueta da tampa
      const cab = buildText('cabo', rec);
      const tam = buildText('tampa', rec);
      return [cab, tam].filter(Boolean).join('\n\n');
    }
    if (mode === 'splitter') {             // 1 etiqueta por splitter (bandeja)
      return sps.map(lineShort).join('\n\n');
    }
    if (mode === 'splitter_portas') {      // 1 etiqueta por splitter com portas
      return sps.map(lineFull).join('\n\n');
    }
    return (rec.device || '').replace(/\s*\/\s*/g, '\n');   // nome do dispositivo
  }

  // Texto -> lista de etiquetas (linha em branco separa etiquetas)
  function textToLabels(text) {
    return String(text || '')
      .split(/\n\s*\n/)
      .map((b) => b.split('\n').map((l) => l.trim()).filter(Boolean))
      .filter((l) => l.length);
  }

  // ---------- desenho ----------
  function renderLabel(lines) {
    const n = lines.length;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    const pad = 4;                                   // px acima/abaixo
    let font = Math.min(64, Math.floor((HEAD_PX - pad * 2) / n * 0.92));
    const fontStr = (px) => `bold ${px}px Arial, Helvetica, sans-serif`;
    ctx.font = fontStr(font);
    let textW = Math.max(...lines.map((l) => ctx.measureText(l).width));

    const margin = Math.round(cfg.marginMm * DOTS_PER_MM);
    let lengthPx;
    if (cfg.paper === 'gaps' || cfg.lengthMm > 0) {
      lengthPx = Math.round((cfg.lengthMm > 0 ? cfg.lengthMm : 40) * DOTS_PER_MM);
      const maxText = lengthPx - margin * 2;
      if (textW > maxText) {                         // encolhe a letra para caber
        font = Math.max(10, Math.floor(font * maxText / textW));
        ctx.font = fontStr(font);
        textW = Math.max(...lines.map((l) => ctx.measureText(l).width));
      }
    } else {
      lengthPx = Math.max(20 * DOTS_PER_MM, Math.ceil(textW) + margin * 2);
    }
    lengthPx = Math.ceil(lengthPx / 8) * 8;

    canvas.width = lengthPx;
    canvas.height = HEAD_PX;
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#000';
    ctx.font = fontStr(font);
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    const lineH = (HEAD_PX - pad * 2) / n;
    lines.forEach((l, i) => ctx.fillText(l, lengthPx / 2, pad + lineH * (i + 0.5)));
    return canvas;
  }

  function renderPreview() {
    const box = document.getElementById('etq-preview');
    if (!box) return;
    box.innerHTML = '';
    const labels = textToLabels(document.getElementById('etq-text').value);
    labels.forEach((lines) => {
      const c = renderLabel(lines);
      c.style.cssText = 'display:block;max-width:100%;height:auto;margin:0 auto 8px;border:1px dashed #888;background:#fff;image-rendering:pixelated;';
      box.appendChild(c);
    });
    const copies = Math.max(1, parseInt(cfg.copies, 10) || 1);
    document.getElementById('etq-count').textContent =
      labels.length ? `${labels.length} etiqueta(s) × ${copies} cópia(s)` : 'Nenhuma etiqueta';
  }

  // ---------- impressora ----------
  function hasBluetooth() { return !!(navigator.bluetooth && navigator.bluetooth.requestDevice); }

  function loadLib() {
    if (window.niimbluelib) return Promise.resolve(window.niimbluelib);
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = LIB_URL;
      s.onload = () => (window.niimbluelib ? resolve(window.niimbluelib) : reject(new Error('Biblioteca não carregou')));
      s.onerror = () => reject(new Error('Sem internet para carregar a biblioteca da impressora'));
      document.head.appendChild(s);
    });
  }

  function setStatus(msg, kind) {
    const el = document.getElementById('etq-status');
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'small mt-2 ' + (kind === 'err' ? 'text-danger' : kind === 'ok' ? 'text-success' : 'text-info');
  }

  async function connect() {
    const lib = await loadLib();
    if (client && client.isConnected && client.isConnected()) return client;
    client = new lib.NiimbotBluetoothClient();
    client.on('disconnect', () => { updateConnLabel(); });
    await client.connect();
    updateConnLabel();
    return client;
  }

  function updateConnLabel() {
    const el = document.getElementById('etq-conn');
    if (!el) return;
    const ok = client && client.isConnected && client.isConnected();
    let name = '';
    try { const m = ok && client.getModelMetadata(); name = m ? m.model : ''; } catch (e) { /* */ }
    el.textContent = ok ? `Conectada ${name}` : 'Desconectada';
    el.className = 'badge ' + (ok ? 'bg-success' : 'bg-secondary');
  }

  async function printAll() {
    const labels = textToLabels(document.getElementById('etq-text').value);
    if (!labels.length) { setStatus('Escreva o texto da etiqueta.', 'err'); return; }
    if (!hasBluetooth()) { noBluetoothHelp(); return; }

    const btn = document.getElementById('etq-print');
    btn.disabled = true;
    try {
      setStatus('Conectando na impressora...');
      const c = await connect();
      const lib = window.niimbluelib;
      const taskName = c.getPrintTaskType() || 'D110';
      const copies = Math.max(1, parseInt(cfg.copies, 10) || 1);
      const labelType = cfg.paper === 'gaps' ? lib.LabelType.WithGaps : lib.LabelType.Continuous;
      let meta = null;
      try { meta = c.getModelMetadata(); } catch (e) { /* */ }
      const dir = (meta && meta.printDirection) || 'left';

      for (let i = 0; i < labels.length; i++) {
        setStatus(`Imprimindo ${i + 1}/${labels.length}...`);
        const encoded = lib.ImageEncoder.encodeCanvas(renderLabel(labels[i]), 0, dir);
        const task = c.protocol.newPrintTask(taskName, {
          totalPages: copies,
          labelType,
          density: parseInt(cfg.density, 10) || 3,
          statusPollIntervalMs: 100,
          statusTimeoutMs: 8000,
        });
        try {
          await task.printInit();
          await task.printPage(encoded, copies);
          await task.waitForPageFinished();
          await task.waitForFinished();
        } finally {
          try { await task.printEnd(); } catch (e) { /* */ }
        }
      }
      setStatus('Pronto! Etiqueta(s) impressa(s).', 'ok');
    } catch (e) {
      console.error('[etiqueta]', e);
      setStatus('Erro: ' + (e && e.message ? e.message : e), 'err');
    } finally {
      btn.disabled = false;
      updateConnLabel();
    }
  }

  function noBluetoothHelp() {
    setStatus('Este navegador não tem Bluetooth. No iPad abra o SPLICER no app Bluefy para imprimir direto — ou use "Compartilhar imagem" e imprima pelo app NIIMBOT.', 'err');
  }

  // Plano B: imagem PNG para o app NIIMBOT (ou salvar)
  async function shareImage() {
    const labels = textToLabels(document.getElementById('etq-text').value);
    if (!labels.length) { setStatus('Escreva o texto da etiqueta.', 'err'); return; }
    const files = [];
    for (let i = 0; i < labels.length; i++) {
      const canvas = renderLabel(labels[i]);
      const blob = await new Promise((r) => canvas.toBlob(r, 'image/png'));
      const nome = labels[i][0].replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 40) || 'etiqueta';
      files.push(new File([blob], `${nome}.png`, { type: 'image/png' }));
    }
    try {
      if (navigator.canShare && navigator.canShare({ files })) {
        await navigator.share({ files, title: 'Etiqueta' });
        return;
      }
    } catch (e) { if (e && e.name === 'AbortError') return; }
    files.forEach((f) => {                 // sem compartilhar: baixa os arquivos
      const a = document.createElement('a');
      a.href = URL.createObjectURL(f);
      a.download = f.name;
      document.body.appendChild(a); a.click(); a.remove();
    });
  }

  // ---------- tela ----------
  function syncCfgInputs() {
    document.getElementById('etq-paper').value = cfg.paper;
    document.getElementById('etq-length').value = String(cfg.lengthMm);
    document.getElementById('etq-density').value = String(cfg.density);
    document.getElementById('etq-copies').value = String(cfg.copies);
  }

  function applyMode() {
    const mode = document.getElementById('etq-mode').value;
    document.getElementById('etq-text').value = buildText(mode, currentRec || {});
    renderPreview();
  }

  window.openLabelPrint = function (rec) {
    currentRec = rec || null;
    if (!currentRec) {
      // aberto pela janela do dispositivo: usa o que está nos campos (mesmo sem salvar)
      const id = document.getElementById('device-record-id').value;
      const all = window._mapRecords || window.allRecords || [];
      currentRec = Object.assign({}, all.find((r) => String(r.id) === String(id)) || {});
      const nameField = document.getElementById('device-name');
      if (nameField && nameField.value) currentRec.device = nameField.value;
      const inf = document.getElementById('device-info');
      if (inf && inf.value) currentRec.info = inf.value;
    }
    document.getElementById('etq-device').textContent = currentRec.device || '';
    syncCfgInputs();
    const sps = parseSplitters(currentRec.device);
    const has1x4 = sps.some((s) => s.s);
    const hasCabo = !!cableInfo(currentRec).cabo;
    document.getElementById('etq-mode').value = hasCabo ? 'completa' : (has1x4 ? 'tampa' : 'splitter');
    applyMode();
    setStatus(hasBluetooth() ? '' : 'Sem Bluetooth neste navegador (no iPad use o Bluefy) — dá para usar "Compartilhar imagem".', hasBluetooth() ? '' : 'err');
    updateConnLabel();

    const dev = bootstrap.Modal.getInstance(document.getElementById('deviceModal'));
    if (dev) dev.hide();
    bootstrap.Modal.getOrCreateInstance(document.getElementById('labelModal')).show();
  };

  // v154 — botão "Etiqueta" direto no balão do pin
  window.printLabelFromMap = function (id) {
    const all = window._mapRecords || [];
    const rec = all.find((r) => String(r.id) === String(id));
    try { if (window.leafletMap) window.leafletMap.closePopup(); } catch (e) { /* */ }
    window.openLabelPrint(rec || null);
  };

  document.addEventListener('DOMContentLoaded', () => {
    const $ = (id) => document.getElementById(id);
    if (!$('labelModal')) return;
    $('etq-mode').addEventListener('change', applyMode);
    $('etq-text').addEventListener('input', renderPreview);
    $('etq-paper').addEventListener('change', (e) => { cfg.paper = e.target.value; if (cfg.paper === 'gaps' && !cfg.lengthMm) { cfg.lengthMm = 40; $('etq-length').value = '40'; } saveCfg(); renderPreview(); });
    $('etq-length').addEventListener('change', (e) => { cfg.lengthMm = parseInt(e.target.value, 10) || 0; saveCfg(); renderPreview(); });
    $('etq-density').addEventListener('change', (e) => { cfg.density = parseInt(e.target.value, 10) || 3; saveCfg(); });
    $('etq-copies').addEventListener('change', (e) => { cfg.copies = Math.max(1, parseInt(e.target.value, 10) || 1); saveCfg(); renderPreview(); });
    $('etq-print').addEventListener('click', printAll);
    $('etq-share').addEventListener('click', shareImage);
    $('etq-connect').addEventListener('click', async () => {
      if (!hasBluetooth()) { noBluetoothHelp(); return; }
      try { setStatus('Procurando impressora...'); await connect(); setStatus('Impressora conectada.', 'ok'); }
      catch (e) { setStatus('Erro: ' + (e && e.message ? e.message : e), 'err'); }
    });
  });
})();
