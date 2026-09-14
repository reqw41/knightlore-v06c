#!/usr/bin/env node
// Покрытие кода: какие байты кода НИ РАЗУ не исполнялись (меню + вход в комнату + игра
// по нескольким комнатам). Холодный код — кандидат на переписку ради байт.
//   node tools/coverage_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [комнаты|start]
// -> build/coverage.bin (1 байт на адрес: 1 = исполнялся) и таблица «процедура — байт — исполнено».
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||120;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, OBJ=L.graphic_objs_tbl, VIDBUF=0xA000;
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
const IMG=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
const cov=new Uint8Array(0x10000);

function runRoom(room){
  memory.init_from_array(IMG,0);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
  let phase="menu",iter=0,roomSet=(room===null);
  memory.write=function(a,w,s){a&=0xffff;
    if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
    if(phase==="init"&&!roomSet&&a>=VIDBUF&&a<VIDBUF+0x2000){roomSet=true;ow(OBJ+8,room,false);ow(OBJ+40,room,false);}
    if(a===ROOM&&phase==="init"){phase="game";keyboard.matrix[2]&=~1;}
    if(a===SEED2HI&&phase==="game")iter++;
    return ow(a,w,s);};
  // до входа в комнату — с покрытием (меню, инициализация)
  const step=()=>{cov[cpu.pc]=1;const c=(cpu.instruction(),cpu.vcycles);if(cpu.last_opcode===0xd3)io.commit();return c;};
  let frc=0,frames=0,guard=0;
  while(phase!=="game"&&guard++<3e7){const c=step();frc+=c;
    if(frc>=59904){frc-=59904;frames++;if(frames===60)keyboard.matrix[2]|=1;v06c.irq=true;v06c.checkInterrupt();}}
  if(phase!=="game")return false;
  const st=iter; let steps=0;
  while(iter-st<N&&steps<4e7){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}frc+=step();steps++;}
  return true;
}
const rooms=(ROOMS||"start,39,163,133,31,3").split(",").map(s=>s.trim()==="start"?null:+s.trim());
for(const r of rooms){const ok=runRoom(r);console.log(`${r===null?"стартовая":"комната "+r}: ${ok?"ок":"НЕ ДОШЛИ"}`);}
fs.writeFileSync("build/coverage.bin",Buffer.from(cov));

// --- таблица по процедурам (границы из .lst, как в sizemap_vec.py) ---
const RE_B=/^\s*\d+\+?\s+([0-9A-Fa-f]{4})\s((?:\s?[0-9A-Fa-f]{2}){1,4})\s*(.*)$/;
const RE_N=/^\s*\d+\+?\s+([0-9A-Fa-f]{4})\s+(.*)$/;
const INNER=/^(loc_|IL_|ps_|rp_|vb_|us_|bts_|vfw_|vdb_|vpb_|usl_|kl_b6|kl_frv|vec_zx|\.)/;
const size={},hot={},addr={},bytes={};
let cur="(до первой метки)";
for(const line of lst){
  let m=RE_B.exec(line),a,nb,body;
  if(m){a=parseInt(m[1],16);nb=m[2].trim().split(/\s+/).length;body=m[3];}
  else{const m2=RE_N.exec(line);if(!m2)continue;a=parseInt(m2[1],16);nb=0;body=m2[2];}
  const lm=/^([A-Za-z_][A-Za-z0-9_]*):/.exec(body.trim());
  if(lm&&!INNER.test(lm[1])){cur=lm[1];if(!(cur in addr))addr[cur]=a;}
  if(nb){size[cur]=(size[cur]||0)+nb;if(!(cur in addr)||addr[cur]>a)addr[cur]=a;
    let h=0;for(let i=0;i<nb;i++)if(cov[(a+i)&0xffff])h++;hot[cur]=(hot[cur]||0)+h;}
}
const rows=Object.keys(size).map(n=>[n,addr[n],size[n],hot[n]||0]).sort((x,y)=>(y[2]-y[3])-(x[2]-x[3]));
console.log("\nХОЛОДНЫЙ КОД (байт всего / исполнено / холодных), без spr_*:");
let coldTot=0,coldTotAll=0;
for(const [n,a,sz,h] of rows){coldTotAll+=sz-h;
  if(n.startsWith("spr_"))continue; coldTot+=sz-h;
  if(sz-h>=24)console.log(`${n.padEnd(34)} ${a.toString(16).padStart(4,"0")} ${String(sz).padStart(5)} ${String(h).padStart(5)} ${String(sz-h).padStart(5)}`);}
console.log(`\nхолодных байт (без spr_*): ${coldTot}; со спрайтами: ${coldTotAll}`);
