// Кто и с чем заводит эффекты звука в цели vec. Стенд тот же, что в screendiff_vec.js
// (сид зафиксирован, ряды ZX кладутся на границе итерации, источник звука — ВИ53).
//
//   KLROOM=<n> node tools/sfxwatch_vec.js build/knightlore_vec.r0m <итераций> [--obj=<gfx>]
//
// На каждый вызов msx_sfx_start печатает «итер / кадр / эффект / слот / тиков / период» и АДРЕС
// ВОЗВРАТА — по нему видно, какой именно хук сработал (ищи адрес в build/knightlore_vec.lst).
// --obj=<gfx>: следить за первым объектом с таким graphic_no и печатать каждую его смену
// вместе с (ix+12) — так меряется задержка «событие -> звук» (см. docs/PORT_RECIPE.md §11.1).
// KLDUMP=1 — напечатать graphic_no всех 40 объектов сразу после входа в комнату,
// KLDUMP2=<кадр> — то же на указанном кадре (объекты комнаты доставляются не сразу).
"use strict";
const fs = require("fs"), vm = require("vm");
const base = process.env.V06JS || require("path").join(require("os").homedir(), "projects/vector06js");
const args = process.argv.slice(2);
const ROM = args[0], N = +args[1] || 300;
const opt = n => (args.find(a => a.startsWith("--" + n + "=")) || "").split("=")[1];
const SLD = ROM.replace(/\.r0m$/, ".sld");
const WATCHGFX = opt("obj") === undefined ? -1 : +opt("obj");
const L = {};
for (const l of fs.readFileSync(SLD, "latin1").split("\n")) {
  const p = l.split("|");
  if (p.length >= 8 && p[6] === "L") { const n = p[7].split(",")[1]; if (n && !(n in L)) L[n] = +p[5]; }
}
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
if (L.vec_snd_force !== undefined) memory.write(L.vec_snd_force, 2, false);  // ВИ53
cpu.pc = 0; cpu.sp = 0xC000; cpu.iff = false;
const rd = a => memory.bytes[memory.tobank(a & 0xffff)];
const ow = memory.write.bind(memory);
const poke = (a, v) => ow(a & 0xffff, v & 0xff, false);
const lst = fs.readFileSync(ROM.replace(/\.r0m$/, ".lst"), "latin1").split("\n");
let SYNC = -1;
for (const l of lst) { const m = l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s.*\tcall\s+shuffle_objects_required/); if (m) { SYNC = parseInt(m[1], 16); break; } }
// KLGAG: seed_3 += mem[seed_2] -> nop
{
  const s3 = L.seed_3 & 0xff, s3h = (L.seed_3 >> 8) & 0xff;
  const pat = [0x3A, s3, s3h, 0x86, 0x85, 0x84, 0x32, s3, s3h]; let at = -1;
  for (let a = 0x100; a < 0xF000 && at < 0; a++) { let ok = true; for (let i = 0; i < pat.length; i++) if (rd(a + i) !== pat[i]) { ok = false; break; } if (ok) at = a; }
  if (at >= 0) memory.write(at + 3, 0x00, false);
}
memory.write(L.vec_zxbuild, 0xC9, false);
for (let r = 0; r < 8; r++) memory.write(L.vec_zxrows + r, 0xFF, false);
const KEYS = { z: [0, 1], x: [0, 2], a: [1, 0], q: [2, 0], "0": [4, 0], space: [7, 0] };
const rows = new Uint8Array(8).fill(0xFF);
const key = (k, down) => { const [r, b] = KEYS[k]; if (down) rows[r] &= ~(1 << b); else rows[r] |= (1 << b); poke(L.vec_zxrows + r, rows[r]); };
const SEED2HI = L.seed_2 + 1, ROOM = L.curr_room_attrib;
let phase = "menu", iters = 0, roomSet = false;
memory.write = function (a, w, s) {
  a &= 0xffff;
  if (phase === "menu" && cpu.pc === SYNC) { poke(L.seed_1, 0x34); poke(L.seed_1 + 1, 0x12); poke(L.seed_2, 0x78); poke(L.seed_2 + 1, 0x56); poke(L.seed_3, 0x9A); phase = "init"; }
  if (process.env.KLROOM && phase === "init" && a >= 0xA000 && a < 0xC000 && rd(L.graphic_objs_tbl + 8) !== 0 && !roomSet) {
    roomSet = true; ow(L.graphic_objs_tbl + 8, +process.env.KLROOM, false); ow(L.graphic_objs_tbl + 40, +process.env.KLROOM, false);
  }
  if (a === ROOM && phase === "init") { phase = "game"; key("0", false); }
  if (a === SEED2HI && phase === "game") iters++;
  return ow(a, w, s);
};
const SFX = L.msx_sfx_start, NOISE = L.audio_msx_noise;
let frame = 0, frc = 0, tacc = 0, steps = 0;
const out = [];
const step = () => {
  if (cpu.pc === SFX) {
    // A = id, B = тиков, DE = период, C = слот
    const A = cpu.regs[7] & 0xff, B = cpu.regs[0] & 0xff, C = cpu.regs[1] & 0xff,
          D = cpu.regs[2] & 0xff, E = cpu.regs[3] & 0xff;
    out.push(`итер ${iters} кадр ${frame}  эффект ${A} слот ${C} тиков ${B} период ${(D << 8) | E}  ret=0x${((rd(cpu.sp+1)<<8)|rd(cpu.sp)).toString(16)}${objBase>=0?'  (ix+12)=0x'+rd(objBase+12).toString(16):''}`);
  }
  cpu.instruction(); const c = cpu.vcycles; steps++; frc += c;
  tacc += c; if (tacc > 1) { const tn = tacc >> 1; tacc -= tn * 2; timer.Count(tn); }
  if (cpu.last_opcode === 0xd3) io.commit();
};
for (let f = 0; f < 60; f++) v06c.oneInterrupt(true);
key("0", true);
let guard = 0; while (phase !== "game" && guard++ < 3000) v06c.oneInterrupt(true);
if (phase !== "game") throw new Error("не дошли до комнаты");
const start = iters;
// найти объект с нужным graphic_no
let objBase = -1;
const findObj = () => {
  if (WATCHGFX < 0) return;
  for (let n = 0; n < 40; n++) { const g0 = rd(L.graphic_objs_tbl + n * 0x20); if (g0 === WATCHGFX) { objBase = L.graphic_objs_tbl + n * 0x20; return; } }
};
findObj();
if (process.env.KLDUMP) { const o=[]; for (let n=0;n<40;n++) o.push(rd(L.graphic_objs_tbl+n*0x20)); process.stdout.write('objs='+o.join(',')+'\n'); }
if (WATCHGFX >= 0) out.push(objBase < 0 ? `объект gfx=${WATCHGFX} не найден` : `объект gfx=${WATCHGFX} по адресу 0x${objBase.toString(16)}`);
let lastG = objBase >= 0 ? rd(objBase) : -1;
while (iters - start < N && steps < 4e8) {
  if (frc >= 59904) {
    frc -= 59904; frame++; v06c.irq = true; v06c.checkInterrupt();
    if (process.env.KLDUMP2 && frame === +process.env.KLDUMP2) { const o=[]; for (let n=0;n<40;n++) o.push(rd(L.graphic_objs_tbl+n*0x20)); out.push('objs@'+frame+'='+o.join(',')); if(objBase<0){findObj(); if(objBase>=0){out.push('объект gfx='+WATCHGFX+' найден на кадре '+frame+', адрес 0x'+objBase.toString(16)); lastG=rd(objBase);} } }
    if (objBase >= 0) { const gg = rd(objBase); if (gg !== lastG) { out.push(`*** итер ${iters - start} кадр ${frame}: graphic_no ${lastG} -> ${gg}, (ix+12)=0x${rd(objBase + 12).toString(16)}`); lastG = gg; } }
  }
  step();
}
process.stdout.write(out.join("\n") + `\n# итераций ${iters - start}, кадров ${frame}\n`);
