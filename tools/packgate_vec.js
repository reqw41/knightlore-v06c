#!/usr/bin/env node
// Гейт релизного образа: заставка -> клавиша/джойстик -> распаковка -> та же память, что у цели vec.
//   node tools/packgate_vec.js [build/knightlore_title.r0m] [build/knightlore_vec.r0m] [build/knightlore_vec.sld]
// Что проверяется (код возврата 0 — всё сошлось):
//   1. заставка на экране: снимок четырёх плоскостей с ПАЛИТРОЙ ИЗ ЧИПА (io.Palette, защёлка по лучу)
//      -> build/title_shot.png, и он обязан совпасть с картинкой из src/title_p*.bin + src/title.pal;
//   2. мелодия: журнал OUT в ВИ53 за время заставки — та же последовательность делителей,
//      что даёт мелодия menu в самой игре (сверяется с прогоном образа vec);
//   3. снятие клавишей матрицы и кнопкой джойстика (порт 0x0E): после распаковки ВСЯ память
//      байт в байт равна образу vec в той же точке (PC = main+3, таблицы сдвига уже построены);
//   4. время распаковки в кадрах.
// Ключи окружения: KLPG_SHOT — куда класть снимок (по умолчанию build/title_shot.png),
//   KLPG_FRAMES — сколько кадров держать заставку до нажатия (по умолчанию 120).
"use strict";
const fs = require("fs"), vm = require("vm"), zlib = require("zlib");
const base = "/Users/boris/projects/vector06js";
const TITLE = process.argv[2] || "build/knightlore_title.r0m";
const VEC = process.argv[3] || "build/knightlore_vec.r0m";
const SLD = process.argv[4] || "build/knightlore_vec.sld";
const SHOT = process.env.KLPG_SHOT || "build/title_shot.png";
const TITLE_FRAMES = +(process.env.KLPG_FRAMES || 120);

const L = {};
for (const l of fs.readFileSync(SLD, "latin1").split("\n")) {
  const p = l.split("|");
  if (p.length >= 8 && p[6] === "L") { const n = p[7].split(",")[1]; if (n && !(n in L)) L[n] = +p[5]; }
}
if (L.main === undefined) throw new Error("в SLD нет метки main");
const MAIN3 = L.main + 3;        // сразу после call build_lookup_tbls: таблицы построены

// ---- машина -------------------------------------------------------------
function machine(romPath) {
  const g = {}; g.window = g;
  const fcx = () => ({ createImageData: () => ({ data: new Uint8ClampedArray(4) }), putImageData: () => {}, getImageData: () => ({ data: new Uint8ClampedArray(4) }), fillRect() {}, drawImage() {} });
  const fc = () => ({ width: 0, height: 0, style: {}, getContext: () => fcx() });
  g.document = { createElement: fc, getElementById: fc };
  g.requestAnimationFrame = () => 0; g.setTimeout = () => 0; g.print = () => 0;
  g.console = { log() {}, debug() {}, warn() {}, error() {}, info() {} };
  const ctx = vm.createContext(g);
  for (const f of ["i8080-js/i8080_trace.js", "i8080-js/i8080_disasm.js", "i8080-js/i8080.js", "src/fili.js", "src/ay.js", "src/fd1793.js", "src/fddimage.js", "src/i8253.js", "src/io.js", "src/keyboard.js", "src/memory.js", "src/sound.js", "src/tv.js"])
    vm.runInContext(fs.readFileSync(base + "/" + f, "utf8"), ctx, { filename: f });
  const memory = new ctx.Memory(), keyboard = new ctx.Keyboard(), timer = new ctx.I8253(),
        ay = new ctx.AY(), floppy = new (ctx.Floppy().FD1793)(), io = new ctx.IO(keyboard, timer, memory, ay, floppy);
  // джойстик Вектора — отдельная микросхема, а не матрица (vector06js синтезирует его из клавиш)
  const JOY_IDLE = 0xFF; const st = { joy: JOY_IDLE, vilog: [], frame: 0 };
  const _rd = io.input.bind(io); io.input = p => ((p & 0xFF) === 0x0E ? st.joy : _rd(p));
  const _out = io.output.bind(io);
  io.output = function (port, w8) { if (port >= 0x08 && port <= 0x0B) st.vilog.push([st.frame, port, w8 & 0xFF]); return _out(port, w8); };
  const cpu = new ctx.I8080(memory, io), tape = { advance: () => {}, sample: 0, reset: () => {} };
  const v06c = new ctx.Vector06c(cpu, memory, io, ay, tape);
  v06c.bmp = new Uint32Array(576 * 288); v06c.displayFrame = () => {}; v06c.check_breakpoint = () => false; v06c.dbg = null;
  memory.init_from_array(new Uint8Array(fs.readFileSync(romPath)).subarray(0, 0x10000), 0);
  memory.detach_boot && memory.detach_boot();
  cpu.pc = 0; cpu.sp = 0xC000; cpu.iff = false;
  st.rd = a => memory.bytes[memory.tobank(a & 0xffff)];
  return { ctx, memory, keyboard, timer, io, cpu, v06c, st };
}
// клавиши матрицы Вектора: ряд, бит (active-high в keyboard.matrix)
const VKEYS = { q: [4, 0x02], "0": [2, 0x01], space: [7, 0x80] };
function key(m, k, down) { const [r, b] = VKEYS[k]; if (down) m.keyboard.matrix[r] |= b; else m.keyboard.matrix[r] &= ~b; }

// пошаговый прогон с кадровыми прерываниями (как в остальных стендах): timer тикает,
// потому что игра читает счётчик 0 (vec_di_budget)
function runSteps(m, opts) {
  const { untilPC, maxFrames, onFrame } = opts;
  let frc = 0, tacc = 0, steps = 0, frames = 0;
  for (;;) {
    if (frc >= 59904) {
      frc -= 59904; frames++; m.st.frame++;
      m.v06c.irq = true; m.v06c.checkInterrupt();
      if (onFrame) onFrame(frames);
      if (maxFrames && frames >= maxFrames) return { frames, steps, hit: false };
    }
    m.cpu.instruction(); steps++;
    const c = m.cpu.vcycles; frc += c; tacc += c;
    if (tacc > 1) { const tn = tacc >> 1; tacc -= tn * 2; m.timer.Count(tn); }
    if (m.cpu.last_opcode === 0xd3) m.io.commit();
    if (untilPC !== undefined && m.cpu.pc === untilPC) return { frames, steps, hit: true };
    if (steps > 4e8) return { frames, steps, hit: false };
  }
}

// ---- журнал ВИ53 -> последовательность нот (счётчик, делитель) ----------
function viNotes(vilog) {
  const half = [null, null, null], out = [];
  const PORT = { 0x0B: 0, 0x0A: 1, 0x09: 2 };
  for (const [f, port, val] of vilog) {
    if (port === 0x08) { const c = val >> 6; if (((val >> 4) & 3) === 0 || c > 2) continue; half[c] = null; }
    else if (port in PORT) {
      const c = PORT[port];
      // 0xFFFF в счётчике 0 — не нота, а секундомер кадра (vec_di_budget перезаряжает его из ISR)
      if (half[c] === null) half[c] = val; else { const d = half[c] | (val << 8); if (d !== 0xFFFF) out.push([c, d]); half[c] = null; }
    }
  }
  return out;
}

// ---- PNG ---------------------------------------------------------------
let TBL = null;
function crc32(buf) { if (!TBL) { TBL = new Int32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1; TBL[n] = c; } } let c = 0xFFFFFFFF; for (const b of buf) c = TBL[(c ^ b) & 0xFF] ^ (c >>> 8); return (c ^ 0xFFFFFFFF) >>> 0; }
function png(path, W, H, rgb) {
  const raw = Buffer.alloc(H * (W * 3 + 1));
  for (let y = 0; y < H; y++) { raw[y * (W * 3 + 1)] = 0; rgb.copy(raw, y * (W * 3 + 1) + 1, y * W * 3, (y + 1) * W * 3); }
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t, "latin1"), d]); const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td) >>> 0); return Buffer.concat([len, td, crc]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(W, 0); ihdr.writeUInt32BE(H, 4); ihdr[8] = 8; ihdr[9] = 2;
  fs.writeFileSync(path, Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]));
}

let bad = 0;
const say = (ok, msg) => { if (!ok) bad++; console.log((ok ? "OK   " : "ПЛОХО") + " " + msg); };

// ---- 1. эталон: образ vec ----------------------------------------------
const ref = machine(VEC);
const refRun = runSteps(ref, { untilPC: MAIN3, maxFrames: 3000 });
if (!refRun.hit) { console.log("ПЛОХО образ vec не дошёл до main+3"); process.exit(1); }
const refMem = Buffer.alloc(0x10000); for (let a = 0; a < 0x10000; a++) refMem[a] = ref.st.rd(a);
runSteps(ref, { maxFrames: 1200 });                   // меню играет мелодию menu
const refNotes = viNotes(ref.st.vilog);

// ---- 2. заставка: снимок и мелодия -------------------------------------
const t = machine(TITLE);
for (let f = 0; f < TITLE_FRAMES; f++) { t.v06c.oneInterrupt(true); t.st.frame++; }
const pal = [];
for (let i = 0; i < 16; i++) { const v = t.io.Palette[i]; pal.push([(v >> 5) & 7, (v >> 13) & 7, (v >> 22) & 3]); }
// ожидаемая палитра — из src/title.pal (байт %BBGGGRRR)
const wantPal = [...fs.readFileSync("src/title.pal")].map(v => [v & 7, (v >> 3) & 7, (v >> 6) & 3]);
say(JSON.stringify(pal) === JSON.stringify(wantPal), "палитра заставки дошла до чипа: " + [...fs.readFileSync("src/title.pal")].map(v => v.toString(16).padStart(2, "0")).join(" "));
// снимок: 4 плоскости + палитра, 256x256 один к одному
{
  const planes = [0, 1, 2, 3].map(i => fs.readFileSync("src/title_p" + i + ".bin"));
  const W = 256, H = 256, img = Buffer.alloc(W * H * 3); let wrong = 0;
  for (let sy = 0; sy < 256; sy++) {
    const mrow = 255 - sy;
    for (let col = 0; col < 32; col++) {
      const b = [0x8000, 0xA000, 0xC000, 0xE000].map(base2 => t.st.rd(base2 + (col << 8) + mrow));
      const w = [0, 1, 2, 3].map(i => planes[i][(col << 8) + mrow]);
      for (let i = 0; i < 4; i++) if (b[i] !== w[i]) wrong++;
      for (let bit = 7; bit >= 0; bit--) {
        const idx = (((b[0] >> bit) & 1) << 3) | (((b[1] >> bit) & 1) << 2) | (((b[2] >> bit) & 1) << 1) | ((b[3] >> bit) & 1);
        const c = pal[idx], o = ((sy * W) + col * 8 + (7 - bit)) * 3;
        img[o] = c[0] * 255 / 7 | 0; img[o + 1] = c[1] * 255 / 7 | 0; img[o + 2] = c[2] * 255 / 3 | 0;
      }
    }
  }
  png(SHOT, W, H, img);
  say(wrong === 0, `плоскости заставки в памяти = src/title_p*.bin (расхождений ${wrong}); снимок -> ${SHOT}`);
}
const titleNotes = viNotes(t.st.vilog);
say(titleNotes.length > 8, `мелодия на заставке звучит: нот ВИ53 за ${TITLE_FRAMES} кадров — ${titleNotes.length}`);
{
  const n = Math.min(24, titleNotes.length, refNotes.length);
  const a = JSON.stringify(titleNotes.slice(0, n)), b = JSON.stringify(refNotes.slice(0, n));
  say(n > 0 && a === b, `первые ${n} нот заставки совпали с мелодией menu из игры`);
  if (a !== b) { console.log("     заставка: " + a); console.log("     игра:     " + b); }
}

// ---- 3. снятие клавишей и джойстиком ------------------------------------
function dismiss(mode) {
  const m = machine(TITLE);
  for (let f = 0; f < TITLE_FRAMES; f++) { m.v06c.oneInterrupt(true); m.st.frame++; }
  if (mode === "key") key(m, "q", true); else m.st.joy = 0xFF & ~0x40;   // FIRE1
  const r1 = runSteps(m, { maxFrames: 10 });
  if (mode === "key") key(m, "q", false); else m.st.joy = 0xFF;
  // три отрезка: гашение экрана -> распаковщик (0xF100) -> штатный старт игры (vec_boot, таблицы)
  const rA = runSteps(m, { untilPC: 0xF100, maxFrames: 60 });
  if (!rA.hit) { say(false, `${mode}: распаковщик не запустился`); return null; }
  const rB = runSteps(m, { untilPC: 0x0000, maxFrames: 400 });
  if (!rB.hit) { say(false, `${mode}: распаковка не кончилась`); return null; }
  const rC = runSteps(m, { untilPC: MAIN3, maxFrames: 400 });
  if (!rC.hit) { say(false, `${mode}: после распаковки игра не дошла до main+3`); return null; }
  const r2 = { frames: r1.frames + rA.frames + rB.frames + rC.frames, unpack: rB.frames, boot: rC.frames };
  const mem = Buffer.alloc(0x10000); for (let a = 0; a < 0x10000; a++) mem[a] = m.st.rd(a);
  // мёртвый хвост стека под SP: игра и релиз входят в main с одинаковым SP (= clear_start),
  // но сколько раз ISR успел войти до этого — вопрос кадровой фазы, и байты НИЖЕ SP разные.
  const SP0 = L.clear_start, DEAD0 = SP0 - 64, DEAD1 = SP0 - 1;
  let diff = [], i = 0;
  while (i < 0x10000) {
    if (i >= DEAD0 && i <= DEAD1) { i = DEAD1 + 1; continue; }
    if (mem[i] !== refMem[i]) { const s = i; while (i < 0x10000 && mem[i] !== refMem[i]) i++; diff.push([s, i - 1]); } else i++;
  }
  say(diff.length === 0, `${mode}: после распаковки память = образу vec байт в байт, кроме мёртвого хвоста стека 0x${DEAD0.toString(16)}..0x${DEAD1.toString(16)} (кадров от нажатия до меню ${r2.frames})`);
  if (diff.length) for (const [a, b] of diff.slice(0, 12)) console.log(`     0x${a.toString(16)}..0x${b.toString(16)} (${b - a + 1} Б): стало ${[...mem.subarray(a, Math.min(b + 1, a + 8))].map(v => v.toString(16)).join(" ")}, надо ${[...refMem.subarray(a, Math.min(b + 1, a + 8))].map(v => v.toString(16)).join(" ")}`);
  console.log(`     ${mode}: гашение ${r1.frames + rA.frames} кадров, распаковка ${r2.unpack}, штатный старт игры ${r2.boot}`);
  return r2;
}
const fKey = dismiss("key");
const fJoy = dismiss("joy");
if (fKey) {
  // порог на РАСПАКОВКУ: очистка плоскости 0 и постройка таблиц сдвига есть и у цели vec,
  // их пользователь ждёт и при обычной загрузке
  say(fKey.unpack <= 100, `распаковка укладывается в 2 с: ${fKey.unpack} кадров (${(fKey.unpack / 50).toFixed(2)} с)`);
  console.log(`     от нажатия до меню ${fKey.frames} кадров (${(fKey.frames / 50).toFixed(2)} с)`);
}

console.log(bad === 0 ? "packgate: СОВПАДЕНИЕ" : `packgate: РАСХОЖДЕНИЙ ${bad}`);
process.exit(bad === 0 ? 0 : 1);
