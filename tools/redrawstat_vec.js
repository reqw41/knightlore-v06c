#!/usr/bin/env node
// ПОТОЛОК экономии на перерисовке: сколько вызовов print_sprite не меняют в буфере НИ ОДНОГО байта.
//   KLROOM=39 node tools/redrawstat_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld 200 [inputs.txt]
// Почему это именно потолок: print_sprite пишет (buf & ~mask) | data. Вызов, после которого все
// затронутые байты равны прежним, ничего не меняет в буфере, поэтому его можно выбросить, и все
// остальные вызовы этой итерации получат ровно те же входные данные — буфер совпадёт байт в байт.
// Значит НИКАКОЕ правило «задет» не сэкономит больше, чем эти вызовы; и наоборот, вызов, изменивший
// хоть байт, выбрасывать нельзя. Печатаются также такты таких вызовов — это верхняя граница выигрыша.
"use strict";
const fs=require("fs"),vm=require("vm");
const base=process.env.V06JS||require("path").join(require("os").homedir(),"projects/vector06js");
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||200;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
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
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const rd16=a=>rd(a)|rd(a+1)<<8;
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
// Раскладка 1 Вектора (kl_in_t1): O/P поворот, Q вперёд, A прыжок, цифры взять. Имена клавиш
// в сценариях — ZX-логические (z/x поворот, a вперёд, q прыжок). Ряды Вектора: 0 ВК/стрелки,
// 2 цифры 0..7, 4 @ABCDEFG, 5 HIJKLMNO, 6 PQRSTUVW, 7 XYZ[\\]^ПРБ.
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
let phase="menu",iter=0,inSpr=false,dirty=0,wrote=0;
memory.write=function(a,w,s){a&=0xffff;
  if(inSpr&&a>=0xA000&&a<0xC000){wrote++;if(rd(a)!==(w&0xff))dirty++;}
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(L.graphic_objs_tbl+8,false)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;for(const [k,d] of (inputs[iter]||[]))key(k,d);}
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const PS=L.print_sprite, CLW=L.vidbuf_clear_window;
const startIter=iter; let steps=0,frc=0,tacc=0,cyc=0;
let calls=0,idle=0,cycAll=0,cycIdle=0,clwCalls=0,clwCyc=0,total=0;
let ret=0,sp0=0,c0=0;
const perObj=new Map();   // graphic_no -> [вызовов, пустых]
let curObj=0;
while(iter-startIter<N&&steps<3e8){
  const pc=cpu.pc;
  if(!inSpr&&pc===PS){inSpr=true;dirty=0;wrote=0;ret=rd16(cpu.sp);sp0=cpu.sp;c0=total;curObj=rd(rd16(L.z80_ix));}
  else if(inSpr&&pc===ret&&cpu.sp===sp0+2){
    inSpr=false;calls++;const c=total-c0;cycAll+=c;
    const e=perObj.get(curObj)||[0,0];e[0]++;
    if(dirty===0){idle++;cycIdle+=c;e[1]++;}
    perObj.set(curObj,e);}
  if(pc===CLW){clwCalls++;}
  if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
  const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;total+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}if(cpu.last_opcode===0xd3)io.commit();
}
const it=iter-startIter;
console.log(`комната ${process.env.KLROOM||"стартовая"}: итераций ${it}, тактов/итер ${(total/it).toFixed(0)}`);
console.log(`  print_sprite: ${(calls/it).toFixed(2)} вызовов/итер, ${(cycAll/it).toFixed(0)} тактов/итер (${(100*cycAll/total).toFixed(1)} %)`);
console.log(`  из них ВПУСТУЮ (ни одного изменённого байта): ${(idle/it).toFixed(2)}/итер = ${(100*idle/calls).toFixed(1)} % вызовов, ${(cycIdle/it).toFixed(0)} тактов/итер = ${(100*cycIdle/total).toFixed(1)} % кадра`);
console.log(`  vidbuf_clear_window: ${(clwCalls/it).toFixed(2)}/итер`);
const rows_=[...perObj.entries()].sort((a,b)=>b[1][0]-a[1][0]).slice(0,10);
console.log("  графика  вызовов/итер  впустую/итер");
for(const [gn,[a,b]] of rows_)console.log(`  ${String(gn).padStart(7)}  ${(a/it).toFixed(2).padStart(11)}  ${(b/it).toFixed(2).padStart(12)}`);
