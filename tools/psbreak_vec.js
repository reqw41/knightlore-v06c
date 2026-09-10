#!/usr/bin/env node
// Разбивка ВЫЗОВА print_sprite по фазам (такты по слотам шины, cpu.vcycles):
//   flip      — call flip_sprite (переориентация данных спрайта на месте)
//   hdr       — заголовок print_sprite (расчёт ширины/высоты/клипа по Y, патчи иммедиатов)
//   ps_cw     — клип ширины по столбцу 31
//   vidbuf    — vidbuf_calc_addr_in_bc
//   budget    — vec_ps_budget + vec_di_budget (в т.ч. halt и прерывание внутри него)
//   diprol    — di, сохранение SP, ld sp,hl, ld h,страница, jp в цепочку
//   loop      — сам пиксельный цикл (loc_D7AA .. ret)
//   ret       — ld sp,(tmp_SP)/ei/ret (учтён в loop, показан отдельно нулём)
//   nullexit  — спрайт нулевой ширины: flip_sprite снимает адрес возврата и выходит за вызывателя
// node tools/psbreak_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [комнаты|start]
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
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
const IMG=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
const rd=a=>memory.read(a&0xffff,false);

const PS=L.print_sprite, FLIP=L.flip_sprite, PSCW=L.ps_cw, VIDB=L.vidbuf_calc_addr_in_bc,
      BUD=L.vec_ps_budget, D7AA=L.loc_D7AA, PSSH=L.ps_shpg, IXC=L.z80_ix, OBJ=L.graphic_objs_tbl, VIDBUF=0xA000;
for(const [n,v] of [["print_sprite",PS],["flip_sprite",FLIP],["ps_cw",PSCW],["vidbuf_calc_addr_in_bc",VIDB],
                    ["vec_ps_budget",BUD],["loc_D7AA",D7AA],["ps_shpg",PSSH]])
  if(v===undefined)throw new Error("нет метки "+n);
const PHASES=["flip","hdr","ps_cw","vidbuf","budget","diprol","loop"];
const FLV=L.fl_vrow,FLH=L.fl_hrow;

function runRoom(room){
  memory.init_from_array(IMG,0);
  if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
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

  const st={calls:0,al:0,sh:0,nullexit:0,flipwork:0,total:0,halt:0,iters:0,bytesAl:0,bytesSh:0,
            ph:{},phAl:{},phSh:{},phH:{},haltBud:0,wh:{},nV:0,nH:0,nVH:0,nNone:0,cycV:0,cycH:0,cycN:0,flip0:0};
  for(const p of PHASES){st.ph[p]=0;st.phAl[p]=0;st.phSh[p]=0;st.phH[p]=0;}
  const start=iter; let frc=0,steps=0,tacc=0,frames=0,lost=0;
  let inCall=false,retA=-1,cur=null,curS=-1,subRet=-1,t0=0,flipT0=0,curW=0,curH=0;
  const add=(p,c)=>{st.ph[p]+=c; if(curS===0)st.phAl[p]+=c; else if(curS>0)st.phSh[p]+=c; else st.pend=(st.pend||[]).concat([[p,c]]);};
  let pend=[];
  const addP=(p,c)=>{st.ph[p]+=c; if(curS<0)pend.push([p,c]); else if(curS===0)st.phAl[p]+=c; else st.phSh[p]+=c;};
  const flush=()=>{for(const [p,c] of pend){if(curS===0)st.phAl[p]+=c;else st.phSh[p]+=c;} pend.length=0;};
  while(iter-start<N&&steps<5e7){
    if(frc>=59904){frc-=59904;frames++;if(!cpu.iff)lost++;v06c.irq=true;v06c.checkInterrupt();}
    const pc=cpu.pc;
    if(!inCall){
      if(pc===PS){inCall=true;retA=rd(cpu.sp)|(rd(cpu.sp+1)<<8);cur="flip";curS=-1;pend.length=0;
                  st.calls++;st.fv=false;st.fh=false;}
    }else{
      if(pc===retA){ // конец вызова (обычный ret или nullexit из flip_sprite)
        if(curS<0){st.nullexit++;flush2(st,pend);}
        inCall=false;cur=null;pend.length=0;
      }
      else if(pc===FLIP+3||(cur==="flip"&&pc===PS+3)) {}
      if(inCall){
        if(pc===FLV&&cur==="flip")st.fv=true;
        if(pc===FLH&&cur==="flip")st.fh=true;
        if(cur==="flip"&&pc===PS+3){cur="hdr";
          const d=st.ph.flip-st.flip0; st.flip0=st.ph.flip;
          if(st.fv&&st.fh){st.nVH++;st.cycV+=d;}else if(st.fv){st.nV++;st.cycV+=d;}
          else if(st.fh){st.nH++;st.cycH+=d;}else{st.nNone++;st.cycN+=d;}}
        else if(cur==="hdr"&&pc===PSCW){subRet=rd(cpu.sp)|(rd(cpu.sp+1)<<8);cur="ps_cw";}
        else if(cur==="ps_cw"&&pc===subRet){cur="hdr";}
        else if(cur==="hdr"&&pc===VIDB){subRet=rd(cpu.sp)|(rd(cpu.sp+1)<<8);cur="vidbuf";}
        else if(cur==="vidbuf"&&pc===subRet){cur="hdr";}
        else if(cur==="hdr"&&pc===BUD){subRet=rd(cpu.sp)|(rd(cpu.sp+1)<<8);cur="budget";}
        else if(cur==="budget"&&pc===subRet){cur="diprol";}
        else if(pc===PSSH&&curS<0){ // здесь известны ширина/высота/сдвиг
          const ix=rd(IXC)|(rd(IXC+1)<<8);
          curW=rd(ix+24);curH=rd(ix+25);curS=rd(ix+26)&7;
          const nb=curW*curH;
          if(curS===0){st.al++;st.bytesAl+=nb;}else{st.sh++;st.bytesSh+=nb;}
          const k=curW+"x"+curH;st.wh[k]=(st.wh[k]||0)+1;
          flush();
        }
        else if(cur==="diprol"&&pc===D7AA){cur="loop";}
      }
    }
    const c=(cpu.instruction(),cpu.vcycles);steps++;st.total+=c;frc+=c;
    tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}   // ВИ53 = 1.5 МГц: секундомер бюджета
    const isHalt=(cpu.last_opcode===0x76);
    if(isHalt)st.halt+=c;
    if(inCall&&cur){addP(cur,c); if(isHalt){st.haltBud+=c;st.phH[cur]+=c;}}
    if(cpu.last_opcode===0xd3)io.commit();
  }
  st.iters=iter-start;st.frames=frames;st.lost=lost;
  return st;
}
function flush2(){}

const rooms=(ROOMS||"39,163,31,start").split(",").map(s=>s.trim()==="start"?null:+s.trim());
for(const r of rooms){
  const s=runRoom(r);
  const nm=r===null?"стартовая":"комната "+r;
  if(!s){console.log(`${nm}: не дошли`);continue;}
  const bytes=s.bytesAl+s.bytesSh;
  const totNoHalt=s.total-s.halt;
  let sum=0;for(const p of PHASES)sum+=s.ph[p]-s.phH[p];
  console.log(`\n=== ${nm} === итераций ${s.iters}, тактов/итер ${(s.total/s.iters).toFixed(0)} (${(3e6/(s.total/s.iters)).toFixed(1)} итер/с), halt ${(100*s.halt/s.total).toFixed(1)}%`);
  console.log(`print_sprite: вызовов ${s.calls} (выровн ${s.al}, сдвинут ${s.sh}, nullexit ${s.nullexit}), байт окна ${bytes} (выровн ${s.bytesAl}, сдвинут ${s.bytesSh})`);
  console.log(`ВСЕГО в print_sprite ${sum} = ${(100*sum/totNoHalt).toFixed(1)}% тактов без halt; halt внутри бюджета ${s.haltBud} (${(100*s.haltBud/s.total).toFixed(1)}% всех); кадров ${s.frames}, обратный ход на di ${s.lost}`);
  console.log(`фаза (без halt) тактов  %вызова  /вызов   /байт   выровн/байт  сдвинут/байт  /итер`);
  for(const p of PHASES){
    const v=s.ph[p]-s.phH[p];
    console.log(`${p.padEnd(10)} ${String(v.toFixed(0)).padStart(9)} ${(100*v/sum).toFixed(1).padStart(7)}% ${(v/s.calls).toFixed(1).padStart(7)} ${(v/bytes).toFixed(2).padStart(7)} ${(s.phAl[p]/(s.bytesAl||1)).toFixed(2).padStart(11)} ${(s.phSh[p]/(s.bytesSh||1)).toFixed(2).padStart(13)} ${(v/s.iters).toFixed(0).padStart(7)}`);
  }
  console.log(`flip: без работы ${s.nNone} (${(s.cycN/(s.nNone||1)).toFixed(0)}/вызов, ${(s.cycN/s.iters).toFixed(0)}/итер), только H ${s.nH} (${(s.cycH/(s.nH||1)).toFixed(0)}/вызов, ${(s.cycH/s.iters).toFixed(0)}/итер), с V ${s.nV+s.nVH} (${(s.cycV/((s.nV+s.nVH)||1)).toFixed(0)}/вызов, ${(s.cycV/s.iters).toFixed(0)}/итер)`);
  const top=Object.entries(s.wh).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([k,v])=>`${k}:${v}`).join(" ");
  console.log(`частые окна: ${top}`);
}
