#!/usr/bin/env node
// Перепись всех 256 номеров комнаты (валидных 128): такты, мусор вне картинки плоскости 0,
// счётчик объектов против rendered_objs_cnt, "обратный ход в di" (должен быть 0), не застряла ли комната.
// По образцу tools/roommap_vec.js (подмена комнаты, свежий старт) + tools/profile_vec.js (тиканье ВИ53
// timer.Count и io.commit после OUT — иначе секундомер бюджета стоит и вывод врёт, см. docs/PORT_RECIPE.md §14;
// и счётчик "обратный ход в di" — frames/lost).
//   node tools/roomsweep_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld [итераций=60] [комнаты через запятую|a-b]
// CSV -> build/roomsweep.csv:
//   room,ok,err,attr,iters,stuck,nobj,rendered_cnt,nobj_mismatch,plane0_garbage,lost_di,frames
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||60;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd;
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
const ROMBYTES=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
memory.init_from_array(ROMBYTES,0);memory.detach_boot&&memory.detach_boot();
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);

cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
if(process.env.KLGAG){ const rdb=a=>memory.bytes[memory.tobank(a&0xffff)];
  const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rdb(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");
  ow(at+3,0x00,false); }

const OBJ=L.graphic_objs_tbl, VIDBUF=0xA000, RC=L.rendered_objs_cnt, NB=L.kl_nobj;
if(RC===undefined)console.error("предупреждение: нет метки rendered_objs_cnt — колонка rendered_cnt будет -1");
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];

const rooms=ROOMS?(ROOMS.includes("-")?(([a,b])=>Array.from({length:b-a+1},(_,i)=>a+i))(ROOMS.split("-").map(Number)):ROOMS.split(",").map(Number)):[...Array(256).keys()];
const out=[];
function runRoom(room){
  // свежий старт: образ заново, регистры, матрица клавиатуры
  memory.init_from_array(ROMBYTES,0);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false; for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  let phase="menu",iter=0,roomSet=false,steps=0;
  memory.write=function(a,w,s){a&=0xffff;
    if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
    if(phase==="init"&&!roomSet&&a>=VIDBUF&&a<VIDBUF+0x2000){roomSet=true;ow(OBJ+8,room,false);ow(OBJ+40,room,false);}  // clear_scrn_buffer в build_screen_objects: до retrieve_screen
    if(a===ROOM&&phase==="init"){phase="game";keyboard.matrix[2]&=~1;}
    if(a===SEED2HI&&phase==="game"){iter++;if(RC!==undefined){const v=rd(RC);if(v>maxRendered)maxRendered=v;}}
    return ow(a,w,s);};
  for(let f=0;f<60;f++)v06c.oneInterrupt(true); keyboard.matrix[2]|=1;
  let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
  if(phase!=="game")return {room,ok:false,err:"нет комнаты"};
  let maxRendered=-1;
  const start=iter; let frc=0,tacc=0,frames=0,lost=0;
  const stepLimit=8e7;
  while(iter-start<N&&steps<stepLimit){
    if(frc>=59904){frc-=59904;frames++;if(!cpu.iff)lost++;v06c.irq=true;v06c.checkInterrupt();}
    const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;
    if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xd3)io.commit();
  }
  const itersDone=iter-start, stuck=itersDone<N;
  // (а) мусор вне картинки: плоскость 0, строки памяти 0..31 и 224..255, все 32 столбца — обязаны быть 0
  let garbage=0;
  for(const mrow of [...Array(32).keys(),...Array(32).keys()].map((v,i)=>i<32?v:224+v))
    for(let col=0;col<32;col++) if(rd(0x8000+(col<<8)+mrow)!==0) garbage++;
  // (б) объекты: ненулевой graphic_no (смещение 0 в записи 32 байта, границы занятости) против МАКСИМУМА
  // rendered_objs_cnt за все итерации (счётчик сбрасывается каждый рендер-кадр и считает только ВИДИМЫЕ
  // на экране объекты — меньше nobj в норме; аномалия — если max_rendered==0, т.е. НИКОГДА ничего не рисуется).
  let nobj=0; for(let i=0;i<40;i++) if(rd(OBJ+i*32)!==0) nobj++;
  const neverRendered=(RC!==undefined&&maxRendered===0)?1:0;
  const attr=rd(ROOM);
  return {room,ok:true,err:"",attr,iters:itersDone,stuck,nobj,maxRendered,neverRendered,garbage,lost,frames};
}
for(const room of rooms){
  const x=runRoom(room);
  out.push(x);
  if(!x.ok)console.log(`комната ${String(room).padStart(3)}: ${x.err}`);
  else console.log(`комната ${String(room).padStart(3)} attr ${x.attr.toString(16).padStart(2,'0')} итер ${x.iters}${x.stuck?" ЗАСТРЯЛА":""} `+
    `объектов ${x.nobj} max_rendered ${x.maxRendered}${x.neverRendered?" НИЧЕГО НЕ РИСУЕТСЯ":""} `+
    `мусор_вне_картинки ${x.garbage} обратный_ход_di ${x.lost}${x.lost?" !!!":""} кадров ${x.frames}`);
}
fs.mkdirSync("build",{recursive:true});
const header="room,ok,err,attr,iters,stuck,nobj,max_rendered,never_rendered,plane0_garbage,lost_di,frames\n";
const rows=out.map(x=>x.ok
  ?`${x.room},1,,${x.attr},${x.iters},${x.stuck?1:0},${x.nobj},${x.maxRendered},${x.neverRendered},${x.garbage},${x.lost},${x.frames}`
  :`${x.room},0,${x.err},,,,,,,,,`);
fs.writeFileSync(process.env.OUT||"build/roomsweep.csv",header+rows.join("\n")+"\n");
const valid=out.filter(x=>x.ok);
const anomalies=valid.filter(x=>x.stuck||x.neverRendered||x.garbage>0||x.lost>0||x.nobj===0);
console.log(`\nвсего ${out.length}, валидных (дошли до игры) ${valid.length}, с аномалиями ${anomalies.length}`);
if(anomalies.length)console.log("аномальные комнаты: "+anomalies.map(x=>x.room).join(","));
