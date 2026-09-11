#!/usr/bin/env node
// Дифференциальный гейт на ОДНУ процедуру: холодный вызов в двух сборках (эталон и текущая)
// на одинаковом сгенерированном состоянии; сверяются регистры, помеченные регионы памяти
// и трасса записей в нулевую страницу (баг оригинала: запись по нулевому указателю).
// Нужен потому, что сценарные гейты (локстеп, render_gate) вход в комнату почти не задевают:
// find_special_objs_here вызывается 1 раз за 600 итераций, update_special_objs — ни разу.
//   node tools/procdiff_vec.js <refRom> <refSld> <newRom> <newSld> <label> [N] [seed]
// Регионы и генератор состояния — в GEN/CMP по имени метки (адреса берутся из ЕЁ сборки,
// поэтому сдвиг кода и переменных между сборками сравнению не мешает).
"use strict";
const fs = require("fs"), vm = require("vm");
const base = "/Users/boris/projects/vector06js";
const [RREF, SREF, RNEW, SNEW, LABEL, NS, SEEDS] = process.argv.slice(2);
const N = +NS || 200, SEED0 = +SEEDS || 12345;
const SENT = 0xFFF0;                                    // адрес возврата-часовой

function labels(sld) {
  const L = {};
  for (const l of fs.readFileSync(sld, "latin1").split("\n")) {
    const p = l.split("|");
    if (p.length >= 8 && p[6] === "L") { const n = p[7].split(",")[1]; if (n && !(n in L)) L[n] = +p[5]; }
  }
  return L;
}
function machine(rom) {
  const g = {}; g.window = g;
  const fcx = () => ({ createImageData: () => ({ data: new Uint8ClampedArray(4) }), putImageData() {}, getImageData: () => ({ data: new Uint8ClampedArray(4) }), fillRect() {}, drawImage() {} });
  const fc = () => ({ width: 0, height: 0, style: {}, getContext: fcx });
  g.document = { createElement: fc, getElementById: fc };
  g.requestAnimationFrame = () => 0; g.setTimeout = () => 0; g.print = () => 0;
  g.console = { log() {}, debug() {}, warn() {}, error() {}, info() {} };
  const ctx = vm.createContext(g);
  for (const f of ["i8080-js/i8080_trace.js", "i8080-js/i8080_disasm.js", "i8080-js/i8080.js", "src/fili.js", "src/ay.js",
                   "src/fd1793.js", "src/fddimage.js", "src/i8253.js", "src/io.js", "src/keyboard.js", "src/memory.js", "src/sound.js", "src/tv.js"])
    vm.runInContext(fs.readFileSync(base + "/" + f, "utf8"), ctx, { filename: f });
  const memory = new ctx.Memory(), keyboard = new ctx.Keyboard(), timer = new ctx.I8253(), ay = new ctx.AY(),
        floppy = new (ctx.Floppy().FD1793)(), io = new ctx.IO(keyboard, timer, memory, ay, floppy);
  const cpu = new ctx.I8080(memory, io);
  const img = new Uint8Array(fs.readFileSync(rom)).subarray(0, 0x10000);
  memory.init_from_array(img, 0);
  memory.detach_boot && memory.detach_boot();
  const ow = memory.write.bind(memory);
  const m = { cpu, memory, trace: [], on: false };
  memory.write = function (a, w, s) { a &= 0xffff; if (m.on && a < 0x40) m.trace.push([a, w & 0xff]); return ow(a, w, s); };
  m.rd = a => memory.bytes[memory.tobank(a & 0xffff)];
  m.poke = (a, v) => ow(a & 0xffff, v & 0xff, false);
  m.reload = () => { memory.init_from_array(img, 0); memory.detach_boot && memory.detach_boot(); };
  return m;
}
// --- генератор состояния: массив [имяМетки, смещение, байты] ---
function rng(seed) { let s = seed >>> 0; return () => (s = (s * 1664525 + 1013904223) >>> 0) >>> 24; }

// таблица объектов: 40 записей по 32. graphic_no берём из реальных (0 = пусто, иначе 1..40:
// дальше по спрайтовой таблице ходит calc_2d_info, и произвольный номер увёл бы её в мусор).
function objtbl(r) {
  const t = [];
  for (let i = 0; i < 40; i++) {
    t.push(r() < 48 ? 0 : (r() % 40) + 1);          // graphic no
    for (let k = 1; k < 32; k++) t.push(r());
    t[i * 32 + 7] &= 0xCF;                          // WIPE|DRAW снимем — их и ставит процедура
  }
  return t;
}
const GEN = {
  // вход в комнату: таблица спецобъектов (32 записи по 9) и текущий экран игрока
  find_special_objs_here(r) {
    // в таблице special_objs_here всего ДВЕ записи (третья — other_objs_here, она же признак конца):
    // игра гарантирует не больше двух спецобъектов на экран, иначе цикл loc_C583 не остановится.
    const scr = 200, sot = [];
    for (let i = 0; i < 32; i++) {
      const gfx = r() < 64 ? 0 : (r() % 40) + 1;           // четверть записей пустые
      sot.push(gfx, r(), r(), r(), r(), r(), r(), r(), r() % 6);   // байт 8 = чужой экран
    }
    const k = r() % 3;                                     // 0, 1 или 2 объекта на нашем экране
    const pick = []; while (pick.length < k) { const j = r() % 32; if (!pick.includes(j)) pick.push(j); }
    for (const j of pick) { sot[j * 9] = (r() % 40) + 1; sot[j * 9 + 8] = scr; }
    const here = []; for (let i = 0; i < 96; i++) here.push(r());
    const ix = []; for (let i = 0; i < 32; i++) ix.push(r()); ix[8] = scr;
    return { regs: {}, set: [["special_objs_tbl", 0, sot], ["special_objs_here", 0, here],
                                        ["graphic_objs_tbl", 0, ix]],
             ptr: [["z80_ix", "graphic_objs_tbl", 0]] };
  },
  // меню: display_menu -> display_text_list -> print_text_single_colour (8 строк в буфер).
  // suppress_border = 1, чтобы не уходить в print_border/update_screen (железо экрана).
  display_menu(r) {
    const buf = []; for (let i = 0; i < 32 * 192; i++) buf.push(r());
    return { regs: {}, set: [["vidbufpic", 0, buf], ["suppress_border", 0, [1]]] };
  },
  // HUD: display_day -> print_text (пропуск байта атрибута, шрифт day_font)
  display_day(r) {
    const buf = []; for (let i = 0; i < 32 * 192; i++) buf.push(r());
    return { regs: {}, set: [["vidbufpic", 0, buf], ["curr_room_attrib", 0, [r()]]] };
  },
  // «лишняя жизнь»: голова процедуры перекладывает IX/IY и расширяет коробку игрока
  upd_103(r) {
    return { regs: {}, set: [["graphic_objs_tbl", 0, objtbl(r)]],
             ptr: [["z80_ix", "graphic_objs_tbl", 32 * (1 + r() % 39)], ["z80_iy", "graphic_objs_tbl", 32 * (r() % 40)]] };
  },
  // сброс объекта: копия X,Y,Z игрока в объект, подъём Z, пересчёт пиксельных координат.
  // Вход в середину room_to_drop: HL = указатель на запись сбрасываемого объекта, а на стеке
  // лежат два слова, которые снимет done_pickup_drop (их кладёт handle_pickup_drop).
  loc_C0DD(r) {
    return { regs: { hlLabel: ["special_objs_tbl", 9 * (r() % 30)] }, push: [r() | (r() << 8), r() | (r() << 8)],
             set: [["graphic_objs_tbl", 0, objtbl(r)]],
             ptr: [["z80_ix", "graphic_objs_tbl", 32 * (r() % 40)], ["z80_iy", "graphic_objs_tbl", 32 * (r() % 40)]] };
  },
  // WIPE|DRAW объекту в IY (проверяется заодно сохранность IX и IY)
  set_wipe_and_draw_IY(r) {
    return { regs: {}, set: [["graphic_objs_tbl", 0, objtbl(r)]],
             ptr: [["z80_ix", "graphic_objs_tbl", 32 * (r() % 40)], ["z80_iy", "graphic_objs_tbl", 32 * (r() % 40)]] };
  },
  // финальная анимация: два прохода по таблице объектов (пометить 11 объектов, блоки -> искры)
  prepare_final_animation(r) {
    return { regs: {}, set: [["graphic_objs_tbl", 0, objtbl(r)]],
             ptr: [["z80_ix", "graphic_objs_tbl", 32 * (r() % 40)], ["z80_iy", "graphic_objs_tbl", 0]] };
  },
  // ХВОСТ ЗАГРУЗКИ КОМНАТЫ: DE = первый свободный слот таблицы объектов; процедура обязана
  // обнулить ровно хвост от DE до конца таблицы (и в новой сборке ещё посчитать занятые).
  // Сценарные гейты сюда почти не заходят: вход в комнату — 1-2 раза за прогон.
  zero_end_of_graphic_objs_tbl(r) {
    return { regs: { deLabel: ["graphic_objs_tbl", 32 * (r() % 41)] },
             set: [["graphic_objs_tbl", 0, objtbl(r)]] };
  },
  // выход из комнаты: перенести координаты из special_objs_here обратно в special_objs_tbl
  update_special_objs(r) {
    const here = []; for (let i = 0; i < 96; i++) here.push(r());
    for (let i = 0; i < 3; i++) {
      here[i * 32] = r() < 64 ? 0 : (r() % 40) + 1;        // graphic_no
      here[i * 32 + 8] = r() % 6;                          // экран
      here[i * 32 + 16] = 0; here[i * 32 + 17] = 0;        // указатель проставим ниже (по сборке)
    }
    const sot = []; for (let i = 0; i < 288; i++) sot.push(r());
    const slot = [r() % 32, r() % 32, r() < 32 ? -1 : r() % 32];   // -1 = нулевой указатель (баг оригинала)
    return { regs: {}, set: [["special_objs_here", 0, here], ["special_objs_tbl", 0, sot]],
             ptrEntry: slot };
  },
};
// регистры, мёртвые на выходе (вызыватель их сразу перезадаёт) — из сравнения исключаются
const DEAD = {
  // build_screen_objects: сразу call clear_scrn_buffer / call adjust_plyr_xyz_for_room_size
  find_special_objs_here: ["a", "b", "c", "d", "e", "h", "l", "f"],
  update_special_objs: ["a", "b", "c", "d", "e", "h", "l", "f"],
  // retrieve_screen возвращается в build_screen_objects, который сразу делает
  // call find_special_objs_here — все рабочие регистры там перезадаются
  zero_end_of_graphic_objs_tbl: ["a", "b", "c", "h", "l", "f"],
  // вызыватели (menu_loop, game_over, display_status_info) сразу перезадают всё
  display_menu: ["a", "b", "c", "d", "e", "h", "l", "f", "iyn"],
  display_day: ["a", "b", "c", "d", "e", "h", "l", "f", "iyn"],
  // upd_* вызываются из диспетчера update_sprite_loop, который сразу перезадаёт всё
  upd_103: ["a", "b", "c", "d", "e", "h", "l", "f"],
  loc_C0DD: ["a", "b", "c", "d", "e", "h", "l", "f"],
  // единственный вызыватель (loc_C0DD+..., drop_object) сразу делает ld hl, object_carried_2
  set_wipe_and_draw_IY: ["a", "b", "c", "d", "e", "h", "l", "f"],
  // chk_all_objs_in_cauldron сразу делает ret; IY процедура и в оригинале не восстанавливает
  prepare_final_animation: ["a", "b", "c", "d", "e", "h", "l", "f", "iyn"],
};
const CMP = {
  display_menu: [["vidbufpic", 0, 0]],
  display_day: [["vidbufpic", 0, 0]],
  upd_103: [["graphic_objs_tbl", 0, 40 * 32], ["lives", 0, 1]],
  loc_C0DD: [["graphic_objs_tbl", 0, 40 * 32]],
  set_wipe_and_draw_IY: [["graphic_objs_tbl", 0, 40 * 32]],
  prepare_final_animation: [["graphic_objs_tbl", 0, 40 * 32], ["special_objs_here", 0, 96]],
  zero_end_of_graphic_objs_tbl: [["graphic_objs_tbl", 0, 40 * 32]],
  find_special_objs_here: [["special_objs_here", 0, 96], ["special_objs_tbl", 0, 288]],
  update_special_objs: [["special_objs_tbl", 0, 288], ["special_objs_here", 0, 96]],
};

// «картинка буфера»: только строки 32..223 каждого столбца. Строки 224..255/0..31 отданы под
// код (vec_pack_holes) — их нельзя ни забивать случайными байтами, ни сравнивать между сборками.
const PIC = [];
for (let p = 0xA000; p < 0xC000; p += 256) for (let a = p + 32; a < p + 224; a++) PIC.push(a);

function run(m, L, st) {
  const cpu = m.cpu;
  // образ перезаливаем ПЕРЕД каждым состоянием: процедуры пишут самомодифицируемые иммедиаты
  // (doi_prep) и переменные вне сравниваемых регионов, а зависший прогон оставлял бы мусор,
  // из-за которого следующие тесты врут.
  m.reload();
  for (const r of [0, 1, 2, 3, 4, 5, 7]) cpu.set_reg(r, 0);      // регистры с прошлого прогона не переносим
  cpu.sf = cpu.pf = cpu.hf = cpu.zf = cpu.cf = 0;
  for (const [lbl, off, bytes] of st.set) {
    if (lbl === "vidbufpic") { for (let i = 0; i < bytes.length && i < PIC.length; i++) m.poke(PIC[i], bytes[i]); continue; }
    const a = L[lbl]; if (a === undefined) throw new Error("нет метки " + lbl);
    for (let i = 0; i < bytes.length; i++) m.poke(a + off + i, bytes[i]);
  }
  if (st.ptr) for (const [cell, lbl, off] of st.ptr) { const v = L[lbl] + off; m.poke(L[cell], v & 0xff); m.poke(L[cell] + 1, v >> 8); }
  if (st.ptrEntry) for (let i = 0; i < st.ptrEntry.length; i++) {
    const s = st.ptrEntry[i], p = s < 0 ? 0 : L.special_objs_tbl + s * 9;
    m.poke(L.special_objs_here + i * 32 + 16, p & 0xff); m.poke(L.special_objs_here + i * 32 + 17, p >> 8);
  }
  // стек — туда же, куда его ставит сама игра (ld sp, clear_start): он растёт ВНИЗ от переменных.
  // Любой другой адрес рискует лечь в сравниваемый регион: с SP = 0x7DF0 стек вызовов затирал
  // объекты 23-24 таблицы (0x7AE8..0x7FE8) и гейт показывал ложное расхождение.
  cpu.sp = st.regs.sp || L.clear_start; m.poke(cpu.sp - 1, SENT >> 8); m.poke(cpu.sp - 2, SENT & 0xff); cpu.sp -= 2;
  if (st.regs.hlLabel) { const v = L[st.regs.hlLabel[0]] + st.regs.hlLabel[1]; cpu.set_h(v >> 8); cpu.set_l(v & 0xff); }
  if (st.regs.deLabel) { const v = L[st.regs.deLabel[0]] + st.regs.deLabel[1]; cpu.set_d(v >> 8); cpu.set_e(v & 0xff); }
  if (st.push) for (const w of st.push) { m.poke(cpu.sp - 1, w >> 8); m.poke(cpu.sp - 2, w & 0xff); cpu.sp -= 2; }
  cpu.pc = L[LABEL]; cpu.iff = false;
  m.trace = []; m.on = true;
  let n = 0; while (cpu.pc !== SENT && n < 3000000) { cpu.instruction(); n++; }
  m.on = false;
  if (cpu.pc !== SENT) return null;
  const regs = { a: cpu.a(), b: cpu.b(), c: cpu.c(), d: cpu.d(), e: cpu.e(), h: cpu.h(), l: cpu.l(), f: cpu.store_flags(),
                 ix: m.rd(L.z80_ix) | m.rd(L.z80_ix + 1) << 8, iy: m.rd(L.z80_iy) | m.rd(L.z80_iy + 1) << 8 };
  const mem = {};
  for (const [lbl, off, len] of (CMP[LABEL] || [])) { let s = "";
    if (lbl === "vidbufpic") { for (const a of PIC) s += m.rd(a).toString(16).padStart(2, "0"); }
    else for (let i = 0; i < len; i++) s += m.rd(L[lbl] + off + i).toString(16).padStart(2, "0");
    mem[lbl] = s; }
  // регионы под метками сравниваем как есть; IX/IY — как СМЕЩЕНИЕ от базы своей сборки
  regs.ixo = regs.ix - (L.graphic_objs_tbl || 0); regs.iyo = regs.iy;
  return { regs, mem, trace: m.trace.map(([a, v]) => a + ":" + v).join(" "), steps: n };
}

const Lr = labels(SREF), Ln = labels(SNEW);
if (!(LABEL in Lr)) { console.log("метки " + LABEL + " нет в эталоне — сравнивать не с чем"); process.exit(2); }
if (!(LABEL in Ln)) { console.log("метки " + LABEL + " нет в новой сборке"); process.exit(2); }
const mr = machine(RREF), mn = machine(RNEW);
// iyo нормируем: IY у обеих сборок указывает в таблицы, которые могут переехать -> считаем смещение от известных баз
const iyBase = (L) => [["special_objs_tbl", L.special_objs_tbl], ["special_objs_here", L.special_objs_here],
                       ["graphic_objs_tbl", L.graphic_objs_tbl]];
const normIY = (L, v) => { let best = "abs:" + v; for (const [n, a] of iyBase(L)) if (a !== undefined && v >= a && v < a + 512) best = n + "+" + (v - a); return best; };

let bad = 0, hang = 0, stepsR = 0, stepsN = 0;
for (let i = 0; i < N; i++) {
  const st1 = GEN[LABEL](rng(SEED0 + i * 7919));
  const st2 = GEN[LABEL](rng(SEED0 + i * 7919));           // тот же seed -> то же состояние
  const a = run(mr, Lr, st1), b = run(mn, Ln, st2);
  // холодный вызов может уйти в бесконечный цикл (путь через звук/вывод ждёт железа) —
  // если ОБЕ сборки не вернулись, это согласие, а не расхождение
  if (!a && !b) { hang++; continue; }
  if (!a || !b) { console.log(`тест ${i}: не вернулась ${!a ? "эталонная" : "новая"} (вторая вернулась)`); bad++; continue; }
  stepsR += a.steps; stepsN += b.steps;
  a.regs.iyn = normIY(Lr, a.regs.iy); b.regs.iyn = normIY(Ln, b.regs.iy);
  const d = [];
  const dead = new Set(DEAD[LABEL] || []);
  for (const k of ["a", "b", "c", "d", "e", "h", "l", "f", "ixo", "iyn"])
    if (!dead.has(k) && a.regs[k] !== b.regs[k]) d.push(`${k}: ref=${a.regs[k]} new=${b.regs[k]}`);
  for (const k of Object.keys(a.mem)) if (a.mem[k] !== b.mem[k]) {
    const bad = []; for (let j = 0; j * 2 < a.mem[k].length; j++)
      if (a.mem[k].substr(j * 2, 2) !== b.mem[k].substr(j * 2, 2) && bad.length < 12)
        bad.push(`${j}(об.${j >> 5}+${j & 31}) ref=${a.mem[k].substr(j * 2, 2)} new=${b.mem[k].substr(j * 2, 2)}`);
    d.push(`${k}: ` + bad.join(", "));
  }
  if (a.trace !== b.trace) d.push(`нулевая страница: ref=[${a.trace}] new=[${b.trace}]`);
  if (d.length) { bad++; if (bad <= 3) console.log(`тест ${i}: ` + d.join("; ")); }
}
console.log(`${LABEL}: ${N} состояний, расхождений ${bad}` + (hang ? `, обе не вернулись ${hang}` : "") +
            `; инструкций ref=${stepsR} new=${stepsN}` +
            (stepsR ? ` (${(100 * stepsN / stepsR - 100).toFixed(1)} %)` : ""));
process.exit(bad ? 1 : 0);
