#!/usr/bin/env node
// Журнал ИГРОВОГО СОСТОЯНИЯ по итерациям для ЛЮБОЙ цели (lockstep и vec) на общем сценарии.
// Нужен, чтобы сверить цель vec (нативный рендер, звук MSX, своя клавиатура) с целью lockstep
// (логика 1:1 с ZX, вывод заглушён) на том, чего локстеп против ZX не покрывает: смена комнат.
//   node tools/logictrace_vec.js <rom> <sld> <итераций> <выход.txt>
// Сценарий детерминирован и одинаков для обеих целей (ключи те же, что у hangwatch_vec.js):
//   KLSEED зерно ходьбы, KLARCH принудительный переход между комнатами, KLDAY смена дня/ночи,
//   KLLIVES бессмертие. seed_3 += mem[seed_2] гасится ВСЕГДА (иначе разная раскладка кода =
//   разная трасса ГСЧ), `ld a,r` в обеих целях = 0.
// Ввод подаётся ДЕТЕРМИНИРОВАННО на границе итерации: у lockstep — в vkbd (ряды ZX),
// у vec — прямо в vec_kbmat (оттуда читает kl_vec_input) и в vec_zxrows (меню/пауза/read_port),
// при заглушённых vec_scan и vec_zxbuild. Строка журнала: «итерация комната FNV(переменные)».
// Из переменных исключены 5 указателей, как в lockstep_vec.js (они законно разные).
"use strict";
const fs=require("fs"),vm=require("vm");
const base=process.env.V06JS||require("path").join(require("os").homedir(),"projects/vector06js");
const [ROM,SLD,NS,OUT]=process.argv.slice(2); const N=+NS||2000;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
const VARS0=L.clear_start, VARS1=L.clear_end, REC=VARS1-VARS0;
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
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
const rdb=a=>memory.bytes[memory.tobank(a&0xffff)];
{ // seed_3 += mem[seed_2] — гасим ВСЕГДА: у двух целей разная раскладка кода
  const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rdb(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  // У цели lockstep этот байт уже подменён ячейкой kl_rnd_feed (её заполняет журнал ZX);
  // без журнала она ноль — ровно то же, что гашение `add a,(hl)` у цели vec.
  if(at<0&&L.kl_rnd_feed===undefined)throw new Error("не нашёл seed_3 += mem[seed_2]");
  if(at>=0)ow(at+3,0x00,false); }
// исключения — те же 5 указателей, что в lockstep_vec.js
const PTRS=["tmp_SP","gfxbase_8x8","tmp_objects_to_draw","render_obj_1","render_obj_2"];
const IGN=new Set();for(const n of PTRS){if(L[n]===undefined)continue;const o=L[n]-VARS0;IGN.add(o);IGN.add(o+1);}
const names=Object.entries(L).filter(([n,a])=>a>=VARS0&&a<VARS1).sort((x,y)=>x[1]-y[1]);
const nameOf=off=>{let best="?";for(const [n,a] of names){if(a-VARS0<=off)best=n+"+"+(off-(a-VARS0));}return best;};

// ---- ввод: одна логика имён клавиш на обе цели ----
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const zrows=new Uint8Array(8).fill(0xFF);
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],space:[7,0x80]};
const kbm=new Uint8Array(8);
const VECT=(VKBD===undefined);          // цель vec
if(VECT){ if(L.vec_scan!==undefined)ow(L.vec_scan,0xC9,false);
          if(L.vec_zxbuild!==undefined)ow(L.vec_zxbuild,0xC9,false);
          for(let r=0;r<8;r++){ow(L.vec_kbmat+r,0,false);ow(L.vec_zxrows+r,0xFF,false);} }
function key(k,down){
  const [r,b]=KEYS[k];
  if(down)zrows[r]&=~(1<<b);else zrows[r]|=(1<<b);
  if(VECT){const [vr,vb]=VKEYS[k];if(down)kbm[vr]|=vb;else kbm[vr]&=~vb;poke(L.vec_kbmat+vr,kbm[vr]);poke(L.vec_zxrows+r,zrows[r]);}
  else poke(VKBD+r,zrows[r]);}

let rndState=(+process.env.KLSEED||12345)>>>0;
function rnd(){rndState=(rndState*1103515245+12345)>>>0;return rndState>>>16;}
let walkHeld=null, walkUntil=0;
function walkStep(it){
  if(it<walkUntil)return;
  if(walkHeld){key(walkHeld,false);walkHeld=null;walkUntil=it+2;return;}
  const r=rnd()%100;
  if(r<64){walkHeld="a";walkUntil=it+60+(rnd()%120);}
  else if(r<78){walkHeld="z";walkUntil=it+2+(rnd()%3);}
  else if(r<92){walkHeld="x";walkUntil=it+2+(rnd()%3);}
  else if(r<97){walkHeld="q";walkUntil=it+2;}
  else {walkHeld="0";walkUntil=it+3;}
  key(walkHeld,true);
}
const DAY=+process.env.KLDAY||0, ARCH=+process.env.KLARCH||0;
let phase="menu",iter=0,steps=0,archForced=0; const lines=[];
function h32(){let h=0x811c9dc5>>>0;for(let i=0;i<REC;i++){if(IGN.has(i))continue;h=(h^rdb(VARS0+i))>>>0;h=Math.imul(h,0x01000193)>>>0;}return h>>>0;}
const recs=[];   // сырые записи: их сверяет logicdiff (по именам переменных), хэш — только для быстрого «равно/нет»
function rec(){const b=Buffer.alloc(REC);for(let i=0;i<REC;i++)b[i]=rdb(VARS0+i);return b;}
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){
    iter++;
    if(process.env.KLLIVES){ow(L.lives,5,false);if(rdb(L.days)>=0x50)ow(L.days,1,false);}
    lines.push(`${iter} ${rdb(L.graphic_objs_tbl+8)} ${h32().toString(16).padStart(8,"0")}`);
    recs.push(rec());
    walkStep(iter);
    if(DAY&&iter%DAY===0)ow(L.sun_moon_scratchpad+26,224,false);
    if(ARCH&&iter%ARCH===0){
      ow(L.graphic_objs_tbl+7,rdb(L.graphic_objs_tbl+7)|1,false);
      ow(L.graphic_objs_tbl+12,rdb(L.graphic_objs_tbl+12)&0x0F,false);
      const d=(archForced++)&3;
      if(d===0)ow(L.graphic_objs_tbl+1,0x00,false);
      else if(d===1)ow(L.graphic_objs_tbl+1,0xFF,false);
      else if(d===2)ow(L.graphic_objs_tbl+2,0xFF,false);
      else ow(L.graphic_objs_tbl+2,0x00,false); }
  }
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter;
let frc=0,tacc=0;
// ОБЪЕДИНЁННОЕ ПОКРЫТИЕ (§31.3): KLCOVU=<файл> — карта исполненных байт, ИЛИ с прежней
// (сценарий ходьбы со сменой комнат и сменой дня/ночи добирает то, куда покой не доходит)
const COVU=process.env.KLCOVU; let cov=null;
if(COVU){cov=new Uint8Array(0x10000);if(fs.existsSync(COVU)){const u=fs.readFileSync(COVU);for(let i=0;i<0x10000;i++)if(u[i])cov[i]=1;}}
while(iter-startIter<N&&steps<4e8){
  if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
  if(cov)cov[cpu.pc]=1;
  const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;
  tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
  if(cpu.last_opcode===0xd3)io.commit();}
if(cov)fs.writeFileSync(COVU,Buffer.from(cov));
fs.writeFileSync(OUT,lines.join("\n")+"\n");
fs.writeFileSync(OUT.replace(/\.txt$/,"")+".bin",Buffer.concat(recs));
fs.writeFileSync(OUT.replace(/\.txt$/,"")+".map",names.map(([n,a])=>`${a-VARS0} ${n}`).join("\n")+"\n"+`REC ${REC}\n`);
console.log(`итераций ${iter-startIter}, шагов ${steps} -> ${OUT}`);
console.log(`комнат посещено: ${new Set(lines.map(l=>l.split(" ")[1])).size}`);
