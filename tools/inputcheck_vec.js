#!/usr/bin/env node
// ГЕЙТ РАСКЛАДОК: клавиша матрицы Вектора -> биты user_input, отдельно для каждого из трёх режимов.
// Ни локстеп, ни гейт картинки этого не видят: локстеп кормит ряды ZX напрямую, картинка ввода не
// касается. Поэтому проверка отдельная — как tools/kbcheck_vec.js для рядов ZX.
//   node tools/inputcheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld
// Часть 1: меню — нажатие '1'/'2'/'3' меняет old_input_method (номер режима) и user_input_method.
// Часть 2: ввод — главный код паркуется в jp $, работает только ISR (она набивает vec_kbmat);
//          check_user_input зовётся точечно, читается user_input.
"use strict";
const fs = require("fs"), vm = require("vm");
const base = "/Users/boris/projects/vector06js";
const [ROM, SLD] = process.argv.slice(2);
const L = {};
for (const l of fs.readFileSync(SLD, "latin1").split("\n")) {
  const p = l.split("|"); if (p.length >= 8 && p[6] === "L") { const n = p[7].split(",")[1]; if (n && !(n in L)) L[n] = +p[5]; }
}
const g = {}; g.window = g; g.document = { createElement: () => fc(), getElementById: () => fc() };
function fcx() { return { createImageData: () => ({ data: new Uint8ClampedArray(4) }), putImageData: () => {}, getImageData: () => ({ data: new Uint8ClampedArray(4) }), fillRect() {}, drawImage() {} }; }
function fc() { return { width: 0, height: 0, style: {}, getContext: () => fcx() }; }
g.requestAnimationFrame = () => 0; g.setTimeout = () => 0; g.print = () => 0;
g.console = { log() {}, debug() {}, warn() {}, error() {}, info() {} };
const ctx = vm.createContext(g);
for (const f of ["i8080-js/i8080_trace.js", "i8080-js/i8080_disasm.js", "i8080-js/i8080.js", "src/fili.js", "src/ay.js", "src/fd1793.js", "src/fddimage.js", "src/i8253.js", "src/io.js", "src/keyboard.js", "src/memory.js", "src/sound.js", "src/tv.js"])
  vm.runInContext(fs.readFileSync(base + "/" + f, "utf8"), ctx, { filename: f });
const memory = new ctx.Memory(), keyboard = new ctx.Keyboard(), timer = new ctx.I8253(), ay = new ctx.AY(),
  floppy = new (ctx.Floppy().FD1793)(), io = new ctx.IO(keyboard, timer, memory, ay, floppy);
const cpu = new ctx.I8080(memory, io), tape = { advance: () => {}, sample: 0, reset: () => {} },
  v06c = new ctx.Vector06c(cpu, memory, io, ay, tape);
v06c.bmp = new Uint32Array(576 * 288); v06c.displayFrame = () => {}; v06c.check_breakpoint = () => false; v06c.dbg = null;
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0, 0x10000), 0);
memory.detach_boot && memory.detach_boot();
if (L.vec_snd_force !== undefined) memory.write(L.vec_snd_force, 1, false);
const rd = a => memory.bytes[memory.tobank(a & 0xffff)];
const wr = (a, v) => memory.write(a & 0xffff, v & 0xff, false);

// ---- клавиши матрицы Вектора: [ряд, маска] ----
const K = {
  "ВК": [0, 0x04], "<": [0, 0x10], ">": [0, 0x40], "^": [0, 0x20], "v": [0, 0x80],
  "ПРБ": [7, 0x80], "O": [5, 0x80], "P": [6, 0x01], "Q": [6, 0x02], "A": [4, 0x02],
  "Z": [7, 0x04], "X": [7, 0x01], "M": [5, 0x20],
};
for (let d = 0; d <= 7; d++) K["" + d] = [2, 1 << d];
K["8"] = [3, 0x01]; K["9"] = [3, 0x02];
function press(keys) { keyboard.matrix.fill(0); for (const k of keys) { const [r, m] = K[k]; keyboard.matrix[r] |= m; } }

let bad = 0;
const chk = (ok, msg) => { if (!ok) { bad++; console.log("  ✗ " + msg); } else console.log("  ✓ " + msg); };

cpu.pc = 0; cpu.sp = 0xC000; cpu.iff = false;
for (let f = 0; f < 120; f++) v06c.oneInterrupt(true);   // загрузка, меню нарисовано

// ================= часть 1: меню =================
console.log("МЕНЮ: выбор режима клавишами 1/2/3");
const MODES = [["1", 0, 0], ["2", 1, 4], ["3", 2, 8], ["1", 0, 0]];
for (const [key, idx, uim] of MODES) {
  press([key]); for (let f = 0; f < 8; f++) v06c.oneInterrupt(true);
  press([]);    for (let f = 0; f < 8; f++) v06c.oneInterrupt(true);
  const oi = rd(L.old_input_method), ui = rd(L.user_input_method);
  chk(oi === idx && ui === uim, `'${key}' -> old_input_method=${oi} (ждём ${idx}), user_input_method=${ui} (ждём ${uim})`);
}

// ================= часть 2: раскладки =================
// ПАРКОВКА: 0x0030..0x0032 — дыра перед ORG 0x0038 (как в kbcheck_vec.js)
wr(0x0030, 0xC3); wr(0x0031, 0x30); wr(0x0032, 0x00);
cpu.pc = 0x0030; cpu.sp = 0xC000; cpu.iff = true;
press([]); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);

function userInput(uim, keys) {
  wr(L.user_input_method, uim);
  wr(L.all_objs_in_cauldron, 0); wr(L.obj_dropping_into_cauldron, 0);
  press(keys); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);   // ISR набивает vec_kbmat
  cpu.iff = false;
  cpu.sp = 0xC000; wr(0xBFFF, 0x00); wr(0xBFFE, 0x30); cpu.sp = 0xBFFE;   // адрес возврата = 0x0030
  cpu.pc = L.check_user_input;
  for (let n = 0; n < 100000 && cpu.pc !== 0x0030; n++) cpu.instruction();
  if (cpu.pc !== 0x0030) throw new Error("check_user_input не вернулся");
  cpu.iff = true;
  return rd(L.user_input);
}
const BITS = ["влево", "вправо", "вперёд", "прыжок", "взять", "взять(dir)"];
const show = v => v ? BITS.filter((_, i) => v & (1 << i)).join("+") : "—";
// ожидание: маска битов user_input
const TESTS = [
  ["1 KEYBOARD", 0, [
    [["O"], 0x01], [["P"], 0x02], [["Q"], 0x04], [["A"], 0x08],
    [["0"], 0x10], [["7"], 0x10], [["3"], 0x10],
    [["<"], 0x00], [[">"], 0x00], [["^"], 0x00], [["v"], 0x00], [["ПРБ"], 0x00],
    [["ВК"], 0x00], [["Z"], 0x00], [["X"], 0x00], [["M"], 0x00], [["8"], 0x00], [["9"], 0x00],
    [["O", "Q"], 0x05], [[], 0x00],
  ]],
  ["2 CURSOR", 4, [
    [["<"], 0x01], [[">"], 0x02], [["^"], 0x04], [["ПРБ"], 0x08], [["v"], 0x10],
    [["O"], 0x00], [["P"], 0x00], [["Q"], 0x00], [["A"], 0x00], [["0"], 0x00], [["7"], 0x00],
    [["ВК"], 0x00], [["Z"], 0x00], [["X"], 0x00], [["M"], 0x00],
    [["<", "^"], 0x05], [[], 0x00],
  ]],
  ["3 DIRECTIONAL CONTROL", 8, [
    [["<"], 0x01], [[">"], 0x02], [["^"], 0x04], [["v"], 0x10], [["ПРБ"], 0x08],
    [["0"], 0x20], [["7"], 0x20], [["5"], 0x20],
    [["O"], 0x00], [["P"], 0x00], [["Q"], 0x00], [["A"], 0x00],
    [["ВК"], 0x00], [["Z"], 0x00], [["X"], 0x00], [["M"], 0x00], [["8"], 0x00], [["9"], 0x00],
    [["<", "ПРБ", "3"], 0x29], [[], 0x00],
  ]],
];
for (const [name, uim, cases] of TESTS) {
  console.log(`РЕЖИМ ${name} (user_input_method=${uim})`);
  for (const [keys, want] of cases) {
    const got = userInput(uim, keys);
    chk(got === want, `${(keys.join("+") || "ничего").padEnd(12)} -> ${got.toString(2).padStart(6, "0")} (${show(got)}), ждём ${want.toString(2).padStart(6, "0")} (${show(want)})`);
  }
}

// ================= часть 3: пауза на ВК =================
// handle_pause ждёт нажатия внутри себя, поэтому проверяем не прогоном, а тем, что процедура
// читает ряд 0 матрицы Вектора и при ненажатой ВК возвращается сразу.
console.log("ПАУЗА");
press([]); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);
cpu.iff = false; cpu.sp = 0xBFFE; wr(0xBFFF, 0x00); wr(0xBFFE, 0x30); cpu.pc = L.handle_pause;
let n = 0; for (; n < 5000 && cpu.pc !== 0x0030; n++) cpu.instruction();
chk(cpu.pc === 0x0030, `без ВК handle_pause выходит сразу (${n} инструкций)`);
cpu.iff = true; press(["ВК"]); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);
cpu.iff = false; cpu.sp = 0xBFFE; wr(0xBFFF, 0x00); wr(0xBFFE, 0x30); cpu.pc = L.handle_pause;
let stuck = true; for (let i = 0; i < 5000; i++) { cpu.instruction(); if (cpu.pc === 0x0030) { stuck = false; break; } }
chk(stuck, "с зажатой ВК handle_pause не выходит (ждёт отпускания) — пауза встала");
// пробел паузу больше не ставит (он прыжок в режимах 2 и 3)
cpu.iff = true; press(["ПРБ"]); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);
cpu.iff = false; cpu.sp = 0xBFFE; wr(0xBFFF, 0x00); wr(0xBFFE, 0x30); cpu.pc = L.handle_pause;
n = 0; for (; n < 5000 && cpu.pc !== 0x0030; n++) cpu.instruction();
chk(cpu.pc === 0x0030, "пробел паузу не ставит");

// ЗАЩИТА ОРИГИНАЛА: пауза только если нажат ВК и НИ ОДНОЙ клавиши того же ряда. В ряду 0 Вектора
// вместе с ВК лежат стрелки — клавиши движения раскладок 2 и 3; без этой проверки ложная пауза
// (в том числе «призрак» матрицы) выглядит как зависание.
for (const arr of ["<", ">", "^", "v"]) {
  cpu.iff = true; press(["ВК", arr]); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);
  cpu.iff = false; cpu.sp = 0xBFFE; wr(0xBFFF, 0x00); wr(0xBFFE, 0x30); cpu.pc = L.handle_pause;
  n = 0; for (; n < 5000 && cpu.pc !== 0x0030; n++) cpu.instruction();
  chk(cpu.pc === 0x0030, `ВК вместе со стрелкой ${arr} паузу НЕ ставит (защита оригинала)`);
}

console.log(bad ? `РАСХОЖДЕНИЙ: ${bad}` : "все проверки сошлись");
process.exit(bad ? 1 : 0);
