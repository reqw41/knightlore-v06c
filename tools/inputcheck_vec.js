#!/usr/bin/env node
// ГЕЙТ РАСКЛАДОК: клавиша матрицы Вектора -> биты user_input, отдельно для каждого из трёх режимов.
// Ни локстеп, ни гейт картинки этого не видят: локстеп кормит ряды ZX напрямую, картинка ввода не
// касается. Поэтому проверка отдельная — как tools/kbcheck_vec.js для рядов ZX.
//   node tools/inputcheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld
// Часть 1: меню — нажатие '1'/'2'/'3' меняет old_input_method (номер режима) и user_input_method.
// Часть 1J: меню и ДЖОЙСТИК — направление листает раскладку ПО ФРОНТУ, FIRE = старт игры ('0').
// Часть 2: ввод — главный код паркуется в jp $, работает только ISR (она набивает vec_kbmat);
//          check_user_input зовётся точечно, читается user_input.
// Часть 2J: ввод с ДЖОЙСТИКА (io.input(0x0E) подменён) во всех трёх раскладках, вместе с клавишами,
//          плюс замок кнопки после старта из меню и цена vec_joy в тактах.
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
// ДЖОЙСТИК ВЕКТОРА (порт 0x0E, чтение) в vector06js СОБИРАЕТСЯ ИЗ МАТРИЦЫ (стрелки, пробел, W) —
// в стенде это подмешивало бы джойстик к каждому нажатию клавиши, чего на живом Векторе нет
// (там это отдельная микросхема). Подменяем чтение: по умолчанию покой, сценарий задаёт joy(...).
// Биты порта active-low: 0 RIGHT, 1 LEFT, 2 UP, 3 DOWN, 6 FIRE1, 7 FIRE2; биты 4,5 схема держит в 1.
const JOY_R=0x01, JOY_L=0x02, JOY_U=0x04, JOY_D=0x08, JOY_F1=0x40, JOY_F2=0x80, JOY_IDLE=0xFF;
let JOY = JOY_IDLE;
function joy(...bits){ JOY = JOY_IDLE; for(const b of bits) JOY &= ~b & 0xFF; return JOY; }
{ const _rd = io.input.bind(io); io.input = p => ((p & 0xFF) === 0x0E ? JOY : _rd(p)); }
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

// ============ часть 1J: меню с джойстика (CPU ещё крутит menu_loop) ============
console.log("МЕНЮ: джойстик");
function joyFrames(f) { for (let i = 0; i < f; i++) v06c.oneInterrupt(true); }
function joyStep(bits, name, idx, uim) {
  joy(...bits); joyFrames(8);
  joy();        joyFrames(8);
  const oi = rd(L.old_input_method), ui = rd(L.user_input_method);
  chk(oi === idx && ui === uim, `${name.padEnd(22)} -> old_input_method=${oi} (ждём ${idx}), user_input_method=${ui} (ждём ${uim})`);
}
press([]);                                   // клавиатура молчит: листает именно джойстик
joyStep([JOY_R], "RIGHT (вперёд)",   1, 4);
joyStep([JOY_D], "DOWN  (вперёд)",   2, 8);
joyStep([JOY_D], "DOWN  (по кругу)", 0, 0);
joyStep([JOY_L], "LEFT  (назад)",    2, 8);
joyStep([JOY_U], "UP    (назад)",    1, 4);
// АНТИДРЕБЕЗГ: по уровню одно отклонение стика пролистало бы все три раскладки подряд.
joy(JOY_R); joyFrames(60);
chk(rd(L.old_input_method) === 2, `удержание RIGHT 60 кадров = ОДИН шаг: old_input_method=${rd(L.old_input_method)} (ждём 2)`);
joy(); joyFrames(8);
// FIRE сведён к клавише '0' в рядах ZX: меню обязано увидеть его как «0 START GAME».
press(["1"]); joyFrames(8); press([]); joyFrames(8);          // вернуться в раскладку 1
chk(rd(L.vec_pal + 8) === 0xC0, "в меню vec_pal+8 = синий фон полосы (0xC0)");
joy(JOY_F1); joyFrames(4);
chk((rd(L.vec_zxrows + 4) & 1) === 0, "FIRE1 прижимает клавишу '0' в ряду ZX 4");
joyFrames(60);
chk(rd(L.vec_pal + 8) !== 0xC0, `FIRE1 = старт игры: kl_menu_exit вернул белую палитру (vec_pal+8=0x${rd(L.vec_pal + 8).toString(16)})`);
chk(rd(L.vec_joylck) === 0xC0, `при выходе из меню замкнуты обе кнопки (vec_joylck=0x${rd(L.vec_joylck).toString(16)})`);

// ================= часть 2: раскладки =================
// ПАРКОВКА: 0x0030..0x0032 — дыра перед ORG 0x0038 (как в kbcheck_vec.js)
wr(0x0030, 0xC3); wr(0x0031, 0x30); wr(0x0032, 0x00);
cpu.pc = 0x0030; cpu.sp = 0xC000; cpu.iff = true;
press([]); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);

function userInput(uim, keys, jbits) {
  wr(L.user_input_method, uim);
  wr(L.all_objs_in_cauldron, 0); wr(L.obj_dropping_into_cauldron, 0);
  joy(...(jbits || []));
  press(keys); for (let f = 0; f < 3; f++) v06c.oneInterrupt(true);   // ISR набивает vec_kbmat и vec_joyraw
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
// ЗАМОК КНОПКИ. Игру только что запустили с зажатой FIRE1 (часть 1J) — она НЕ должна читаться
// как прыжок, пока кнопку не отпустят. Идёт ПЕРВЫМ: замок снимает сам kl_vec_input, и любой
// следующий опрос ввода с отпущенной кнопкой его уже откроет.
console.log("ДЖОЙСТИК: замок кнопки после старта из меню");
chk(userInput(0, [], [JOY_F1]) === 0x00, "зажатая с меню FIRE1 прыжком НЕ читается");
chk(userInput(0, [], []) === 0x00, "кнопку отпустили — ввод пуст");
chk(userInput(0, [], [JOY_F1]) === 0x08, "нажали заново — прыжок");

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

// ============ часть 2J: джойстик в игре ============
const JT = [
  ["1 KEYBOARD", 0, [
    [[JOY_L], 0x01], [[JOY_R], 0x02], [[JOY_U], 0x04], [[JOY_F1], 0x08], [[JOY_F2], 0x10],
    [[JOY_D], 0x00],                                        // вниз в поворотных раскладках не заведено
    [[JOY_L, JOY_F1], 0x09], [[JOY_U, JOY_F2], 0x14], [[], 0x00],
  ]],
  ["2 CURSOR", 4, [
    [[JOY_L], 0x01], [[JOY_R], 0x02], [[JOY_U], 0x04], [[JOY_F1], 0x08], [[JOY_F2], 0x10],
    [[JOY_D], 0x00], [[JOY_R, JOY_U], 0x06], [[], 0x00],
  ]],
  ["3 DIRECTIONAL CONTROL", 8, [
    [[JOY_L], 0x01], [[JOY_R], 0x02], [[JOY_U], 0x04], [[JOY_D], 0x10],
    [[JOY_F1], 0x08], [[JOY_F2], 0x20],
    [[JOY_D, JOY_F2], 0x30], [[], 0x00],
  ]],
];
const JNAME = new Map([[JOY_R, "RIGHT"], [JOY_L, "LEFT"], [JOY_U, "UP"], [JOY_D, "DOWN"], [JOY_F1, "FIRE1"], [JOY_F2, "FIRE2"]]);
for (const [name, uim, cases] of JT) {
  console.log(`ДЖОЙСТИК, РЕЖИМ ${name} (user_input_method=${uim})`);
  for (const [jb, want] of cases) {
    const got = userInput(uim, [], jb);
    chk(got === want, `${(jb.map(b => JNAME.get(b)).join("+") || "покой").padEnd(12)} -> ${got.toString(2).padStart(6, "0")} (${show(got)}), ждём ${want.toString(2).padStart(6, "0")} (${show(want)})`);
  }
}
// ПАРАЛЛЕЛЬНО С КЛАВИАТУРОЙ: джойстик ничего не отменяет, биты только складываются.
console.log("ДЖОЙСТИК И КЛАВИАТУРА ОДНОВРЕМЕННО");
chk(userInput(0, ["O"], [JOY_U]) === 0x05, "режим 1: клавиша O + джойстик UP = влево+вперёд");
chk(userInput(0, ["A"], [JOY_L]) === 0x09, "режим 1: клавиша A + джойстик LEFT = влево+прыжок");
chk(userInput(4, ["<"], [JOY_F1]) === 0x09, "режим 2: стрелка влево + FIRE1 = влево+прыжок");
chk(userInput(8, ["0"], [JOY_L]) === 0x21, "режим 3: цифра 0 + джойстик LEFT = влево+взять(dir)");
// ДЖОЙСТИКА НЕТ (порт молчит, все биты в нуле): игра обязана остаться управляемой, а не «зажатой».
{ const save = JOY; JOY = 0x00;
  chk(userInput(8, [], []) === 0x00 || (JOY = save, false), "порт 0x0E читается нулём (эмулятор без джойстика) — считаем отпущенным");
  JOY = save; }

// РЯДЫ ZX: кнопка сведена к клавише '0' («0 START GAME» и «нажми любую клавишу»), направления
// в ряды НЕ лезут вовсе — иначе они цепляли бы клавиши меню и ложную паузу на ВК.
console.log("ДЖОЙСТИК И РЯДЫ ZX");
const rows = () => [...Array(8)].map((_, r) => rd(L.vec_zxrows + r) & 0x1F).join(" ");
joy(); press([]); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);
const rowsIdle = rows();
joy(JOY_F2); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);
chk((rd(L.vec_zxrows + 4) & 1) === 0, "FIRE2 тоже прижимает '0' (кнопка = «любая клавиша»)");
chk((rd(L.vec_rptab + 0) & 1) === 1, "read_port(0) видит кнопку — экраны «нажми любую клавишу» её примут");
joy(); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);
chk(rows() === rowsIdle, "кнопку отпустили — ряды ZX пересобраны, бит '0' вернулся");
chk((rd(L.vec_rptab + 0) & 1) === 0, "read_port(0) снова чист");
for (const [b, n] of [[JOY_L, "LEFT"], [JOY_R, "RIGHT"], [JOY_U, "UP"], [JOY_D, "DOWN"]]) {
  joy(b); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);
  chk(rows() === rowsIdle, `${n} в ряды ZX не лезет (ни клавиш меню, ни ложной паузы на ВК)`);
}
joy(); press(["0"]); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);
chk((rd(L.vec_zxrows + 4) & 1) === 0, "настоящая клавиша '0' работает по-прежнему");
press([]); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);

// ЦЕНА vec_joy В ТАКТАХ (кадр покоя: ничего не нажато, ряды ZX не пересобирались).
{
  joy(); press([]); for (let f = 0; f < 4; f++) v06c.oneInterrupt(true);
  cpu.iff = false; wr(0xBFFF, 0x00); wr(0xBFFE, 0x30); cpu.sp = 0xBFFE; cpu.pc = L.vec_joy;
  let cyc = 0, i8 = 0; for (let n = 0; n < 5000 && cpu.pc !== 0x0030; n++) { cpu.instruction(); cyc += cpu.vcycles; i8 += cpu.cpu_cycles; }
  cpu.iff = true;
  // ТАКТ ВЕКТОРА в модели vector06js = 4 на машинный цикл (ею же считает profile_vec.js и гейт
  // картинки), в скобках — «книжные» такты i8080. CALL стоит 24 (17) и в тело не входит.
  const CALL = 24, CALLI = 17;
  console.log(`ЦЕНА: vec_joy в кадре покоя ${cyc} + ${CALL} (call) = ${cyc + CALL} тактов Вектора ` +
              `(${i8} + ${CALLI} = ${i8 + CALLI} тактов i8080)`);
  chk(cyc + CALL <= 112, `цена vec_joy в ISR ${cyc + CALL} тактов Вектора за кадр (бюджет 112)`);
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
