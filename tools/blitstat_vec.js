#!/usr/bin/env node
// Статистика вызовов вывода: размеры окон blit_to_screen / vidbuf_fill_window / update_screen / print_sprite.
// Нужна, чтобы выбрать структуру стековых версий (сколько байт в столбце, бывает ли перенос l+B > 255).
//   node tools/blitstat_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [inputs.txt]
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||300;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib;
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
// Источник звука задаём ДО старта: проба AY в vec_boot находит чип vector06js и вешает стенд
// на вопросе «SOUND  A AY  T VI53». KL_SND=vi переводит сборку на ВИ53, по умолчанию AY (как было).
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);

cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
const ZXROWS=L.vec_zxrows; memory.write(L.vec_zxbuild,0xC9,false);
for(let r=0;r<8;r++)memory.write(ZXROWS+r,0xFF,false);
function key(k,down){const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(ZXROWS+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
let phase="menu",iter=0;
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);}
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
const startIter=iter; let steps=0,frc=0;
const stat={};
function note(n,o){const s=stat[n]=stat[n]||{n:0,bytes:0,h:{},w:{},maxl:0,carry:0,sp:{}};s.n++;s.bytes+=o.b*o.c;
  s.h[o.b]=(s.h[o.b]||0)+1;s.w[o.c]=(s.w[o.c]||0)+1;if(o.l!==undefined){if(o.l+o.b>s.maxl)s.maxl=o.l+o.b;if(o.l+o.b>256)s.carry++;}
  if(o.sp!==undefined)s.sp[o.sp>>12]=(s.sp[o.sp>>12]||0)+1;}
const T={};T[L.blit_to_screen]="blit_to_screen";T[L.vidbuf_fill_window]="vidbuf_fill_window";
T[L.vidbuf_clear_window]="vidbuf_clear_window";T[L.update_screen]="update_screen";T[L.print_sprite]="print_sprite";
while(iter-startIter<N&&steps<3e8){
  if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
  const nm=T[cpu.pc];
  if(nm){const b=cpu.b(),c=cpu.c(),l=cpu.l(),e=cpu.e();
    if(nm==="update_screen")note(nm,{b:192,c:32,sp:cpu.sp});
    else if(nm==="print_sprite")note(nm,{b:1,c:1,sp:cpu.sp});
    else if(nm==="vidbuf_clear_window")note(nm,{b:b,c:e,l:l,sp:cpu.sp});
    else note(nm,{b:b,c:c,l:l,sp:cpu.sp});}
  const cc=(cpu.instruction(),cpu.vcycles);steps++;frc+=cc;if(cpu.last_opcode===0xd3)io.commit();}
const its=iter-startIter;
for(const [n,s] of Object.entries(stat)){
  const top=o=>Object.entries(o).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([k,v])=>`${k}:${v}`).join(" ");
  console.log(`${n}: вызовов ${s.n} (${(s.n/its).toFixed(2)}/итер), байт окна всего ${s.bytes} (${(s.bytes/its).toFixed(0)}/итер)`);
  console.log(`   высоты ${top(s.h)}`);
  console.log(`   ширины ${top(s.w)}`);
  console.log(`   max(l+B) ${s.maxl}, переносов за 256: ${s.carry}; SP по страницам ${top(s.sp)}`);
}
