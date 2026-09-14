#!/usr/bin/env node
// Журнал звука цели vec. Ядро vector06js, шаг по инструкциям, RST7 раз в 59904 такта = «кадр».
//
//   node tools/aytrace_vec.js build/knightlore_vec.r0m <N> [ключи]
//     --snd=ay|vi|none   режим звука (поке vec_snd_force до старта; по умолчанию ay,
//                        none = оставить пробу и вопрос на стартовом экране)
//     --out=файл         журнал записей регистров AY: «кадр регистр значение» -> tools/aycheck.py
//     --vi=файл          журнал OUT в ВИ53: «кадр порт значение» (порты 0x08..0x0B) -> tools/vicheck.py
//     --key0=кадр        нажать «0» (старт игры) в этом кадре; N = число КАДРОВ (режим мелодий)
//     --keys=кадр:клавиша:down|up,...   расписание нажатий (z x a q 0 space)
//     --inputs=файл      сценарий ходьбы (inputs_alive.txt): N = число ИТЕРАЦИЙ игрового цикла,
//                        стенд как в screendiff_vec.js (сид зафиксирован, ряды ZX кладутся
//                        на границе итерации). Без этого режима в игре стенд разбегается —
//                        проверено и на сборке ДО правок звука.
//     --sld=файл         таблица меток (по умолчанию рядом с ROM)
//
// Записи регистров снимаются НЕ с портов AY, а с точки входа msx_wr (C = регистр, B = значение):
// в режиме ВИ53 драйвер в порты AY не пишет вовсе, а сверять с моделью MSX надо оба режима.
"use strict";
const fs = require("fs"), vm = require("vm");
const base = "/Users/boris/projects/vector06js";
const args = process.argv.slice(2);
const ROM = args[0], NARG = +args[1] || 300;
const opt = n => (args.find(a => a.startsWith("--" + n + "=")) || "").split("=")[1];
const KEY0 = opt("key0"), OUT = opt("out"), VIOUT = opt("vi"), INPUTS = opt("inputs");
const SLD = opt("sld") || ROM.replace(/\.r0m$/, ".sld");
const SND = opt("snd") || "ay";

const L = {};
for (const l of fs.readFileSync(SLD, "latin1").split("\n")) {
  const p = l.split("|");
  if (p.length >= 8 && p[6] === "L") { const n = p[7].split(",")[1]; if (n && !(n in L)) L[n] = +p[5]; }
}
if (L.msx_wr === undefined) throw new Error("в " + SLD + " нет метки msx_wr");

const g = {}; g.window = g;
function fcx() { return { createImageData: () => ({ data: new Uint8ClampedArray(4) }), putImageData: () => {}, getImageData: () => ({ data: new Uint8ClampedArray(4) }), fillRect() {}, drawImage() {} }; }
function fc() { return { width: 0, height: 0, style: {}, getContext: () => fcx() }; }
g.document = { createElement: () => fc(), getElementById: () => fc() };
g.requestAnimationFrame = () => 0; g.setTimeout = () => 0; g.print = () => 0;
g.console = { log() {}, debug() {}, warn() {}, error() {}, info() {} };
const ctx = vm.createContext(g);
for (const f of ["i8080-js/i8080_trace.js", "i8080-js/i8080_disasm.js", "i8080-js/i8080.js",
                 "src/fili.js", "src/ay.js", "src/fd1793.js", "src/fddimage.js", "src/i8253.js",
                 "src/io.js", "src/keyboard.js", "src/memory.js", "src/sound.js", "src/tv.js"])
  vm.runInContext(fs.readFileSync(base + "/" + f, "utf8"), ctx, { filename: f });

const memory = new ctx.Memory(), keyboard = new ctx.Keyboard(), timer = new ctx.I8253(), ay = new ctx.AY(),
      floppy = new (ctx.Floppy().FD1793)(), io = new ctx.IO(keyboard, timer, memory, ay, floppy);
const cpu = new ctx.I8080(memory, io), tape = { advance: () => {}, sample: 0, reset: () => {} },
      v06c = new ctx.Vector06c(cpu, memory, io, ay, tape);
v06c.bmp = new Uint32Array(576 * 288); v06c.displayFrame = () => {}; v06c.check_breakpoint = () => false; v06c.dbg = null;
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0, 0x10000), 0);
memory.detach_boot && memory.detach_boot();
// режим звука задаём ДО старта: иначе проба AY в vec_boot нашла бы чип vector06js и повесила
// стенд на вопросе «SOUND  A AY  T VI53», которого никто не нажмёт.
if (L.vec_snd_force !== undefined && SND !== "none") memory.write(L.vec_snd_force, SND === "vi" ? 2 : 1, false);
cpu.pc = 0; cpu.sp = 0xC000; cpu.iff = false;

const rd = a => memory.bytes[memory.tobank(a & 0xffff)];
const ow = memory.write.bind(memory);
const poke = (a, v) => ow(a & 0xffff, v & 0xff, false);

let frame = 0;
const log = [];      // [кадр, регистр AY, значение]
const vilog = [];    // [кадр, порт ВИ53, значение]
const oout = io.output.bind(io);
io.output = function (port, w8) {
  if (port >= 0x08 && port <= 0x0B) vilog.push([frame, port, w8 & 0xFF]);
  return oout(port, w8);
};

const MSXWR = L.msx_wr;
let steps = 0, frc = 0, tacc = 0;
// ВИ53 тикает вдвое медленнее CPU (1.5 МГц): без этого секундомер бюджета di стоит на месте
const step = () => {
  if (cpu.pc === MSXWR) log.push([frame, cpu.regs[1] & 0x0F, cpu.regs[0] & 0xFF]);
  cpu.instruction(); const c = cpu.vcycles; steps++; frc += c;
  tacc += c; if (tacc > 1) { const tn = tacc >> 1; tacc -= tn * 2; timer.Count(tn); }
  if (cpu.last_opcode === 0xd3) io.commit();
};

let iters = 0;
if (!INPUTS) {
  // ---- режим мелодий: N кадров, клавиши через матрицу Вектора ----
  const VKEYS = { z: [7, 0x04], x: [7, 0x01], a: [4, 0x02], q: [6, 0x02], "0": [2, 0x01], space: [7, 0x80],
                  t: [6, 0x10],     // T — ответ «таймер ВИ53» на вопрос об источнике звука
                  // клавиши джукбокса (ряды матрицы Вектора: 2 — цифры 0..7, 3 — 8,9, 4 — @ABCDEFG, 5 — H..O)
                  "1": [2, 0x02], "2": [2, 0x04], "3": [2, 0x08], "4": [2, 0x10], "5": [2, 0x20],
                  "6": [2, 0x40], "7": [2, 0x80], "8": [3, 0x01], "9": [3, 0x02],
                  b: [4, 0x04], c: [4, 0x08], d: [4, 0x10], e: [4, 0x20], f: [4, 0x40], g: [4, 0x80], h: [5, 0x01] };
  const key = (k, down) => { const [r, b] = VKEYS[k]; if (down) keyboard.matrix[r] |= b; else keyboard.matrix[r] &= ~b; };
  const sched = {};
  for (const t of (opt("keys") || "").split(",")) {
    const m = t.match(/^(\d+):(\w+):(down|up)$/);
    if (m) (sched[+m[1]] = sched[+m[1]] || []).push([m[2], m[3] === "down"]);
  }
  const key0 = KEY0 === undefined ? -1 : +KEY0;
  while (frame < NARG && steps < 4e8) {
    if (frc >= 59904) {
      frc -= 59904; frame++;
      if (frame === key0) key("0", true);
      if (frame === key0 + 3) key("0", false);
      for (const [k, d] of (sched[frame] || [])) key(k, d);
      v06c.irq = true; v06c.checkInterrupt();
    }
    step();
  }
} else {
  // ---- режим ходьбы: стенд screendiff_vec.js (сид зафиксирован, ряды ZX кладём сами) ----
  const lst = fs.readFileSync(ROM.replace(/\.r0m$/, ".lst"), "latin1").split("\n");
  let SYNC = -1;
  for (const l of lst) { const m = l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s.*\tcall\s+shuffle_objects_required/); if (m) { SYNC = parseInt(m[1], 16); break; } }
  if (SYNC < 0) throw new Error("не нашёл call shuffle_objects_required в листинге");
  { // seed_3 += mem[seed_2]: подмешивание байта памяти делает случайность зависимой от раскладки кода
    const s3 = L.seed_3 & 0xff, s3h = (L.seed_3 >> 8) & 0xff;
    const pat = [0x3A, s3, s3h, 0x86, 0x85, 0x84, 0x32, s3, s3h]; let at = -1;
    for (let a = 0x100; a < 0xF000 && at < 0; a++) { let ok = true; for (let i = 0; i < pat.length; i++) if (rd(a + i) !== pat[i]) { ok = false; break; } if (ok) at = a; }
    if (at < 0) throw new Error("не нашёл seed_3 += mem[seed_2]");
    memory.write(at + 3, 0x00, false);
  }
  memory.write(L.vec_zxbuild, 0xC9, false);            // ret: ISR больше не трогает ряды ZX
  for (let r = 0; r < 8; r++) memory.write(L.vec_zxrows + r, 0xFF, false);
  const KEYS = { z: [0, 1], x: [0, 2], a: [1, 0], q: [2, 0], "0": [4, 0], space: [7, 0] };
  const rows = new Uint8Array(8).fill(0xFF);
  const key = (k, down) => { const [r, b] = KEYS[k]; if (down) rows[r] &= ~(1 << b); else rows[r] |= (1 << b); poke(L.vec_zxrows + r, rows[r]); };
  const inputs = {};
  if (fs.existsSync(INPUTS)) for (const l of fs.readFileSync(INPUTS, "utf8").split("\n")) {
    const m = l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);
    if (m) (inputs[+m[1] + 1] = inputs[+m[1] + 1] || []).push([m[2].toLowerCase(), m[3].toLowerCase() === "down"]);
  }
  const SEED2HI = L.seed_2 + 1, ROOM = L.curr_room_attrib;
  let phase = "menu";
  memory.write = function (a, w, s) {
    a &= 0xffff;
    if (phase === "menu" && cpu.pc === SYNC) { poke(L.seed_1, 0x34); poke(L.seed_1 + 1, 0x12); poke(L.seed_2, 0x78); poke(L.seed_2 + 1, 0x56); poke(L.seed_3, 0x9A); phase = "init"; }
    if (a === ROOM && phase === "init") { phase = "game"; key("0", false); }
    if (a === SEED2HI && phase === "game") { iters++; for (const [k, d] of (inputs[iters] || [])) key(k, d); }
    return ow(a, w, s);
  };
  for (let f = 0; f < 60; f++) v06c.oneInterrupt(true);
  key("0", true);
  let guard = 0; while (phase !== "game" && guard++ < 3000) v06c.oneInterrupt(true);
  if (phase !== "game") throw new Error("не дошли до комнаты");
  const start = iters;
  while (iters - start < NARG && steps < 4e8) {
    if (frc >= 59904) { frc -= 59904; frame++; v06c.irq = true; v06c.checkInterrupt(); }
    step();
  }
  iters -= start;
}

const txt = log.map(r => r.join(" ")).join("\n") + "\n";
if (OUT) fs.writeFileSync(OUT, txt);
if (VIOUT) fs.writeFileSync(VIOUT, vilog.map(r => r.join(" ")).join("\n") + "\n");
process.stdout.write(`# режим ${SND}, кадров ${frame}${INPUTS ? ", итераций " + iters : ""}, инструкций ${steps}, записей AY ${log.length}, OUT в ВИ53 ${vilog.length}\n`);
if (!OUT) process.stdout.write(txt);
