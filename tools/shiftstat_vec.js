#!/usr/bin/env node
// Статистика сдвигов print_sprite: доля выровненных вызовов (x&7==0) против сдвинутых,
// распределение по величине сдвига 1..7, байты окна и такты по путям.
// Нужна, чтобы решить судьбу таблиц сдвига 0xF200..0xFFFF (0xE00 = 3.5К).
//   node tools/shiftstat_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [комнаты через запятую|start]
// Комната "start" = стартовая (без подмены). По умолчанию: start,39,163,133,31,3
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

const PS=L.print_sprite, PSAL=L.ps_al, PSSH=L.ps_shpg, IXC=L.z80_ix, OBJ=L.graphic_objs_tbl, VIDBUF=0xA000;
for(const [n,v] of [["print_sprite",PS],["ps_al",PSAL],["ps_shpg",PSSH],["z80_ix",IXC]])
  if(v===undefined)throw new Error("нет метки "+n);

function runRoom(room){
  memory.init_from_array(IMG,0);
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false); // экран выбора звука: сразу AY
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  // seed_3 += mem[seed_2]: без гашения две сборки играют РАЗНЫЕ сценарии (см. render_gate.sh)
  if(process.env.KLSEED!=="live"){const s3=L.seed_3&0xff,s3h=(L.seed_3>>8)&0xff;
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
  // ---- сбор ----
  const st={calls:0,al:0,sh:0,byAl:new Float64Array(8),cycAl:0,cycSh:0,bytesAl:0,bytesSh:0,
            cycByS:new Float64Array(8),bytesByS:new Float64Array(8),wh:{},total:0,iters:0,halt:0};
  const start=iter; let frc=0,steps=0,tacc=0;
  let inCall=false, retA=-1, cyc0=0, curS=0, halt0=0, loop0=0, loopH0=0; st.halt=0;
  st.loopAl=0; st.loopSh=0; st.loopByS=new Float64Array(8);
  while(iter-start<N&&steps<5e7){
    if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
    const pc=cpu.pc;
    if(pc===PS&&!inCall){inCall=true;retA=rd(cpu.sp)|(rd(cpu.sp+1)<<8);cyc0=st.total;halt0=st.halt;curS=-1;}
    else if(inCall&&pc===PSSH){
      const ix=rd(IXC)|(rd(IXC+1)<<8);
      const w=rd(ix+24), h=rd(ix+25), x=rd(ix+26);
      curS=x&7; st.calls++;
      const nb=w*h;
      if(curS===0){st.al++;st.bytesAl+=nb;}else{st.sh++;st.bytesSh+=nb;}
      st.byAl[curS]++; st.bytesByS[curS]+=nb;
      const k=w+"x"+h; st.wh[k]=(st.wh[k]||0)+1; loop0=st.total; loopH0=st.halt;
    }
    else if(inCall&&pc===retA){inCall=false;const d=(st.total-cyc0)-(st.halt-halt0);
      if(curS>=0){if(curS===0)st.cycAl+=d;else st.cycSh+=d;st.cycByS[curS]+=d;
        const dl=(st.total-loop0)-(st.halt-loopH0);
        if(curS===0)st.loopAl+=dl;else st.loopSh+=dl;st.loopByS[curS]+=dl;}}
    const c=(cpu.instruction(),cpu.vcycles);steps++;st.total+=c;frc+=c;if(cpu.last_opcode===0x76)st.halt+=c;
    tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}   // ВИ53 1.5 МГц: БЕЗ этого секундомер
    // бюджета стоит, vec_di_budget всегда уходит в ei/halt, и ISR, исполнившийся ВНУТРИ вызова print_sprite,
    // попадает в его такты (halt вычитается, а ISR — нет). Именно так родились «151/204 такта на байт» в §14.
    if(cpu.last_opcode===0xd3)io.commit();
  }
  st.iters=iter-start;
  return st;
}

const rooms=(ROOMS||"start,39,163,133,31,3").split(",").map(s=>s.trim()==="start"?null:+s.trim());
const agg={calls:0,al:0,sh:0,halt:0,loopAl:0,loopSh:0,byAl:new Float64Array(8),cycAl:0,cycSh:0,bytesAl:0,bytesSh:0,
           cycByS:new Float64Array(8),bytesByS:new Float64Array(8),total:0,iters:0,wh:{}};
for(const r of rooms){
  const s=runRoom(r);
  const nm=r===null?"стартовая":"комната "+r;
  if(!s){console.log(`${nm}: не дошли до комнаты`);continue;}
  const pct=x=>(100*x/(s.calls||1)).toFixed(1)+"%";
  console.log(`\n=== ${nm} === итераций ${s.iters}, тактов ${s.total} (${(s.total/s.iters).toFixed(0)}/итер), из них halt ${(100*s.halt/s.total).toFixed(1)}% (такты ниже — БЕЗ halt)`);
  console.log(`print_sprite: вызовов ${s.calls} (${(s.calls/s.iters).toFixed(2)}/итер); выровненных ${s.al} (${pct(s.al)}), сдвинутых ${s.sh} (${pct(s.sh)})`);
  console.log(`  по сдвигу: ${[...s.byAl].map((v,i)=>`${i}:${v}(${pct(v)})`).join(" ")}`);
  console.log(`  байт окна: выровн ${s.bytesAl} (${(s.bytesAl/s.iters).toFixed(0)}/итер), сдвинут ${s.bytesSh} (${(s.bytesSh/s.iters).toFixed(0)}/итер)`);
  console.log(`  тактов: выровн ${s.cycAl} (${(100*s.cycAl/(s.total-s.halt)).toFixed(1)}% всех(без halt), ${(s.cycAl/(s.al||1)).toFixed(0)}/вызов, ${(s.cycAl/(s.bytesAl||1)).toFixed(1)}/байт)`);
  console.log(`          сдвинут ${s.cycSh} (${(100*s.cycSh/(s.total-s.halt)).toFixed(1)}% всех, ${(s.cycSh/(s.sh||1)).toFixed(0)}/вызов, ${(s.cycSh/(s.bytesSh||1)).toFixed(1)}/байт)`);
  console.log(`  ПИКСЕЛЬНЫЙ ЦИКЛ (ps_shpg..ret, без halt): выровн ${s.loopAl} (${(s.loopAl/(s.bytesAl||1)).toFixed(1)} такт/байт), сдвинут ${s.loopSh} (${(s.loopSh/(s.bytesSh||1)).toFixed(1)} такт/байт)`);
  console.log(`  цикл по сдвигу такт/байт: ${[...s.loopByS].map((v,i)=>`${i}:${(v/(s.bytesByS[i]||1)).toFixed(1)}`).join(" ")}`);
  console.log(`  такты по сдвигу (весь вызов): ${[...s.cycByS].map((v,i)=>`${i}:${v.toFixed(0)}`).join(" ")}`);
  console.log(`  байты по сдвигу: ${[...s.bytesByS].map((v,i)=>`${i}:${v.toFixed(0)}`).join(" ")}`);
  agg.calls+=s.calls;agg.al+=s.al;agg.sh+=s.sh;agg.cycAl+=s.cycAl;agg.cycSh+=s.cycSh;
  agg.bytesAl+=s.bytesAl;agg.bytesSh+=s.bytesSh;agg.total+=s.total;agg.iters+=s.iters;agg.halt+=s.halt;agg.loopAl+=s.loopAl;agg.loopSh+=s.loopSh;
  for(let i=0;i<8;i++){agg.byAl[i]+=s.byAl[i];agg.cycByS[i]+=s.cycByS[i];agg.bytesByS[i]+=s.bytesByS[i];}
  for(const [k,v] of Object.entries(s.wh))agg.wh[k]=(agg.wh[k]||0)+v;
}
const p=x=>(100*x/(agg.calls||1)).toFixed(1)+"%";
console.log(`\n=== ИТОГО по комнатам ===`);
console.log(`вызовов ${agg.calls}; выровненных ${agg.al} (${p(agg.al)}), сдвинутых ${agg.sh} (${p(agg.sh)})`);
console.log(`по сдвигу: ${[...agg.byAl].map((v,i)=>`${i}:${v}(${p(v)})`).join(" ")}`);
console.log(`байт окна: выровн ${agg.bytesAl} (${(100*agg.bytesAl/(agg.bytesAl+agg.bytesSh)).toFixed(1)}%), сдвинут ${agg.bytesSh} (${(100*agg.bytesSh/(agg.bytesAl+agg.bytesSh)).toFixed(1)}%)`);
console.log(`тактов print_sprite ${(agg.cycAl+agg.cycSh).toFixed(0)} = ${(100*(agg.cycAl+agg.cycSh)/(agg.total-agg.halt)).toFixed(1)}% всех(без halt); выровн ${(100*agg.cycAl/(agg.total-agg.halt)).toFixed(1)}%, сдвинут ${(100*agg.cycSh/(agg.total-agg.halt)).toFixed(1)}%`);
console.log(`пиксельный цикл: выровн ${agg.loopAl} (${(100*agg.loopAl/(agg.total-agg.halt)).toFixed(1)}% всех, ${(agg.loopAl/(agg.bytesAl||1)).toFixed(2)} такт/байт), сдвинут ${agg.loopSh} (${(100*agg.loopSh/(agg.total-agg.halt)).toFixed(1)}% всех, ${(agg.loopSh/(agg.bytesSh||1)).toFixed(2)} такт/байт)`);
console.log(`весь вызов такт/байт: выровн ${(agg.cycAl/(agg.bytesAl||1)).toFixed(2)}, сдвинут ${(agg.cycSh/(agg.bytesSh||1)).toFixed(2)}`);
const top=Object.entries(agg.wh).sort((a,b)=>b[1]-a[1]).slice(0,12).map(([k,v])=>`${k}:${v}`).join(" ");
console.log(`частые окна (ширина x высота): ${top}`);
