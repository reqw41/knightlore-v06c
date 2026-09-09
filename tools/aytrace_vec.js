#!/usr/bin/env node
// Журнал записей в AY (порт 0x15 = номер регистра, 0x14 = данные) для цели vec.
// Ядро vector06js, шаг по инструкциям, RST7 раз в 59904 такта = «кадр».
//
//   node tools/aytrace_vec.js build/knightlore_vec.r0m <кадров> [--key0=<кадр>] [--out=файл]
//
// Вывод: строки «кадр регистр значение» (десятично) — сравнивать с msx_psg_model.py
// через tools/aycheck.py.
"use strict";
const fs = require("fs"), vm = require("vm");
const base = "/Users/boris/projects/vector06js";
const args = process.argv.slice(2);
const ROM = args[0], NFRAMES = +args[1] || 300;
const KEY0 = (args.find(a => a.startsWith("--key0=")) || "").split("=")[1];
const OUT = (args.find(a => a.startsWith("--out=")) || "").split("=")[1];

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
cpu.pc = 0; cpu.sp = 0xC000; cpu.iff = false;

// матрица Вектора (бит = нажато), как в profile_vec.js
const VKEYS = { z: [7, 0x04], x: [7, 0x01], a: [4, 0x02], q: [6, 0x02], "0": [2, 0x01], space: [7, 0x80] };
const key = (k, down) => { const [r, b] = VKEYS[k]; if (down) keyboard.matrix[r] |= b; else keyboard.matrix[r] &= ~b; };

let frame = 0, curreg = -1;
const log = [];      // [кадр, регистр, значение]
const oout = io.output.bind(io);
io.output = function (port, w8) {
  if (port === 0x15) curreg = w8 & 0x0F;
  else if (port === 0x14) log.push([frame, curreg, w8 & 0xFF]);
  return oout(port, w8);
};

// --keys=кадр:клавиша:down|up,... — расписание нажатий (клавиши z x a q 0 space)
const sched = {};
for (const t of ((args.find(a => a.startsWith("--keys=")) || "").split("=")[1] || "").split(",")) {
  const m = t.match(/^(\d+):(\w+):(down|up)$/);
  if (m) (sched[+m[1]] = sched[+m[1]] || []).push([m[2], m[3] === "down"]);
}
let frc = 0, steps = 0;
const key0 = KEY0 === undefined ? -1 : +KEY0;
while (frame < NFRAMES && steps < 4e8) {
  if (frc >= 59904) {
    frc -= 59904; frame++;
    if (frame === key0) key("0", true);
    if (frame === key0 + 3) key("0", false);
    for (const [k, d] of (sched[frame] || [])) key(k, d);
    v06c.irq = true; v06c.checkInterrupt();
  }
  const c = cpu.instruction(); steps++; frc += c;
  if (cpu.last_opcode === 0xd3) io.commit();
}

const txt = log.map(r => r.join(" ")).join("\n") + "\n";
if (OUT) fs.writeFileSync(OUT, txt);
process.stdout.write(`# кадров ${frame}, инструкций ${steps}, записей в AY ${log.length}\n`);
if (!OUT) process.stdout.write(txt);
