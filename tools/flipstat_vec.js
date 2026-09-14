#!/usr/bin/env node
// Статистика flip_sprite_data: кто зовёт, сколько реально разворачивает, что за спрайты,
// и симуляция кэша развёрнутых спрайтов (ключ «адрес спрайта + ориентация»).
//   node tools/flipstat_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [комнаты|start]
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||200;
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
const IMG=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
const rd=a=>memory.read(a&0xffff,false);
const FSD=L.flip_sprite_data, IXC=L.z80_ix, OBJ=L.graphic_objs_tbl, VIDBUF=0xA000, ROOMV=L.curr_room_attrib;
if(FSD===undefined)throw new Error("нет метки flip_sprite_data");

function runRoom(room){
  memory.init_from_array(IMG,0);
  if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  {const s3=L.seed_3&0xff,s3h=(L.seed_3>>8)&0xff;
   const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h];let at=-1;
   for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rd(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
   if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");memory.write(at+3,0x00,false);}
  const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
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
  const st={total:0,halt:0,iters:0,calls:0,work:0,cyc:0,cycWork:0,byCaller:{},bytes:0,
            distinct:new Set(),seq:[],seqAll:[],szMax:0,szSum:0,vOnly:0,hOnly:0,vh:0,need2:{}};
  const start=iter; let frc=0,steps=0,tacc=0;
  let inCall=false,retA=-1,spE=0,cyc0=0,halt0=0,cur=null;
  while(iter-start<N&&steps<5e7){
    if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
    const pc=cpu.pc;
    if(pc===FSD&&!inCall){
      inCall=true;retA=rd(cpu.sp)|(rd(cpu.sp+1)<<8);spE=cpu.sp;cyc0=st.total;halt0=st.halt;
      const de=cpu.de(); const ix=rd(IXC)|(rd(IXC+1)<<8);
      const b0=rd(de),want=rd(ix+7)&0xC0,have=b0&0xC0,orig=IMG[de]&0xC0;
      const w=b0&0x0F,h=rd(de+1);
      cur={de,want,have,w,h,need:want!==have};
      st.calls++; st.byCaller[retA]=(st.byCaller[retA]||{n:0,work:0,cyc:0});
      st.byCaller[retA].n++;
      if(cur.need){st.work++;st.bytes+=2*w*h;
        const d=(want^have)&0xC0;
        if(d===0xC0)st.vh++;else if(d===0x80)st.vOnly++;else st.hOnly++;
        const sz=2+2*w*h; if(sz>st.szMax)st.szMax=sz;
        st.byCaller[retA].work++;}
      // след для симуляции кэша: только вызовы, где требуемая ориентация != ИСХОДНОЙ (кэш нужен лишь им)
      st.seqAll.push([de,want,w,h,orig]);
      if(want!==orig){st.seq.push([de,want,w,h,retA]);st.distinct.add(de+":"+want);
        st.need2[de+":"+want]=2+2*w*h;}
    } else if(inCall&&pc===retA&&cpu.sp>spE){
      inCall=false;const d=(st.total-cyc0)-(st.halt-halt0);
      st.cyc+=d; if(cur.need)st.cycWork+=d; st.byCaller[retA].cyc+=d; cur=null;
    }
    const c=(cpu.instruction(),cpu.vcycles);steps++;st.total+=c;frc+=c;if(cpu.last_opcode===0x76)st.halt+=c;
    tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xd3)io.commit();
  }
  st.iters=iter-start;
  return st;
}


// ГИБРИД: мастер по-прежнему разворачивается НА МЕСТЕ (быстрый путь have==want сохранён),
// кэш только для случая have!=want; промах = сегодняшняя цена + копия; пул без вытеснения.



// ЧИСТЫЙ кэш: мастер мелких спрайтов НЕ трогаем — копию разворачиваем в слоте.
// Крупные (>maxSz) по-прежнему разворачиваются на месте (мастер меняет ориентацию).
function simPure(seqAll,nslots,maxSz,lru){
  const master={}; let tag=[];
  let fast=0,hit=0,flip=0,big=0;
  for(const [de,want,w,h,orig] of seqAll){
    if(!(de in master))master[de]=orig;
    if(master[de]===want){fast++;continue;}
    const key=de+":"+want, sz=2+2*w*h;
    const i=tag.indexOf(key);
    if(i>=0){hit++;if(lru){tag.splice(i,1);tag.push(key);}continue;}
    flip++;
    if(sz>maxSz){big++;master[de]=want;continue;}   // крупный: разворот мастера на месте
    tag.push(key); if(tag.length>nslots)tag.shift();
  }
  return {fast,hit,flip,big};
}


const rooms=(ROOMS||"start,39,163,133,31,3").split(",").map(s=>s.trim()==="start"?null:+s.trim());
for(const r of rooms){
  const s=runRoom(r);
  const nm=r===null?"стартовая":"комната "+r;
  if(!s){console.log(`${nm}: не дошли`);continue;}
  const net=s.total-s.halt;
  console.log(`\n=== ${nm} === итераций ${s.iters}, тактов/итер ${(s.total/s.iters).toFixed(0)} (без halt ${(net/s.iters).toFixed(0)})`);
  console.log(`flip_sprite_data: вызовов ${s.calls} (${(s.calls/s.iters).toFixed(1)}/итер), из них РАБОТАЮЩИХ ${s.work} (${(s.work/s.iters).toFixed(1)}/итер, ${(100*s.work/s.calls).toFixed(1)}%)`);
  console.log(`  тактов ${s.cyc} = ${(100*s.cyc/net).toFixed(2)}% (без halt); из них на работающих ${s.cycWork} = ${(100*s.cycWork/net).toFixed(2)}%`);
  console.log(`  байт спрайтов развёрнуто ${s.bytes} (${(s.bytes/s.iters).toFixed(0)}/итер), ${(s.cycWork/(s.bytes||1)).toFixed(1)} такт/байт; макс размер спрайта ${s.szMax} Б`);
  console.log(`  тип разворота: только H ${s.hOnly}, только V ${s.vOnly}, V+H ${s.vh}`);
  console.log(`  по вызывающим (адрес возврата): ${Object.entries(s.byCaller).map(([a,v])=>`${(+a).toString(16)}: n=${v.n} work=${v.work} cyc=${v.cyc.toFixed(0)}`).join(" | ")}`);
  const need=Object.values(s.need2);
  console.log(`  вызовов, где нужна НЕ исходная ориентация: ${s.seq.length} (${(s.seq.length/s.iters).toFixed(1)}/итер); различных ключей ${s.distinct.size}, суммарно ${need.reduce((a,b)=>a+b,0)} Б, макс ${Math.max(0,...need)} Б`);
  console.log(`  размеры ключей: ${Object.entries(s.need2).map(([k,v])=>v).sort((a,b)=>b-a).join(" ")}`);
  {const cnt={};const master={};
   for(const [de,want,w,h,orig] of s.seqAll){if(!(de in master))master[de]=orig;
     if(master[de]!==want){const k=de.toString(16)+":"+want.toString(16)+"("+(2+2*w*h)+"Б)";cnt[k]=(cnt[k]||0)+1;master[de]=want;}}
   console.log(`  развороты по ключам: ${Object.entries(cnt).sort((a,b)=>b[1]-a[1]).map(([k,v])=>k+"×"+v).join(" ")}`);}
  for(const [ns,mx,lru] of [[3,126,0],[4,126,0],[5,126,0],[6,126,0],[4,126,1],[5,126,1]]){
    const r=simPure(s.seqAll,ns,mx,lru);
    console.log(`  ЧИСТЫЙ ${ns} слотов по ${mx}Б (=${ns*128}Б)${lru?" LRU":" кольцо"}: разворотов ${r.flip} (${(r.flip/s.iters).toFixed(2)}/итер, ${(100*r.flip/(s.work||1)).toFixed(0)}% от сегодняшних ${s.work}), из них крупных ${r.big}; попаданий ${(r.hit/s.iters).toFixed(2)}/итер, быстрых ${(r.fast/s.iters).toFixed(1)}/итер`);
  }
}
