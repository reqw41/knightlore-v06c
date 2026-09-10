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
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
const VKEYS={z:[7,0x04],x:[7,0x01],a:[4,0x02],q:[6,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
// FNV-1a 32 по региону памяти
function h32(a0,a1){let h=0x811c9dc5>>>0;for(let a=a0;a<a1;a++){h=(h^rd(a))>>>0;h=Math.imul(h,0x01000193)>>>0;}return h>>>0;}
const SCR0=0x8000,SCR1=0xA000,END=0xC000;
let phase="menu",iter=0; const lines=[];
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);
    lines.push(`${iter} ${h32(SCR1,END).toString(16).padStart(8,"0")} ${h32(SCR0,SCR1).toString(16).padStart(8,"0")}`);}
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter; let steps=0,frc=0;
while(iter-startIter<N&&steps<3e8){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
  const c=cpu.instruction();steps++;frc+=c;if(cpu.last_opcode===0xd3)io.commit();}
fs.writeFileSync(`build/scr_${TAG}.txt`,lines.join("\n")+"\n");
const dump=Buffer.alloc(END-SCR0);for(let a=SCR0;a<END;a++)dump[a-SCR0]=rd(a);
fs.writeFileSync(`build/scr_${TAG}.bin`,dump);
console.log(`итераций ${iter-startIter}, снимков ${lines.length} -> build/scr_${TAG}.txt, дамп 0x8000..0xBFFF -> build/scr_${TAG}.bin`);
console.log(`итог: буфер ${h32(SCR1,END).toString(16).padStart(8,"0")} экран ${h32(SCR0,SCR1).toString(16).padStart(8,"0")}`);
