#!/usr/bin/env node
// ГЕЙТ КЛАВИАТУРЫ: матрица Вектора -> ряды ZX (vec_zxrows), снимок по состояниям матрицы.
// Ни локстеп, ни гейт картинки этого пути не видят (оба глушат vec_zxbuild), поэтому проверка отдельная.
//   node tools/kbcheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld [метка]
// Пишет build/kb_<метка>.txt: строка на состояние «состояние: 8 байт рядов ZX». Сравнение — diff.
// Главный код после загрузки паркуется в самодельный jp $, работает только ISR: снимок не зависит
// ни от скорости сборки, ни от того, что игра делает с меню.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,TAGA]=process.argv.slice(2);
const TAG=TAGA||ROM.replace(/.*\//,"").replace(/\.r0m$/,"");
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
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
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,1,false);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
for(let f=0;f<90;f++)v06c.oneInterrupt(true);      // загрузка палитры, инициализация звука, меню
// ПАРКОВКА: дальше крутится только обработчик прерывания. 0x0030..0x0032 — дыра перед ORG 0x0038.
memory.write(0x0030,0xC3,false);memory.write(0x0031,0x30,false);memory.write(0x0032,0x00,false);
cpu.pc=0x0030;cpu.sp=0xC000;cpu.iff=true;
const out=[];
function shot(name,mat){
  for(let r=0;r<8;r++)keyboard.matrix[r]=mat[r];
  for(let f=0;f<3;f++)v06c.oneInterrupt(true);
  // биты 5..7 рядов read_port срезает (and 0x1F) — сравниваем только значащую пятёрку
  let s="";for(let r=0;r<8;r++)s+=(rd(L.vec_zxrows+r)&0x1F).toString(16).padStart(2,"0")+" ";
  out.push(`${name}: ${s.trim()}`);
  // vec_rptab: готовые ответы read_port. Проверяем ВСЕ 256 масок: значение обязано быть либо
  // 0xFF («не считано» -> read_port уходит на общий цикл), либо ровно cpl(AND рядов) and 0x1F.
  if(L.vec_rptab!==undefined){
    for(let m=0;m<256;m++){
      let and=0xFF;for(let r=0;r<8;r++)if(!((m>>r)&1))and&=rd(L.vec_zxrows+r);
      const exp=(~and)&0x1F, got=rd(L.vec_rptab+m);
      if(got!==0xFF&&got!==exp)bad.push(`${name}: маска 0x${m.toString(16)} таблица ${got.toString(16)} != ${exp.toString(16)}`);
    }
  }
}
const bad=[];
const zero=[0,0,0,0,0,0,0,0];
shot("пусто",zero);
for(let r=0;r<8;r++)for(let b=0;b<8;b++){const m=zero.slice();m[r]=1<<b;shot(`ряд${r}бит${b}`,m);}
for(let r=0;r<8;r++){const m=zero.slice();m[r]=0xFF;shot(`ряд${r}=FF`,m);}
shot("всё",[255,255,255,255,255,255,255,255]);
// сочетания: стрелки + буквы, стрелки друг с другом, пробел + стрелка
const combos=[[0x10,0,0,0,0,0,0,0x04],[0x50,0,0,0,0,0,0,0],[0xF0,0,0,0,0,0,0,0],
              [0x90,0,0,0,0,0,0,0x80],[0x04,0,0x01,0,0x02,0,0x02,0x05],[0,0,0x3F,0x03,0,0,0,0]];
combos.forEach((c,i)=>shot("сочет"+i,c));
shot("пусто2",zero);
fs.writeFileSync(`build/kb_${TAG}.txt`,out.join("\n")+"\n");
// ЭТАЛОН. Без него гейт проверял только СОГЛАСОВАННОСТЬ vec_rptab с vec_zxrows — то есть таблицу
// саму с собой: любая правка отображения «клавиша Вектора -> ряд ZX» (vec_zxmap -> vec_kbzx)
// проходила молча (поймано самопроверкой tools/gatecheck.py). Теперь снимок сверяется с
// tools/kbref.txt; перезаписать эталон осознанно — KBREF=write.
const REF="tools/kbref.txt", cur=out.join("\n")+"\n";
if(process.env.KBREF==="write"){fs.writeFileSync(REF,cur);console.log("эталон перезаписан: "+REF);}
else if(fs.existsSync(REF)){const ref=fs.readFileSync(REF,"utf8");
  if(ref!==cur){const a=ref.split("\n"),b=cur.split("\n");let n=0;
    for(let i=0;i<Math.max(a.length,b.length);i++)if(a[i]!==b[i]){if(n<8)console.log(`  эталон: ${a[i]}\n  сейчас: ${b[i]}`);n++;}
    console.log(`РАСХОЖДЕНИЕ С ЭТАЛОНОМ ${REF}: строк ${n}`);process.exit(1);}
  console.log(`снимок совпал с эталоном ${REF} (${out.length} состояний)`);}
else console.log(`эталона ${REF} нет — снять его: KBREF=write node tools/kbcheck_vec.js ...`);
const nrp=L.vec_rptab===undefined?0:out.length*256;
const nfill=L.vec_rptab===undefined?0:[...Array(256)].filter((_,m)=>rd(L.vec_rptab+m)!==0xFF).length;console.log(`заполнено записей vec_rptab в конце прогона: ${nfill}`);
console.log(`состояний ${out.length} -> build/kb_${TAG}.txt; проверок vec_rptab ${nrp}, расхождений ${bad.length}`);
if(bad.length){console.log(bad.slice(0,10).join("\n"));process.exit(1);}
