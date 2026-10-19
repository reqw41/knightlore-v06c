#!/usr/bin/env node
// ГЕЙТ: каждая мелодия ОБЯЗАНА кончаться. play_audio крутит `ld a,(msx_play) / or a / jnz` без
// выхода по времени и без клавиши — поток без команды END (7) в потоках каналов повесит игру
// намертво (картинка замрёт, процессор будет работать). Локстеп и гейт картинки этого не видят:
// мелодии game over и финала в сценариях не звучат вовсе.
//   node tools/tunecheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld [предел кадров]
// Каждая мелодия заводится напрямую (msx_play_tune с её заголовком), дальше крутятся кадры с ISR
// (он и тикает проигрыватель), и считается, за сколько кадров сбросился msx_play.
"use strict";
const fs=require("fs"),vm=require("vm");
const base=process.env.V06JS||require("path").join(require("os").homedir(),"projects/vector06js");
const [ROM,SLD,LIM]=process.argv.slice(2); const CAP=+LIM||4000;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
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
const img=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
const rdb=a=>memory.bytes[memory.tobank(a&0xffff)];
const TUNES=["msx_hdr_menu","msx_hdr_start_game","msx_hdr_game_over","msx_hdr_game_complete","msx_hdr_blip"];
let bad=0;
for(const t of TUNES){
  if(L[t]===undefined){console.log(`${t}: нет такой метки — пропуск`);continue;}
  memory.init_from_array(img,0);memory.detach_boot&&memory.detach_boot();
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
  // разогнать загрузчик: доходим до меню (там уже инициализирован звук и работает ISR)
  for(let f=0;f<120;f++)v06c.oneInterrupt(true);
  // завести мелодию напрямую: HL = заголовок, вызов msx_play_tune с возвратом в ловушку `jp $`
  const trap=L.vec_tmp!==undefined?L.vec_tmp:0x0050;
  memory.write(trap,0xC3,false);memory.write(trap+1,trap&0xff,false);memory.write(trap+2,(trap>>8)&0xff,false); // jp $
  cpu.sp=L.clear_start; cpu.pc=L.msx_play_tune;
  memory.write(cpu.sp-1,(trap>>8)&0xff,false);memory.write(cpu.sp-2,trap&0xff,false);cpu.sp-=2;
  const hdr=L[t]; cpu.set_h((hdr>>8)&0xff); cpu.set_l(hdr&0xff);
  let frames=0,frc=0,tacc=0,steps=0,done=-1;
  while(frames<CAP&&steps<3e8){
    if(frc>=59904){frc-=59904;frames++;v06c.irq=true;v06c.checkInterrupt();
      if(rdb(L.msx_play)===0&&frames>2){done=frames;break;}}
    const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;
    tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xd3)io.commit();}
  if(done<0){console.log(`${t}: НЕ КОНЧИЛАСЬ за ${CAP} кадров (${(CAP/50).toFixed(0)} с) — play_audio повиснет`);bad++;}
  else console.log(`${t}: кончилась на кадре ${done} (${(done/50).toFixed(1)} с)`);
}
console.log(bad?`ПРОВАЛ: незавершающихся мелодий ${bad}`:"все мелодии кончаются — play_audio не повиснет");
process.exit(bad?1:0);
