#!/usr/bin/env node
"use strict";
// ГЕЙТ ОБРАЗА С 0x0100 (tools/mkcom.py): образ кладётся с 0x0100, как обычный .rom Вектора
// или CP/M-загрузчик Турбо+ с ключом -V, исполняется с 0x0100, и в момент перехода на 0x0000
// память [0, END) обязана быть байт в байт равна снимку. Дальше игра идёт FRAMES кадров.
//   node tools/comgate_vec.js <образ-0100.rom> <снимок 64 КБ> [END=0xF100]
//   COMGATE_INJECT=1 — испортить один байт потока: гейт обязан покраснеть
const fs = require("fs"), vm = require("vm"), path = require("path"), os = require("os");
const base = process.env.V06JS || path.join(os.homedir(), "projects/vector06js");
const [romPath, snapPath] = process.argv.slice(2, 4);
const END = parseInt(process.argv[4] || "0xF100");
const FRAMES = +(process.env.COMGATE_FRAMES || 600);
if (!romPath || !snapPath) { console.log("usage: comgate_vec.js <образ-0100.rom> <снимок> [END]"); process.exit(2); }

const g = {}; g.window = g;
const fcx = () => ({ createImageData: () => ({ data: new Uint8ClampedArray(4) }), putImageData() {}, getImageData: () => ({ data: new Uint8ClampedArray(4) }), fillRect() {}, drawImage() {} });
const fc = () => ({ width: 0, height: 0, style: {}, getContext: () => fcx() });
g.document = { createElement: fc, getElementById: fc };
g.requestAnimationFrame = () => 0; g.setTimeout = () => 0; g.print = () => 0;
g.console = { log() {}, debug() {}, warn() {}, error() {}, info() {} };
const ctx = vm.createContext(g);
for (const f of ["i8080-js/i8080_trace.js", "i8080-js/i8080_disasm.js", "i8080-js/i8080.js", "src/fili.js", "src/ay.js", "src/fd1793.js", "src/fddimage.js", "src/i8253.js", "src/io.js", "src/keyboard.js", "src/memory.js", "src/sound.js", "src/tv.js"])
  vm.runInContext(fs.readFileSync(path.join(base, f), "utf8"), ctx, { filename: f });
const memory = new ctx.Memory(), keyboard = new ctx.Keyboard(), timer = new ctx.I8253(),
      ay = new ctx.AY(), floppy = new (ctx.Floppy().FD1793)(), io = new ctx.IO(keyboard, timer, memory, ay, floppy);
const cpu = new ctx.I8080(memory, io), tape = { advance: () => {}, sample: 0, reset: () => {} };
const v06c = new ctx.Vector06c(cpu, memory, io, ay, tape);
v06c.bmp = new Uint32Array(576 * 288); v06c.displayFrame = () => {}; v06c.check_breakpoint = () => false; v06c.dbg = null;

const rom = new Uint8Array(fs.readFileSync(romPath));
if (process.env.COMGATE_INJECT === "1") rom[rom.length - 100] ^= 0x5A;   // байт внутри потока
const snap = fs.readFileSync(snapPath);
memory.init_from_array(rom, 0x0100);
memory.detach_boot && memory.detach_boot();
cpu.pc = 0x0100; cpu.sp = 0xC000; cpu.iff = false;
const rd = a => memory.bytes[memory.tobank(a & 0xffff)];

let jumped = -1, bad = 0, first = -1, started = false;
const oinst = cpu.instruction.bind(cpu);
cpu.instruction = function () {
  if (this.pc === 0x0100) started = true;
  if (started && jumped < 0 && this.pc === 0x0000) {
    jumped = frame;
    for (let a = 0; a < END; a++) if (rd(a) !== snap[a]) { bad++; if (first < 0) first = a; }
  }
  return oinst();
};
let frame = 0;
for (; frame < FRAMES; frame++) v06c.oneInterrupt(true);

const ok = jumped >= 0 && bad === 0;   // порча ловится сравнением, а не флагом
console.log(`  ${jumped >= 0 ? "ok     " : "ПРОВАЛ "} переход на 0x0000: кадр ${jumped}`);
console.log(`  ${jumped >= 0 && bad === 0 ? "ok     " : "ПРОВАЛ "} память [0, 0x${END.toString(16)}) против снимка: расхождений ${bad}${bad ? ", первое 0x" + first.toString(16) : ""}`);
console.log(ok ? `ГЕЙТ ЗЕЛЁНЫЙ: ${path.basename(romPath)} раскладывает снимок байт в байт` : "ГЕЙТ КРАСНЫЙ");
process.exit(ok ? 0 : 1);
