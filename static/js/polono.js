/* SPLICER v170 — POLONO P31S (e similares) por Bluetooth. Protocolo TSPL, não Niimbot/ESC-POS.
   Serviço BLE 0000ff00 / escrita ff02 (fallback ae00/ae01). Só tela; não mexe em banco. */
(function () {
  'use strict';
  const SVC = ['0000ff00-0000-1000-8000-00805f9b34fb', '0000ae00-0000-1000-8000-00805f9b34fb',
               '0000ffe0-0000-1000-8000-00805f9b34fb', '0000fff0-0000-1000-8000-00805f9b34fb',
               '000018f0-0000-1000-8000-00805f9b34fb', '49535343-fe7d-4ae5-8fa9-9fafd205e455',
               'e7810a71-73ae-499d-8c15-faa9aef0c3f2'];
  const WCH = ['0000ff02-0000-1000-8000-00805f9b34fb', '0000ae01-0000-1000-8000-00805f9b34fb',
               '0000ffe1-0000-1000-8000-00805f9b34fb', '0000fff2-0000-1000-8000-00805f9b34fb',
               '00002af1-0000-1000-8000-00805f9b34fb', '49535343-8841-43f4-a8d4-ecbe34729bb3',
               'bef8d6c9-9c21-4c9e-b632-bd58c1009f9f'];
  const DPM = 8;                       // 203 dpi = 8 dots/mm
  let device = null, server = null, ch = null, withResp = false;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  function connected() { return !!(server && server.connected && ch); }

  async function pickChar() {
    const svcs = await server.getPrimaryServices();
    for (const s of svcs) {
      let cs; try { cs = await s.getCharacteristics(); } catch (e) { continue; }
      for (const c of cs) if (WCH.includes(c.uuid) && (c.properties.write || c.properties.writeWithoutResponse)) { withResp = !!c.properties.write; return c; }
      for (const c of cs) { if (c.properties.write) { withResp = true; return c; } if (c.properties.writeWithoutResponse) { withResp = false; return c; } }
    }
    return null;
  }
  async function connect(all) {
    if (connected()) return;
    const opts = all ? { acceptAllDevices: true, optionalServices: SVC }
                     : { filters: SVC.map((s) => ({ services: [s] })), optionalServices: SVC };
    device = await navigator.bluetooth.requestDevice(opts);
    device.addEventListener('gattserverdisconnected', () => { server = null; ch = null; });
    server = await device.gatt.connect();
    ch = await pickChar();
    if (!ch) { try { device.gatt.disconnect(); } catch (e) {} throw new Error('Conectou mas não achei a característica de escrita (ff02).'); }
  }
  async function writeRaw(bytes) {
    const CH = withResp ? 100 : 100;
    for (let i = 0; i < bytes.length; i += CH) {
      const part = bytes.slice(i, i + CH);
      if (withResp) await ch.writeValueWithResponse(part);        // confiável: não embaralha
      else { await ch.writeValueWithoutResponse(part); await sleep(20); }
    }
  }
  const te = new TextEncoder();
  function concat(a) { const n = a.reduce((s, x) => s + x.length, 0); const o = new Uint8Array(n); let p = 0; for (const x of a) { o.set(x, p); p += x.length; } return o; }

  // gira 90° p/ a largura virar a cabeça (≤ headDots) e monta BITMAP TSPL (0=preto).
  function buildLabel(canvas, opt) {
    const headDots = opt.headDots || 96;      // 12 bytes
    const rot = document.createElement('canvas');
    rot.width = canvas.height; rot.height = canvas.width;
    const rx = rot.getContext('2d');
    rx.translate(rot.width / 2, rot.height / 2); rx.rotate(-Math.PI / 2);
    rx.drawImage(canvas, -canvas.width / 2, -canvas.height / 2);
    let w = rot.width, h = rot.height, cv = rot;
    if (w > headDots) {                        // reduz p/ caber na cabeça
      const sc = headDots / w; cv = document.createElement('canvas');
      cv.width = headDots; cv.height = Math.round(h * sc);
      const c = cv.getContext('2d'); c.fillStyle = '#fff'; c.fillRect(0, 0, cv.width, cv.height); c.drawImage(rot, 0, 0, cv.width, cv.height);
      w = cv.width; h = cv.height;
    }
    const bpr = Math.ceil(w / 8), ctx = cv.getContext('2d'), px = ctx.getImageData(0, 0, w, h).data;
    const data = new Uint8Array(bpr * h).fill(0xff);   // branco = 1
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4, a = px[i + 3];
      const lum = a < 128 ? 255 : px[i] * 0.299 + px[i + 1] * 0.587 + px[i + 2] * 0.114;
      if (lum < 128) data[y * bpr + (x >> 3)] &= ~(0x80 >> (x & 7));   // preto = 0
    }
    const widthMm = Math.round(bpr * 8 / DPM);
    const lenMm = opt.gaps ? (opt.lengthMm || 40) : Math.max(10, Math.round(h / DPM));
    const gap = opt.gaps ? (opt.gapMm || 2) : 0;
    const dens = opt.density || 10;
    const head = te.encode(`SIZE ${widthMm} mm,${lenMm} mm\r\nGAP ${gap} mm,0 mm\r\nDIRECTION 0,0\r\nDENSITY ${dens}\r\nCLS\r\nBITMAP 0,0,${bpr},${h},1,`);
    return concat([head, data, te.encode('\r\nPRINT 1\r\n')]);
  }
  async function printCanvas(canvas, opt) { await writeRaw(buildLabel(canvas, opt || {})); }
  async function printTest(opt) {
    const cv = document.createElement('canvas'); cv.width = 240; cv.height = 96;
    const x = cv.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, 240, 96);
    x.fillStyle = '#000'; x.font = 'bold 30px Arial'; x.textBaseline = 'middle'; x.textAlign = 'center';
    x.fillText('SPLICER OK', 120, 34); x.font = '20px Arial'; x.fillText('POLONO P31S', 120, 68);
    await printCanvas(cv, opt || {});
  }
  window.Polono = { connect, connected, printCanvas, printTest };
})();
