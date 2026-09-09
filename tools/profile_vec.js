#!/usr/bin/env node
// Профиль i8080-сборки (lockstep) по процедурам: такты и размер. Шагает по инструкциям на ядре vector06js.
// node tools/profile_vec.js build/knightlore_8080.r0m build/knightlore_8080.sld <итераций> [inputs.txt]   (цели lockstep и vec)
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||300;
const L={}; const lab=[];
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
// функции = метки не loc_/. /il_/IL_/vec_ldir внутренние; отсортированные адреса
const fn=Object.entries(L).filter(([n,a])=>!/^(loc_|\.|IL_|kl_b6|kl_frv|rp_|vb_|us_|bts_|vfw_)/.test(n)&&a>=0x80).sort((x,y)=>x[1]-y[1]);
const isHelper=fn.map(x=>/^il_/.test(x[0]));
const fa=fn.map(x=>x[1]), fnm=fn.map(x=>x[0]);
function idx(pc){let lo=0,hi=fa.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(fa[m]<=pc){r=m;lo=m+1;}else hi=m-1;}return r;}
const VARS0=L.clear_start, SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd;
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
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
// lockstep: виртуальная ZX-матрица vkbd (ряд, бит, активный ноль); vec: матрица Вектора через keyboard.matrix (бит = нажато)
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
const VKEYS={z:[7,0x04],x:[7,0x01],a:[4,0x02],q:[6,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
let phase="menu",iter=0,steps=0,total=0; const cyc=new Float64Array(fa.length), calls=new Float64Array(fa.length);
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);if(process.env.KLDBG&&iter%25===0)console.log(`iter ${iter} kbmat ${[...Array(8)].map((_, r)=>memory.read(0x40+r,false).toString(16).padStart(2,'0')).join(' ')} matrix ${[...keyboard.matrix].map(x=>x.toString(16)).join(' ')} input ${memory.read(L.user_input,false).toString(16)} CW ${io.CW.toString(16)} PA ${io.PA.toString(16)} in2 ${io.input(2).toString(16)} X ${memory.read(L.graphic_objs_tbl+1,false)} Y ${memory.read(L.graphic_objs_tbl+2,false)}`);}
  return ow(a,w,s);};
// до входа в комнату — кадрами; потом — по инструкциям, RST7 раз в 59904 тактов (ISR: клавиатура, звук, палитра)
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter;
if(process.env.KLIO){let n=0;const oi=io.input.bind(io),oo=io.output.bind(io);io.input=function(p){const r=oi(p);if(n++<60)console.log('in',p.toString(16),'->',r.toString(16),'CW',io.CW.toString(16),'PA',io.PA.toString(16));return r;};io.output=function(p,w){if(n++<60)console.log('out',p.toString(16),'<-',w.toString(16));return oo(p,w);};} let cur=-1; const hcyc=new Float64Array(fa.length);
let frc=0; while(iter-startIter<N&&steps<2e8){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();} const pc=cpu.pc;const c=cpu.instruction();steps++;total+=c;frc+=c;if(cpu.last_opcode===0xd3)io.commit();/* без filler вывод в порты не фиксируется */let i=idx(pc);if(i>=0){if(isHelper[i]){if(cur>=0){cyc[cur]+=c;hcyc[cur]+=c;}}else{cur=i;cyc[i]+=c;if(pc===fa[i])calls[i]++;}}}
const iters=iter-startIter; console.log(`итераций ${iters}, инструкций ${steps}, тактов ${total}, тактов/итерацию ${(total/iters).toFixed(0)} (${(3000000/(total/iters)).toFixed(1)} итер/с на 3 МГц)`);
const rows_=[];for(let i=0;i<fa.length;i++)if(cyc[i]>0)rows_.push([fnm[i],cyc[i],(i+1<fa.length?fa[i+1]:0x10000)-fa[i],calls[i],hcyc[i]]);
rows_.sort((a,b)=>b[1]-a[1]);
console.log("процедура                          тактов%   тактов/итер  байт  вызовов/итер  из них IX-хелперы%");
let acc=0;for(const [n,c,sz,cl,hc] of rows_.slice(0,40)){acc+=c;console.log(`${n.padEnd(34)} ${(100*c/total).toFixed(1).padStart(6)}%  ${(c/iters).toFixed(0).padStart(10)}  ${String(sz).padStart(5)}  ${(cl/iters).toFixed(1).padStart(8)}  ${hc>0?(100*hc/c).toFixed(0)+'%':''}`);}
console.log(`(топ-40 = ${(100*acc/total).toFixed(1)} %)`);
