#!/usr/bin/env node
// Длительность КАЖДОГО di-блока по месту его открытия (такты по слотам шины, cpu.vcycles).
// Нужен, чтобы сверить оценки vec_di_budget с реальностью: любой блок длиннее кадра (59904
// такта = 29952 тика ВИ53) гарантированно теряет обратный ход, а блок, чья оценка МЕНЬШЕ
// фактической длительности, теряет его случайно (это и ловит счётчик «обратный ход на di»).
// node tools/distat_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [комнаты|start]
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||200;
const L={},lab=[];
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];lab.push([+p[5],n]);}}}
lab.sort((a,b)=>a[0]-b[0]);
const nameOf=a=>{let lo=0,hi=lab.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(lab[m][0]<=a){r=m;lo=m+1;}else hi=m-1;}
  return r<0?"?":lab[r][1]+(a>lab[r][0]?"+"+(a-lab[r][0]):"");};
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
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
const IMG=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
const rd=a=>memory.read(a&0xffff,false);
const OBJ=L.graphic_objs_tbl, VIDBUF=0xA000;
const stat=new Map();   // адрес di -> {n, max, sum}

function runRoom(room){
  memory.init_from_array(IMG,0);
  if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
  if(process.env.KLSEED!=="live"){const s3=L.seed_3&0xff,s3h=(L.seed_3>>8)&0xff;
    const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h];let at=-1;
    for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rd(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
    if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");memory.write(at+3,0x00,false);}
  let phase="menu",iter=0,roomSet=(room===null);
  memory.write=function(a,w,s){a&=0xffff;
    if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
    if(phase==="init"&&!roomSet&&a>=VIDBUF&&a<VIDBUF+0x2000){roomSet=true;ow(OBJ+8,room,false);ow(OBJ+40,room,false);}
    if(a===ROOM&&phase==="init"){phase="game";keyboard.matrix[2]&=~1;}
    if(a===SEED2HI&&phase==="game")iter++;
    return ow(a,w,s);};
  for(let f=0;f<60;f++)v06c.oneInterrupt(true); keyboard.matrix[2]|=1;
  let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
  if(phase!=="game")return null;
  const start=iter; let frc=0,steps=0,tacc=0,tot=0,diAt=-1,diT=0,frames=0,lost=0,lostAt=new Map();
  while(iter-start<N&&steps<8e7){
    if(frc>=59904){frc-=59904;frames++;if(!cpu.iff){lost++;if(diAt>=0)lostAt.set(diAt,(lostAt.get(diAt)||0)+1);}v06c.irq=true;v06c.checkInterrupt();}
    const pc=cpu.pc, op=rd(pc);
    if(op===0xF3&&cpu.iff){diAt=pc;diT=tot;}                 // di
    const c=(cpu.instruction(),cpu.vcycles);steps++;tot+=c;frc+=c;
    tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xFB&&diAt>=0){                     // ei — блок закрыт
      const d=tot-diT, s=stat.get(diAt)||{n:0,max:0,sum:0};
      s.n++;s.sum+=d;if(d>s.max)s.max=d;stat.set(diAt,s);diAt=-1;}
    if(cpu.last_opcode===0xd3)io.commit();
  }
  return {iters:iter-start,frames,lost,lostAt};
}
const rooms=(ROOMS||"39,163,31,133,3,start").split(",").map(s=>s.trim()==="start"?null:+s.trim());
let tf=0,tl=0; const allLost=new Map();
for(const r of rooms){const s=runRoom(r);if(!s){console.log("не дошли:",r);continue;}
  tf+=s.frames;tl+=s.lost;for(const [k,v] of s.lostAt)allLost.set(k,(allLost.get(k)||0)+v);}
console.log(`кадров ${tf}, обратный ход пришёлся на di: ${tl}`);
for(const [k,v] of [...allLost].sort((a,b)=>b[1]-a[1]))console.log(`  потери в блоке ${k.toString(16)} (${nameOf(k)}): ${v}`);
console.log("\nблок (адрес di)        вызовов   макс.тактов  макс.тиков ВИ53   средн.тактов");
for(const [a,s] of [...stat].sort((x,y)=>y[1].max-x[1].max))
  console.log(`${(a.toString(16)+" "+nameOf(a)).padEnd(28)} ${String(s.n).padStart(8)} ${String(s.max).padStart(12)} ${String(Math.ceil(s.max/2)).padStart(14)} ${(s.sum/s.n).toFixed(0).padStart(14)}`);
