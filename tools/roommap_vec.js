#!/usr/bin/env node
// Карта тактов по комнатам: для каждой комнаты 0..127 — тактов/итерацию в покое (цель vec, ядро vector06js).
// node tools/roommap_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [комнаты через запятую]  -> build/roommap.csv
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||200;
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

const OBJ=L.graphic_objs_tbl, VIDBUF=0xA000;
const rooms=ROOMS?(ROOMS.includes("-")?(([a,b])=>Array.from({length:b-a+1},(_,i)=>a+i))(ROOMS.split("-").map(Number)):ROOMS.split(",").map(Number)):[...Array(256).keys()];
const out=[];
function runRoom(room){
  // свежий старт: образ заново, регистры, матрица
  memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  let phase="menu",iter=0,total=0,roomSet=false,steps=0,maxc=0,cur=0;
  memory.write=function(a,w,s){a&=0xffff;
    if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
    if(phase==="init"&&!roomSet&&a>=VIDBUF&&a<VIDBUF+0x2000){roomSet=true;ow(OBJ+8,room,false);ow(OBJ+32+8,room,false);}  // clear_scrn_buffer в build_screen_objects: до retrieve_screen
    if(a===ROOM&&phase==="init"){phase="game";keyboard.matrix[2]&=~1;}
    if(a===SEED2HI&&phase==="game"){iter++;if(cur>maxc)maxc=cur;cur=0;}
    return ow(a,w,s);};
  for(let f=0;f<60;f++)v06c.oneInterrupt(true); keyboard.matrix[2]|=1;
  let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
  if(phase!=="game")return {room,err:"нет комнаты"};
  const start=iter; let frc=0; total=0;
  while(iter-start<N&&steps<5e7){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}const c=cpu.instruction();steps++;total+=c;frc+=c;cur+=c;if(cpu.last_opcode===0xd3)io.commit();}
  let nobj=0;for(let i=0;i<40;i++)if(memory.read(OBJ+i*32,false))nobj++;
  const attr=memory.read(ROOM,false);
  return {room,attr,nobj,avg:total/(iter-start),max:maxc};
}
for(const r of rooms){const x=runRoom(r);if(x.err){console.log(`комната ${r}: ${x.err}`);continue;}
  out.push(x);console.log(`комната ${String(r).padStart(3)} attr ${x.attr.toString(16).padStart(2,'0')} объектов ${String(x.nobj).padStart(2)}  ${x.avg.toFixed(0).padStart(7)} тактов/итер (${(3e6/x.avg).toFixed(1)} итер/с)  макс ${x.max}`);}
fs.mkdirSync("build",{recursive:true});
fs.writeFileSync(process.env.OUT||"build/roommap.csv","room,attr,objects,cycles_per_iter,iter_per_s,max_iter\n"+out.map(x=>`${x.room},${x.attr},${x.nobj},${x.avg.toFixed(0)},${(3e6/x.avg).toFixed(2)},${x.max}`).join("\n")+"\n");
out.sort((a,b)=>b.avg-a.avg); console.log("ТОП-10 тяжёлых:", out.slice(0,10).map(x=>`${x.room}(${x.nobj} об., ${x.avg.toFixed(0)})`).join(", "));
