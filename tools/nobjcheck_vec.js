#!/usr/bin/env node
// Гейт инварианта kl_nobj: граница «занято/пусто» таблицы объектов не должна быть НИЖЕ
// последнего живого объекта, иначе циклы по объектам его пропустят.
//   KLGAG=1 KLROOM=<комната> node tools/nobjcheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций>
// Проверяется дважды: на границе каждой итерации (kl_nobj против индекса последнего живого)
// и на КАЖДОЙ записи graphic_no != 0 в таблицу (индекс слота против kl_nobj).
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||300;
const L={}; const lab=[];
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
// функции = метки не loc_/. /il_/IL_/vec_ldir внутренние; отсортированные адреса
const fn=Object.entries(L).filter(([n,a])=>!/^(loc_|\.|IL_|kl_b6|kl_frv|rp_|vb_|us_|bts_|vfw_|ps_|vdb_|vpb_|usl_)/.test(n)&&a>=0x80).sort((x,y)=>x[1]-y[1]);
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
// Источник звука задаём ДО старта: проба AY в vec_boot находит чип vector06js и вешает стенд
// на вопросе «SOUND  A AY  T VI53». KL_SND=vi переводит сборку на ВИ53, по умолчанию AY (как было).
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);

cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
// KLGAG=1: погасить seed_3 += mem[seed_2] (86 add a,(hl) -> 00 nop), как это делает screendiff_vec.js.
// Без этого две сборки с разным расположением кода играют РАЗНЫЙ сценарий и «такты/итерацию»
// сравнивать нельзя (см. PORT_RECIPE §17.1). С гашением сценарий один и тот же — сравнение честное.
if(process.env.KLGAG){ const rdb=a=>memory.bytes[memory.tobank(a&0xffff)];
  const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rdb(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");
  ow(at+3,0x00,false); }
// lockstep: виртуальная ZX-матрица vkbd (ряд, бит, активный ноль); vec: матрица Вектора через keyboard.matrix (бит = нажато)
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
// Раскладка 1 Вектора (kl_in_t1): O/P поворот, Q вперёд, A прыжок, цифры взять. Имена клавиш
// в сценариях — ZX-логические (z/x поворот, a вперёд, q прыжок). Ряды Вектора: 0 ВК/стрелки,
// 2 цифры 0..7, 4 @ABCDEFG, 5 HIJKLMNO, 6 PQRSTUVW, 7 XYZ[\\]^ПРБ.
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}

let phase="menu",iter=0,steps=0;
const T=L.graphic_objs_tbl, NB=L.kl_nobj;
if(NB===undefined){console.log("в сборке нет метки kl_nobj — проверять нечего");process.exit(2);}
let worst=999, bad=0, maxlive=-1, wcase=null;
function chk(tag){ const n=memory.read(NB,false); let ml=-1;
  for(let i=0;i<40;i++) if(memory.read(T+i*32,false)!==0) ml=i;
  if(ml>maxlive)maxlive=ml;
  const slack=n-1-ml; if(slack<worst){worst=slack;wcase=[tag,n,ml];}
  if(ml>=n){bad++;if(bad<6)console.log("НАРУШЕНИЕ",tag,": kl_nobj",n,"последний живой",ml);} }

memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(L.graphic_objs_tbl+8,false)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;chk("итерация "+iter);}
  if(phase==="game"&&a>=T&&a<T+1280&&((a-T)%32)===0&&w!==0){const idx=(a-T)/32,n=memory.read(NB,false);
    if(idx>=n){bad++;if(bad<9)console.log("ЗАПИСЬ ВЫШЕ ГРАНИЦЫ: слот",idx,"kl_nobj",n,"pc",cpu.pc.toString(16));}}
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game"){console.log("не дошли до комнаты");process.exit(2);}
const startIter=iter; let frc=0,tacc=0;
while(iter-startIter<N&&steps<6e7){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}if(cpu.last_opcode===0xd3)io.commit();}
console.log(`комната ${process.env.KLROOM||"старт"}: итераций ${iter-startIter}, kl_nobj ${memory.read(NB,false)}, `
  +`последний живой (максимум за прогон) ${maxlive}, минимальный запас ${worst} `
  +`(${wcase?wcase[0]+": kl_nobj "+wcase[1]+", живой "+wcase[2]:"-"}), НАРУШЕНИЙ ${bad}`);
process.exit(bad?1:0);
