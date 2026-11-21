/* SPLICER v169 — impressão ESC/POS por Bluetooth (impressoras tipo POTONO, 58/80 mm).
   Separado do Niimbot (etiqueta.js). Só tela: não mexe em banco/lançamentos.
   Web Bluetooth (no iPad: app Bluefy). Conecta e imprime na mesma ação para não cair. */
(function () {
  'use strict';

  // serviços/caractrísticas comuns dessas impressoras térmicas BLE
  const SERVICES = [
    '000018f0-0000-1000-8000-00805f9b34fb',
    '0000ff00-0000-1000-8000-00805f9b34fb',
    '0000ae30-0000-1000-8000-00805f9b34fb',
    '0000ff e0-0000-1000-8000-00805f9b34fb'.replace(' ', ''),
    '6e400001-b5a3-f393-e0a9-e50e24dcca9e',
    '49535343-fe7d-4ae5-8fa9-9fafd205e455',
  ];
  const WRITE_CHARS = [
    '00002af1-0000-1000-8000-00805f9b34fb',
    '0000ff02-0000-1000-8000-00805f9b34fb',
    '0000ae01-0000-1000-8000-00805f9b34fb',
    '0000ffe1-0000-1000-8000-00805f9b34fb',
    '6e400002-b5a3-f393-e0a9-e50e24dcca9e',
    '49535343-8841-43f4-a8d4-ecbe34729bb3',
  ];

  let device = null, server = null, ch = null, withResp = false;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function connected() { return !!(server && server.connected && ch); }

  async function pickChar() {
    const svcs = await server.getPrimaryServices();
    for (const s of svcs) {
      let chars;
      try { chars = await s.getCharacteristics(); } catch (e) { continue; }
      // 1º: um writable conhecido
      for (const c of chars) {
        if (WRITE_CHARS.includes(c.uuid) && (c.properties.write || c.properties.writeWithoutResponse)) {
          withResp = !c.properties.writeWithoutResponse; return c;
        }
      }
      // 2º: qualquer writable
      for (const c of chars) {
        if (c.properties.writeWithoutResponse) { withResp = false; return c; }
        if (c.properties.write) { withResp = true; return c; }
      }
    }
    return null;
  }

  async function connect(all) {
    if (connected()) return;
    const opts = all
      ? { acceptAllDevices: true, optionalServices: SERVICES }
      : { filters: SERVICES.map((s) => ({ services: [s] })), optionalServices: SERVICES };
    device = await navigator.bluetooth.requestDevice(opts);
    device.addEventListener('gattserverdisconnected', () => { server = null; ch = null; });
    server = await device.gatt.connect();
    ch = await pickChar();
    if (!ch) { try { device.gatt.disconnect(); } catch (e) {} throw new Error('Impressora conectou mas não achei por onde enviar (característica de escrita).'); }
  }

  async function write(bytes) {
    const CH = 180;
    for (let i = 0; i < bytes.length; i += CH) {
      const part = bytes.slice(i, i + CH);
      if (withResp) await ch.writeValueWithResponse(part);
      else { await ch.writeValueWithoutResponse(part); await sleep(8); }
    }
  }

  // canvas -> raster ESC/POS (GS v 0). Preto = bit 1. Ajusta à largura da cabeça.
  function raster(canvas, widthDots) {
    let w = canvas.width, h = canvas.height;
    let cv = canvas;
    if (w > widthDots) {                      // reduz para caber na largura
      const sc = widthDots / w;
      cv = document.createElement('canvas');
      cv.width = Math.floor(w * sc); cv.height = Math.floor(h * sc);
      const cx = cv.getContext('2d'); cx.fillStyle = '#fff'; cx.fillRect(0, 0, cv.width, cv.height);
      cx.drawImage(canvas, 0, 0, cv.width, cv.height);
      w = cv.width; h = cv.height;
    }
    const bpr = Math.ceil(w / 8);
    const ctx = cv.getContext('2d');
    const px = ctx.getImageData(0, 0, w, h).data;
    const out = new Uint8Array(bpr * h);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const a = px[i + 3];
        const lum = a < 128 ? 255 : (px[i] * 0.299 + px[i + 1] * 0.587 + px[i + 2] * 0.114);
        if (lum < 128) out[y * bpr + (x >> 3)] |= (0x80 >> (x & 7));
      }
    }
    const head = new Uint8Array([0x1d, 0x76, 0x30, 0x00, bpr & 0xff, (bpr >> 8) & 0xff, h & 0xff, (h >> 8) & 0xff]);
    const buf = new Uint8Array(head.length + out.length);
    buf.set(head, 0); buf.set(out, head.length);
    return buf;
  }

  function concat(arrs) {
    const n = arrs.reduce((s, a) => s + a.length, 0);
    const o = new Uint8Array(n); let p = 0;
    for (const a of arrs) { o.set(a, p); p += a.length; }
    return o;
  }

  async function printCanvas(canvas, widthDots) {
    const init = new Uint8Array([0x1b, 0x40]);          // ESC @
    const alignC = new Uint8Array([0x1b, 0x61, 0x01]);  // centralizar
    const feed = new Uint8Array([0x1b, 0x64, 0x03]);    // 3 linhas
    await write(concat([init, alignC, raster(canvas, widthDots), feed]));
  }

  async function printTest(widthDots) {
    const cv = document.createElement('canvas');
    cv.width = Math.min(384, widthDots); cv.height = 90;
    const x = cv.getContext('2d');
    x.fillStyle = '#fff'; x.fillRect(0, 0, cv.width, cv.height);
    x.fillStyle = '#000'; x.font = 'bold 34px Arial'; x.textBaseline = 'middle'; x.textAlign = 'center';
    x.fillText('SPLICER OK', cv.width / 2, 30);
    x.font = '22px Arial'; x.fillText('teste POTONO', cv.width / 2, 64);
    await printCanvas(cv, widthDots);
  }

  window.Escpos = { connect, connected, printCanvas, printTest };
})();
