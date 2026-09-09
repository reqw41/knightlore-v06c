#!/usr/bin/env node
// Дамп живых вызовов процедур на i8080-сборке (lockstep): вход/выход каждого вызова -> build/dump_<label>.jsonl
// node tools/calldump_vec.js build/knightlore_8080.r0m build/knightlore_8080.sld <итераций> <inputs.txt> label[,label...]
// Регионы памяти на запись задаются в RULES (по имени метки): список [имя, адрес-выражение, длина].
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS,LABELS]=process.argv.slice(2); const N=+NS||200;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
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
for(let r=0;r<8;r++)memory.write(L.vkbd+r,0xFF,false);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const rd16=a=>rd(a)|rd(a+1)<<8;
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
function key(k,down){const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
let phase="menu",iter=0;
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);}
  return ow(a,w,s);};
const regs=()=>({a:cpu.a(),b:cpu.b(),c:cpu.c(),d:cpu.d(),e:cpu.e(),h:cpu.h(),l:cpu.l(),f:cpu.store_flags(),sp:cpu.sp,ix:rd16(L.z80_ix),iy:rd16(L.z80_iy)});
const hex=(a,n)=>{let s="";for(let i=0;i<n;i++)s+=rd(a+i).toString(16).padStart(2,"0");return s;};
// ПРАВИЛА: какие регионы писать (вычисляются по регистрам/памяти на входе; на выходе — те же адреса)
const RULES={
  save_2d_info:{regions:r=>[["obj",r.ix,32]]},
  calc_pixel_XY:{regions:r=>[["obj",r.ix,32]]},
  list_objects_to_draw:{regions:r=>[["objtbl",L.graphic_objs_tbl,40*32],["list",L.objects_to_draw,41]]},
  flip_sprite_data:{regions:r=>{const de=r.d<<8|r.e;const w=rd(de)&15,h=rd(de+1);return [["obj",r.ix,32],["spr",de,2+w*2*h]];}},
  get_ptr_object:{regions:r=>[]},
  // вход: объект IX, таблица объектов, спрайт (по sprite_tbl[(ix+0)]); выход: флаги DRAW, поля 24-27, флип спрайта
  set_draw_objs_overlapped:{regions:r=>{const sp=rd16(L.sprite_tbl+2*rd(r.ix));const w=rd(sp)&15,h=rd(sp+1);return [["obj",r.ix,32],["objtbl",L.graphic_objs_tbl,40*32],["spr",sp,2+w*2*h]];}},
  calc_2d_info:{regions:r=>{const sp=rd16(L.sprite_tbl+2*rd(r.ix));const w=rd(sp)&15,h=rd(sp+1);return [["obj",r.ix,32],["spr",sp,2+w*2*h]];}},
  // не процедура, а точка: класс перекрытия C при известных IX/IY (loc_CF62 = ld l,c)
  // --- logic (native-logic) ---
  is_near_to:{regions:r=>[["o1",r.ix,32],["o2",r.iy,32]]},
  do_objs_intersect_on_x:{regions:r=>[["o1",r.ix,32],["o2",r.iy,32]]},
  do_objs_intersect_on_y:{regions:r=>[["o1",r.ix,32],["o2",r.iy,32]]},
  do_objs_intersect_on_z:{regions:r=>[["o1",r.ix,32],["o2",r.iy,32]]},
  chk_plyr_spec_near_arch:{regions:r=>[["o1",r.ix,32],["objtbl",L.graphic_objs_tbl,4*32]]},
  upd_2_4:{regions:r=>[["objtbl",L.graphic_objs_tbl,40*32],["o1",r.ix,32]]},
  update_sprite_loop:{regions:r=>[["objtbl",L.graphic_objs_tbl,40*32]]},
  wipe_next_object:{regions:r=>[["objtbl",L.graphic_objs_tbl,40*32]]},
  loc_CF62:{point:true,regions:r=>[["obj1",r.ix,32],["obj2",r.iy,32]]},
};
const want=LABELS.split(",");
const traps=new Map(); const out={};
for(const n of want){if(!(n in L))throw new Error("нет метки "+n);traps.set(L[n],n);out[n]=fs.createWriteStream(`build/dump_${n}.jsonl`);}
const pending=[]; // {label, ret, sp, entry}
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter; let steps=0; const counts={};
while(iter-startIter<N&&steps<3e8){
  const pc=cpu.pc;
  // выходы: PC == адрес возврата и SP == sp_входа+2
  for(let i=pending.length-1;i>=0;i--){const p=pending[i];if(pc===p.ret&&cpu.sp===p.sp+2){const r=regs();const rec={label:p.label,iter,entry:p.entry,exit:{regs:r,mem:p.entry.memAddrs.map(([n,a,len])=>[n,a,hex(a,len)])}};out[p.label].write(JSON.stringify(rec)+"\n");counts[p.label]=(counts[p.label]||0)+1;pending.splice(i,1);}}
  const lab=traps.get(pc);
  if(lab){const rule=RULES[lab]||{regions:()=>[]};const r=regs();const regions=rule.regions(r);const entry={regs:r,memAddrs:regions.map(([n,a,len])=>[n,a,len]),mem:regions.map(([n,a,len])=>[n,a,hex(a,len)])};
    if(rule.point){out[lab].write(JSON.stringify({label:lab,iter,entry})+"\n");counts[lab]=(counts[lab]||0)+1;}
    else pending.push({label:lab,ret:rd16(cpu.sp),sp:cpu.sp,entry});}
  cpu.instruction();steps++;
}
for(const n of want)out[n].end();
console.log("итераций",iter-startIter,"вызовов:",JSON.stringify(counts));
