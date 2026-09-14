#!/usr/bin/env node
// ГЕЙТ РЕНДЕРА: побайтовая сверка буфера (плоскость 1) и экрана (плоскость 0) между двумя сборками.
// Локстеп проверяет только логику (переменные) — картинку он не видит; здесь снимается сама память вывода.
//   node tools/screendiff_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [inputs.txt] [метка]
// Пишет build/scr_<метка>.txt: по строке на итерацию «iter hbuf hscr» (FNV-1a 32) + финальный дамп
// build/scr_<метка>.bin (0x8000..0xBFFF, 16К). Сравнение: diff двух .txt (первая разошедшаяся итерация)
// и cmp двух .bin. Метка по умолчанию — базовое имя ROM + суффикс наличия ввода.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS,TAGA]=process.argv.slice(2); const N=+NS||300;
const WATCH=process.env.KLWATCH?process.env.KLWATCH.split("-").map(x=>parseInt(x,16)):null; let wn=0;
const TAG=TAGA||(ROM.replace(/.*\//,"").replace(/\.r0m$/,"")+(INPUTS?"_alive":"_rest"));
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd;
const lst=fs.readFileSync(ROM.replace(/\.r0m$/,".lst"),"latin1").split("\n");
let SYNC=-1;for(const l of lst){const m=l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s.*\tcall\s+shuffle_objects_required/);if(m){SYNC=parseInt(m[1],16);break;}}
if(SYNC<0)throw new Error("не нашёл call shuffle_objects_required в листинге");
const g={};g.window=g;g.document={createElement:()=>fc(),getElementById:()=>fc()};
function fcx(){return{createImageData:()=>({data:new Uint8ClampedArray(4)}),putImageData:()=>{},getImageData:()=>({data:new Uint8ClampedArray(4)}),fillRect(){},drawImage(){}};}
function fc(){return{width:0,height:0,style:{},getContext:()=>fcx()};}
g.requestAnimationFrame=()=>0;g.setTimeout=()=>0;g.print=()=>0;g.console={log(){},debug(){},warn(){},error(){},info(){}};
const ctx=vm.createContext(g);
for(const f of ["i8080-js/i8080_trace.js","i8080-js/i8080_disasm.js","i8080-js/i8080.js","src/fili.js","src/ay.js","src/fd1793.js","src/fddimage.js","src/i8253.js","src/io.js","src/keyboard.js","src/memory.js","src/sound.js","src/tv.js"])
  vm.runInContext(fs.readFileSync(base+"/"+f,"utf8"),ctx,{filename:f});
const memory=new ctx.Memory(),keyboard=new ctx.Keyboard(),timer=new ctx.I8253(),ay=new ctx.AY(),
      floppy=new(ctx.Floppy().FD1793)(),io=new ctx.IO(keyboard,timer,memory,ay,floppy);
// ДЖОЙСТИК ВЕКТОРА (порт 0x0E, чтение) в vector06js СОБИРАЕТСЯ ИЗ МАТРИЦЫ (стрелки, пробел, W) —
// в стенде это подмешивало бы джойстик к каждому нажатию клавиши, чего на живом Векторе нет
// (там это отдельная микросхема). Подменяем чтение: по умолчанию покой, сценарий задаёт joy(...).
// Биты порта active-low: 0 RIGHT, 1 LEFT, 2 UP, 3 DOWN, 6 FIRE1, 7 FIRE2; биты 4,5 схема держит в 1.
const JOY_R=0x01, JOY_L=0x02, JOY_U=0x04, JOY_D=0x08, JOY_F1=0x40, JOY_F2=0x80, JOY_IDLE=0xFF;
let JOY = JOY_IDLE;
function joy(...bits){ JOY = JOY_IDLE; for(const b of bits) JOY &= ~b & 0xFF; return JOY; }
{ const _rd = io.input.bind(io); io.input = p => ((p & 0xFF) === 0x0E ? JOY : _rd(p)); }
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
// Источник звука задаём ДО старта: проба AY в vec_boot находит чип vector06js и вешает стенд
// на вопросе «SOUND  A AY  T VI53». KL_SND=vi переводит сборку на ВИ53, по умолчанию AY (как было).
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);

cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
// seed_3 += mem[seed_2] + L + H: игра подмешивает в случайное число байт памяти по адресу-счётчику
// (на ZX это экран, на Векторе — собственный код). Любой сдвиг кода меняет всю случайную
// последовательность, и сравнивать картинку двух сборок становится нельзя. Гасим этот байт
// (86 add a,(hl) -> 00 nop) ОДИНАКОВО в обеих сборках: остальная логика и весь вывод не трогаются.
{ const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rd(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");
  memory.write(at+3,0x00,false);
  if(process.env.KLDBG)console.log("seed_3-подмешивание погашено на",at.toString(16)); }
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
// Раскладка 1 Вектора (kl_in_t1): O/P поворот, Q вперёд, A прыжок, цифры взять.
// Имена клавиш в сценариях — ZX-логические (z/x поворот, a вперёд, q прыжок).
// Ряды Вектора: 0 ВК/стрелки, 2 цифры 0..7, 4 @ABCDEFG, 5 HIJKLMNO, 6 PQRSTUVW, 7 XYZ[\]^ПРБ.
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],space:[7,0x80]};
// Ввод должен быть НЕЗАВИСИМ от тактов: иначе две сборки с разной скоростью читают клавиши в разные кадры
// и расходятся законно. Поэтому у цели vec глушим vec_scan и vec_zxbuild (ret) и кладём на границе
// итерации И ряды ZX в vec_zxrows (меню, «любая клавиша», пауза через read_port), И матрицу Вектора
// в vec_kbmat — игрока читает kl_vec_input именно оттуда, а не из рядов ZX.
// KLKB=hw возвращает честный путь через матрицу Вектора и скан в ISR.
const HWKB=process.env.KLKB==="hw";
const ZXROWS=(!HWKB&&VKBD===undefined&&L.vec_zxbuild!==undefined)?L.vec_zxrows:undefined;
const KBMAT=(ZXROWS!==undefined&&L.vec_scan!==undefined)?L.vec_kbmat:undefined;
const kbm=new Uint8Array(8);
if(ZXROWS!==undefined){memory.write(L.vec_zxbuild,0xC9,false);     // ret: ISR больше не трогает ряды
  for(let r=0;r<8;r++)memory.write(ZXROWS+r,0xFF,false);}          // ряды ZX: ничего не нажато
if(KBMAT!==undefined){memory.write(L.vec_scan,0xC9,false);         // ret: ISR больше не трогает матрицу
  for(let r=0;r<8;r++)memory.write(KBMAT+r,0x00,false);}           // матрица Вектора: ничего не нажато
function key(k,down){
  if(VKBD===undefined&&ZXROWS===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  if(KBMAT!==undefined){const [vr,vb]=VKEYS[k];if(down)kbm[vr]|=vb;else kbm[vr]&=~vb;poke(KBMAT+vr,kbm[vr]);}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke((VKBD!==undefined?VKBD:ZXROWS)+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
// FNV-1a 32 по региону памяти
function h32(a0,a1){let h=0x811c9dc5>>>0;for(let a=a0;a<a1;a++){h=(h^rd(a))>>>0;h=Math.imul(h,0x01000193)>>>0;}return h>>>0;}
// Буфер: только строки 32..223 каждого столбца — это и есть картинка. Строки 224..255/0..31 лежат
// вне картинки и отданы под код (vec_pack_holes), сравнивать их между ревизиями бессмысленно.
function hbuf(){let h=0x811c9dc5>>>0;for(let p=SCR1;p<END;p+=256)for(let a=p+32;a<p+224;a++){h=(h^rd(a))>>>0;h=Math.imul(h,0x01000193)>>>0;}return h>>>0;}
const SCR0=0x8000,SCR1=0xA000,END=0xC000;
let phase="menu",iter=0; const lines=[];
memory.write=function(a,w,s){a&=0xffff;
  if(WATCH&&a>=WATCH[0]&&a<=WATCH[1]&&wn++<30)console.log(`WATCH iter ${iter} pc=${cpu.pc.toString(16)} ${a.toString(16)} <- ${w.toString(16)} (was ${memory.read(a,false).toString(16)})`);
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(L.graphic_objs_tbl+8,false)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}  // KLROOM: подмена комнаты при clear_scrn_buffer перед retrieve_screen (как в profile_vec.js)
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);
    lines.push(`${iter} ${hbuf().toString(16).padStart(8,"0")} ${h32(SCR0,SCR1).toString(16).padStart(8,"0")}`);}
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter; let steps=0,frc=0,tacc=0;   // tacc: ВИ53 тикает вдвое медленнее CPU (1.5 МГц) — иначе секундомер бюджета стоит

while(iter-startIter<N&&steps<3e8){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
  const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}if(cpu.last_opcode===0xd3)io.commit();}
fs.writeFileSync(`build/scr_${TAG}.txt`,lines.join("\n")+"\n");
const dump=Buffer.alloc(SCR1-SCR0+32*192);let di=0;
for(let a=SCR0;a<SCR1;a++)dump[di++]=rd(a);                      // плоскость 0 целиком (вне картинки обязана быть нулём)
for(let p=SCR1;p<END;p+=256)for(let a=p+32;a<p+224;a++)dump[di++]=rd(a);   // буфер: только строки картинки
fs.writeFileSync(`build/scr_${TAG}.bin`,dump);
if(process.env.KLFULL){const full=new Uint8Array(0x10000);for(let a=0;a<0x10000;a++)full[a]=memory.read(a,false);fs.writeFileSync(`build/mem_${TAG}.bin`,full);}
console.log(`итераций ${iter-startIter}, снимков ${lines.length} -> build/scr_${TAG}.txt, дамп экран+картинка буфера -> build/scr_${TAG}.bin`);
console.log(`итог: буфер ${h32(SCR1,END).toString(16).padStart(8,"0")} экран ${h32(SCR0,SCR1).toString(16).padStart(8,"0")}`);
