#!/usr/bin/env node
// Кто и как часто пишет в строки HUD буфера рендера (плоскость 1, 0xA000..0xBFFF, младший байт = строка).
// Нужен, чтобы решить, можно ли отдать строки HUD плоскости 1 под цвет (второй бит палитры).
//   node tools/hudbuf_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [inputs.txt]
// Ключи: KLROOM, KLGAG, KLROWS=y0,y1 (строки, по умолчанию 32,79), KLCOLS=c0,c1, KLBASE=a000|8000 (плоскость).
// Печатает: записи по процедурам (адрес возврата не нужен — метка по PC), сколько строк HUD буфера
// НЕНУЛЕВЫЕ в момент кадрового прерывания (то есть видны лучу), и то же по столбцам.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||300;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
const fn=Object.entries(L).filter(([n,a])=>a>=0x80).sort((x,y)=>x[1]-y[1]);
const fa=fn.map(x=>x[1]), fnm=fn.map(x=>x[0]);
function lbl(pc){let lo=0,hi=fa.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(fa[m]<=pc){r=m;lo=m+1;}else hi=m-1;}return r<0?"?":fnm[r];}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd;
const lst=fs.readFileSync(ROM.replace(/\.r0m$/,".lst"),"latin1").split("\n");
let SYNC=-1;for(const l of lst){const m=l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s.*\tcall\s+shuffle_objects_required/);if(m){SYNC=parseInt(m[1],16);break;}}
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
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
if(process.env.KLGAG){ const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rd(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]"); ow(at+3,0x00,false); }
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
// Раскладка 1 Вектора (kl_in_t1): O/P поворот, Q вперёд, A прыжок, цифры взять. Имена клавиш
// в сценариях — ZX-логические (z/x поворот, a вперёд, q прыжок). Ряды Вектора: 0 ВК/стрелки,
// 2 цифры 0..7, 4 @ABCDEFG, 5 HIJKLMNO, 6 PQRSTUVW, 7 XYZ[\\]^ПРБ.
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
let y0=32,y1=79; if(process.env.KLROWS){const m=process.env.KLROWS.split(",");y0=+m[0];y1=+m[1];}
let c0=0,c1=31; if(process.env.KLCOLS){const m=process.env.KLCOLS.split(",");c0=+m[0];c1=+m[1];}   // KLCOLS: сузить столбцы
const iterhit=new Map();
const BASE=parseInt(process.env.KLBASE||"a000",16);   // KLBASE: 8000 = экран (плоскость 0), a000 = буфер
let phase="menu",iter=0,steps=0;
const wr=new Map(), wrnz=new Map(); let nw=0,nwnz=0;
const colhit=new Uint32Array(32), rowhit=new Uint32Array(256);
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&rd(L.graphic_objs_tbl+8)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);}
  if(phase==="game"&&a>=BASE&&a<BASE+0x2000){const row=a&0xff,col=(a>>8)&0x1f;
    if(row>=y0&&row<=y1&&col>=c0&&col<=c1){if(w!==0)iterhit.set(iter,(iterhit.get(iter)||0)+1);const n=lbl(cpu.pc); nw++; wr.set(n,(wr.get(n)||0)+1);
      colhit[(a>>8)&0x1f]++; rowhit[row]++;
      if(w!==0){nwnz++; wrnz.set(n,(wrnz.get(n)||0)+1);} }}
  return ow(a,w,s);};
// SP-блиты пишут мимо memory.write? нет: i8080-js push идёт через memory.write. Но pop читает — нам не важно.
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter; nw=0;nwnz=0;wr.clear();wrnz.clear();colhit.fill(0);rowhit.fill(0);
let frc=0,tacc=0,frames=0,dirtyFrames=0,dirtyBytesMax=0,dirtySum=0;
function checkDirty(){ // сколько байт строк HUD буфера ненулевые прямо сейчас (видит луч)
  let n=0; for(let col=c0;col<=c1;col++)for(let r=y0;r<=y1;r++)if(rd(BASE+(col<<8)+r))n++;
  frames++; if(n){dirtyFrames++;dirtySum+=n;if(n>dirtyBytesMax)dirtyBytesMax=n;} }
while(iter-startIter<N&&steps<2e8){
  if(frc>=59904){frc-=59904;checkDirty();v06c.irq=true;v06c.checkInterrupt();}
  const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
  if(cpu.last_opcode===0xd3)io.commit();}
const it=iter-startIter;
console.log(`итераций ${it}, строки буфера ${y0}..${y1}: записей ${nw} (${(nw/it).toFixed(1)} на итерацию), из них ненулевых ${nwnz} (${(nwnz/it).toFixed(1)})`);
console.log("процедура                            записей  ненулевых");
for(const [n,c] of [...wr.entries()].sort((a,b)=>b[1]-a[1]).slice(0,15))console.log(`${n.padEnd(34)} ${String(c).padStart(9)}  ${String(wrnz.get(n)||0).padStart(9)}`);
console.log("столбцы с записями: "+[...colhit.entries()].filter(([i,c])=>c).map(([i,c])=>`${i}:${c}`).join(" "));
console.log("строки с записями:  "+[...rowhit.entries()].filter(([i,c])=>c).map(([i,c])=>`${i}:${c}`).join(" "));
{const k=[...iterhit.keys()].sort((a,b)=>a-b);
 console.log(`итераций с ненулевой записью в окно: ${k.length} из ${it}; первые: ${k.slice(0,12).map(i=>i-startIter+":"+iterhit.get(i)).join(" ")}`);}
console.log(`кадров ${frames}, из них с ненулевым буфером в строках HUD ${dirtyFrames} (${(100*dirtyFrames/frames).toFixed(1)} %), байт максимум ${dirtyBytesMax}, в среднем по грязным ${dirtyFrames?(dirtySum/dirtyFrames).toFixed(1):0}`);
