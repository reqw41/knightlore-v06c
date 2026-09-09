#!/usr/bin/env node
// Сторона Вектора для локстепа Knight Lore: headless-ядро vector06js, образ 64К (.r0m, PC=0).
// Граница итерации = запись старшего байта seed_2 (ld (seed_2),hl в loc_B000), как на ZX-стенде.
// Использование: node tools/lockstep_vec.js build/knightlore_8080.r0m build/knightlore_8080.sld N [inputs.txt] [zx_states.bin]
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS,ZX]=process.argv.slice(2); const N=+NS||300;
// LAG: на стенде MAME клавиша, выставленная в тапе итерации N, видна игре с итерации N+1 (латч порта по кадру)
const LAG=+(process.env.KL_LAG||1);
// --- метки ---
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L))L[n]=+p[5];}}
const VARS0=L.clear_start, VARS1=L.clear_end, REC=VARS1-VARS0;
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd;
// адрес инструкции после `call play_audio` (start tune) — точка синхронизации сидов
const lst=fs.readFileSync(ROM.replace(/\.r0m$/,".lst"),"latin1").split("\n");
let SYNC=-1;for(const l of lst){const m=l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s.*\tcall\s+shuffle_objects_required/);if(m){SYNC=parseInt(m[1],16);break;}}
if(SYNC<0)throw new Error("не нашёл call shuffle_objects_required в листинге");
// --- ядро ---
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
const img=new Uint8Array(fs.readFileSync(ROM));
memory.init_from_array(img.subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
for(let r=0;r<8;r++)memory.write(L.vkbd+r,0xFF,false);   // матрица: ничего не нажато
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const ow=memory.write.bind(memory);
const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
// --- ввод: ZX-матрица ---
const KEYS={shift:[0,0],z:[0,1],x:[0,2],c:[0,3],v:[0,4],a:[1,0],s:[1,1],d:[1,2],f:[1,3],g:[1,4],q:[2,0],w:[2,1],e:[2,2],r:[2,3],t:[2,4],
  "1":[3,0],"2":[3,1],"3":[3,2],"4":[3,3],"5":[3,4],"0":[4,0],"9":[4,1],"8":[4,2],"7":[4,3],"6":[4,4],p:[5,0],o:[5,1],i:[5,2],u:[5,3],y:[5,4],
  enter:[6,0],l:[6,1],k:[6,2],j:[6,3],h:[6,4],space:[7,0],sym:[7,1],m:[7,2],n:[7,3],b:[7,4]};
const rows=new Uint8Array(8).fill(0xFF);
function key(k,down){const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={};
if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+LAG]=inputs[+m[1]+LAG]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}
// --- ZX-журнал для сравнения ---
let zx=null,zxN=0; if(ZX&&fs.existsSync(ZX)){zx=fs.readFileSync(ZX);zxN=Math.floor(zx.length/REC);}
// указатели в переменных: зависят от раскладки, сравниваем отдельно
const PTRS=["tmp_SP","gfxbase_8x8","tmp_objects_to_draw","render_obj_1","render_obj_2"];
const IGN=new Set();for(const n of PTRS){const o=L[n]-VARS0;IGN.add(o);IGN.add(o+1);}
const names=Object.entries(L).filter(([n,a])=>a>=VARS0&&a<VARS1).sort((x,y)=>x[1]-y[1]);
const allL=Object.entries(L).sort((x,y)=>x[1]-y[1]);
const nameOf=off=>{let best="?";for(const [n,a] of names){if(a-VARS0<=off)best=n+"+"+(off-(a-VARS0));}return best;};
nameOf.pc=a=>{let b="?";for(const [n,v] of allL){if(v<=a&&!n.startsWith("loc_")&&!n.startsWith("."))b=n+"+"+(a-v).toString(16);}return b;};
// --- состояние прогона ---
let phase="menu",iter=0,frame=0,firstDiff=-1,diffs=0,roomFrame=-1,syncFrame=-1;
const states=[];const idx=[];
memory.write=function(a,w,s){
  a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";syncFrame=frame;}
  if(a===ROOM&&phase==="init"){phase="game";roomFrame=frame;key("0",false);}
  if(a===SEED2HI&&phase==="game"){
    iter++;
    const rec=Buffer.alloc(REC);for(let i=0;i<REC;i++)rec[i]=rd(VARS0+i);
    states.push(rec);idx.push(`${iter} ${frame}`);
    if(zx&&iter<=zxN){const z=zx.subarray((iter-1)*REC,iter*REC);
      let same=true;for(let i=0;i<REC;i++)if(!IGN.has(i)&&z[i]!==rec[i]){same=false;break;}
      if(!same){diffs++;if(firstDiff<0){firstDiff=iter;
        const d=[];for(let i=0;i<REC&&d.length<16;i++)if(!IGN.has(i)&&z[i]!==rec[i])d.push(`${nameOf(i)}(+${i.toString(16)}): zx=${z[i].toString(16)} vec=${rec[i].toString(16)}`);
        console.log(`ПЕРВОЕ РАСХОЖДЕНИЕ на итерации ${iter} (кадр ${frame}):\n  `+d.join("\n  "));}}}
    for(const [k,down] of (inputs[iter]||[]))key(k,down);
    // оракул случайности: seed_3(N+1) = seed_3(N) + mem[seed_2] + L + H, где HL = 0x5678+N (значение seed_2)
    if(zx&&L.kl_rnd_feed&&iter+1<=zxN){const zN=zx[(iter-1)*REC+5],zN1=zx[iter*REC+5];const v=(0x5678+iter)&0xffff;
      poke(L.kl_rnd_feed,(zN1-zN-(v&0xff)-(v>>8))&0xff);}
  }
  return ow(a,w,s);
};
const t0=Date.now();
for(let f=0;f<60;f++){v06c.oneInterrupt(true);frame++;}
key("0",true);
while(iter<N&&frame<20000){v06c.oneInterrupt(true);frame++;}
const dt=(Date.now()-t0)/1000;
console.log(`sync на кадре ${syncFrame}, комната на кадре ${roomFrame} (attr ${rd(ROOM).toString(16)}), итераций ${iter} за ${frame} кадров (${(iter/frame*50).toFixed(1)} итер/с игрового времени), ${dt.toFixed(1)} с реального`);
console.log(`seed_1=${(rd(L.seed_1)|rd(L.seed_1+1)<<8).toString(16)} seed_2=${(rd(L.seed_2)|rd(L.seed_2+1)<<8).toString(16)} seed_3=${rd(L.seed_3).toString(16)} lives=${rd(L.lives)} pc=${cpu.pc.toString(16)} (${nameOf.pc(cpu.pc)})`);
fs.writeFileSync("build/vec_states.bin",Buffer.concat(states));fs.writeFileSync("build/vec_states.idx",idx.join("\n")+"\n");
if(zx)console.log(diffs?`расхождений итераций: ${diffs} из ${Math.min(iter,zxN)}, первое на ${firstDiff}`:`СОВПАДЕНИЕ: ${Math.min(iter,zxN)} итераций байт в байт`);
