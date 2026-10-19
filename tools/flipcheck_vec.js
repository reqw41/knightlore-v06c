#!/usr/bin/env node
// ГЕЙТ разворота спрайтов: холодные вызовы flip_sprite_data по ВСЕМ спрайтам и ВСЕМ четырём
// ориентациям (включая V, которого нет ни в одном сценарном гейте), результат сверяется с
// независимой моделью на JS. Сверяются байты ПО ВОЗВРАЩЁННОМУ DE — так гейт не зависит от
// того, разворачивает ли сборка мастера на месте или отдаёт копию из кэша.
//   node tools/flipcheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld [проходов]
"use strict";
const fs=require("fs"),vm=require("vm");
const base=process.env.V06JS||require("path").join(require("os").homedir(),"projects/vector06js");
const [ROM,SLD,NS]=process.argv.slice(2); const PASSES=+NS||3;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
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
const IMG=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
memory.init_from_array(IMG,0);memory.detach_boot&&memory.detach_boot();
const rd=a=>memory.read(a&0xffff,false), wr=(a,v)=>memory.write(a&0xffff,v&0xff,false);
const FSD=L.flip_sprite_data, IXC=L.z80_ix, STBL=L.sprite_tbl, REV=0xF100;
if(FSD===undefined||STBL===undefined)throw new Error("нет меток flip_sprite_data/sprite_tbl");

// --- прогон до игры: нужны таблицы (reverse_bits_table строит build_lookup_tbls) ---
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(L.vec_snd_force!==undefined)wr(L.vec_snd_force,1);
if(L.vkbd!==undefined)for(let r=0;r<8;r++)wr(L.vkbd+r,0xFF);
let phase="menu";const ow=memory.write.bind(memory);
const lst=fs.readFileSync(ROM.replace(/\.r0m$/,".lst"),"latin1").split("\n");
let SYNC=-1;for(const l of lst){const m=l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s.*\tcall\s+shuffle_objects_required/);if(m){SYNC=parseInt(m[1],16);break;}}
memory.write=function(a,w,s){a&=0xffff;
  if(a===L.curr_room_attrib&&phase==="menu")phase="game";
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true);
keyboard.matrix[2]|=1;                                   // «0» — старт игры
let guard=0;while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
memory.write=ow;
// таблица бит-реверса — из памяти сборки (её строит build_lookup_tbls)
const rev=new Uint8Array(256); for(let i=0;i<256;i++)rev[i]=rd(REV+i);
{let ok=true;for(let i=0;i<256;i++){let r=0;for(let b=0;b<8;b++)if(i&(1<<b))r|=0x80>>b;if(rev[i]!==r)ok=false;}
 if(!ok)throw new Error("reverse_bits_table в памяти не похожа на бит-реверс");}

// --- список спрайтов: адреса из sprite_tbl, пока запись осмысленна ---
// ⚠️ по указателям из sprite_tbl брать НЕЛЬЗЯ: в таблице есть мусорные записи (0x7FF, 0xFBF,
// 0xFFF, 0x1FFF, 0x2FFF), игра их не использует, а гейт разворачивал по ним код «на месте» и
// портил память — падение зависело от того, что случайно лежало по этим адресам после сдвига кода.
const spr=[];
for(const [n,a] of Object.entries(L)) if(/^spr_\d+$/.test(n)){const w=rd(a)&0x0F,h=rd(a+1);
  if(w===0||h===0||h>64)continue; if(!spr.includes(a))spr.push(a);}
spr.sort((x,y)=>x-y);
if(spr.length<20)throw new Error("подозрительно мало спрайтов: "+spr.length);

// --- модель ---
function model(bytes,want){
  const w=bytes[0]&0x0F,h=bytes[1],cur=bytes[0]&0xC0,rw=2*w;
  let rows=[];for(let r=0;r<h;r++)rows.push(bytes.slice(2+r*rw,2+(r+1)*rw));
  let flag=cur;
  if(((cur^want)&0x80)){rows.reverse();flag^=0x80;}
  if(((cur^want)&0x40)){
    rows=rows.map(row=>{const out=new Uint8Array(rw);
      for(let p=0;p<w;p++){out[2*p]=rev[row[2*(w-1-p)]];out[2*p+1]=rev[row[2*(w-1-p)+1]];}return out;});
    flag^=0x40;}
  const out=new Uint8Array(2+rw*h);out[0]=(bytes[0]&0x3F)|flag;out[1]=h;
  for(let r=0;r<h;r++)out.set(rows[r],2+r*rw);
  return out;
}

// --- холодный вызов flip_sprite_data ---
const OBJ=L.graphic_objs_tbl;
const RETA=0x0002;                                        // любой адрес: ловим его как «вернулись»
let bad=0,calls=0;
function call1(de,want){
  wr(IXC,OBJ&0xff);wr(IXC+1,OBJ>>8);
  wr(OBJ+7,(rd(OBJ+7)&0x3F)|want);
  const w=rd(de)&0x0F,h=rd(de+1),n=2+2*w*h;
  const before=new Uint8Array(n);for(let i=0;i<n;i++)before[i]=rd(de+i);
  cpu.sp=0xC000;wr(0xBFFE,RETA&0xff);wr(0xBFFF,RETA>>8);cpu.sp=0xBFFE;
  cpu.set_rp(2,de);                                       // DE
  cpu.pc=FSD;cpu.iff=false;
  let steps=0;while(cpu.pc!==RETA&&steps++<200000)cpu.instruction();
  if(cpu.pc!==RETA)throw new Error(`flip_sprite_data не вернулся: спрайт ${de.toString(16)} (w=${w} h=${h}) -> ориентация ${want.toString(16)}, pc=${cpu.pc.toString(16)}`);
  const got=cpu.de();
  const exp=model(before,want);
  calls++;
  for(let i=0;i<n;i++)if(rd(got+i)!==exp[i]){
    if(bad++<3){console.log(`РАСХОЖДЕНИЕ спрайт ${de.toString(16)} -> ориентация ${want.toString(16)}: `+
      `байт +${i}: получено ${rd(got+i).toString(16)}, ожидалось ${exp[i].toString(16)} (DE вернулся ${got.toString(16)}, n=${n})`);
      console.log(`  мастер до: ${[...before.slice(0,8)].map(x=>x.toString(16)).join(" ")}`);
      console.log(`  мастер сейчас: ${[...Array(8)].map((_,k)=>rd(de+k).toString(16)).join(" ")}`);
      console.log(`  по DE: ${[...Array(8)].map((_,k)=>rd(got+k).toString(16)).join(" ")}`);
      console.log(`  ожидалось: ${[...exp.slice(0,8)].map(x=>x.toString(16)).join(" ")}`);
      }
    return;}
}
const WANT=[0x00,0x40,0x80,0xC0];
let rndState=12345;const rnd=()=>(rndState=(rndState*1103515245+12345)&0x7fffffff)>>>8;
for(let p=0;p<PASSES;p++)
  for(const a of spr)
    for(const w of (p===0?WANT:WANT.slice().sort(()=>((rnd()&1)?1:-1))))
      call1(a,w);
// отдельно: «тасовка» — случайный порядок спрайтов и ориентаций, чтобы кольцо кэша прокрутилось
for(let i=0;i<4000;i++)call1(spr[rnd()%spr.length],WANT[rnd()%4]);
console.log(`flipcheck: спрайтов ${spr.length}, вызовов ${calls}, расхождений ${bad}`);
process.exit(bad?1:0);
