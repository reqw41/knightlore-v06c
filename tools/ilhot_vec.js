#!/usr/bin/env node
// СКОЛЬКО РАЗ ИСПОЛНЯЕТСЯ КАЖДЫЙ КОНКРЕТНЫЙ call il_* (а не «сколько раз звали хелпер»).
// Нужен для точечного инлайна: инлайнить имеет смысл горячие МЕСТА вызова, а не формы хелпера.
//   KLGAG=1 KLROOM=<комната> node tools/ilhot_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [inputs.txt]
// Печатает: адрес места, хелпер, исполнений на итерацию, размер тела хелпера, цена инлайна в байтах,
// имя ближайшей метки сверху (чтобы видеть, в каком цикле сидит место).
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||300;
const L={}; const lab=[];
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
// функции = метки не loc_/. /il_/IL_/vec_ldir внутренние; отсортированные адреса
const fn=Object.entries(L).filter(([n,a])=>!/^(loc_|\.|IL_|kl_b6|kl_frv|rp_|vb_|us_|bts_|vfw_|ps_|vdb_|vpb_|vp_|usl_)/.test(n)&&a>=0x80).sort((x,y)=>x[1]-y[1]);
const isHelper=fn.map(x=>/^il_/.test(x[0]));
const fa=fn.map(x=>x[1]), fnm=fn.map(x=>x[0]);
function idx(pc){let lo=0,hi=fa.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(fa[m]<=pc){r=m;lo=m+1;}else hi=m-1;}return r;}
const VARS0=L.clear_start, SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd;
const lst=fs.readFileSync(ROM.replace(/\.r0m$/,".lst"),"latin1").split("\n");
// места вызова хелперов: строки листинга вида "<n> <адрес> CD lo hi \tcall il_xxx"
const site={};                       // адрес call -> имя хелпера
for(const l of lst){const m=l.match(/^\s*\d+\+?\s+([0-9A-F]{4})\s+CD [0-9A-F]{2} [0-9A-F]{2}\s+\S*\s*call\s+(il_\w+)/);
  if(m)site[parseInt(m[1],16)]=m[2];}
// размер тела хелпера = до следующей метки il_ (по адресам меток из sld)
const hsz={};
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
var FR=0, iterFr=[], iterCy=[];   // FR: кадров с начала прогона; iterFr/iterCy: снимок на границе итерации (запись seed_2+1)
var TOT=0;const hits={};
let phase="menu",iter=0,steps=0,total=0; const cyc=new Float64Array(fa.length), calls=new Float64Array(fa.length);
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(L.graphic_objs_tbl+8,false)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}  // KLROOM: подмена комнаты при clear_scrn_buffer перед retrieve_screen
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;iterFr.push(FR);iterCy.push(TOT);for(const [k,d] of (inputs[iter]||[]))key(k,d);if(process.env.KLDBG&&iter%25===0)console.log(`iter ${iter} kbmat ${[...Array(8)].map((_, r)=>memory.read(0x40+r,false).toString(16).padStart(2,'0')).join(' ')} matrix ${[...keyboard.matrix].map(x=>x.toString(16)).join(' ')} input ${memory.read(L.user_input,false).toString(16)} CW ${io.CW.toString(16)} PA ${io.PA.toString(16)} in2 ${io.input(2).toString(16)} X ${memory.read(L.graphic_objs_tbl+1,false)} Y ${memory.read(L.graphic_objs_tbl+2,false)}`);}
  return ow(a,w,s);};
// до входа в комнату — кадрами; потом — по инструкциям, RST7 раз в 59904 тактов (ISR: клавиатура, звук, палитра)
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter;
if(process.env.KLIO){let n=0;const oi=io.input.bind(io),oo=io.output.bind(io);io.input=function(p){const r=oi(p);if(n++<60)console.log('in',p.toString(16),'->',r.toString(16),'CW',io.CW.toString(16),'PA',io.PA.toString(16));return r;};io.output=function(p,w){if(n++<60)console.log('out',p.toString(16),'<-',w.toString(16));return oo(p,w);};} let cur=-1; const hcyc=new Float64Array(fa.length);
let frc=0,tacc=0,lost=0,frames=0,hcyc_halt=0;
const PC0=L.vec_pace===undefined?-1:L.vec_pace, PCE=Math.min(...Object.entries(L).filter(([n,a])=>a>L.vec_pace&&!/^vp_/.test(n)).map(([n,a])=>a)), PC1=L.vp_done===undefined?-1:L.vp_done; let pcCode=0,pcHalt=0;   // цена ограничителя темпа: код и halt отдельно   // tacc: ВИ53 тикает вдвое медленнее CPU (1.5 МГц) — иначе секундомер бюджета стоит
 while(iter-startIter<N&&steps<2e8){if(frc>=59904){frc-=59904;frames++;FR++;if(!cpu.iff)lost++;v06c.irq=true;v06c.checkInterrupt();} const pc=cpu.pc;const c=(cpu.instruction(),cpu.vcycles);steps++;total+=c;TOT+=c;frc+=c;if(cpu.last_opcode===0x76)hcyc_halt+=c;if(PC0>=0&&((pc>=PC0&&pc<PCE)||(pc>=PC1&&pc<PC1+3))){if(cpu.last_opcode===0x76)pcHalt+=c;else pcCode+=c;}tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}if(cpu.last_opcode===0xd3)io.commit();/* без filler вывод в порты не фиксируется */if(site[pc]!==undefined)hits[pc]=(hits[pc]||0)+1;let i=idx(pc);if(i>=0){if(isHelper[i]){if(cur>=0){cyc[cur]+=c;hcyc[cur]+=c;}}else{cur=i;cyc[i]+=c;if(pc===fa[i])calls[i]++;}}}
const iters=iter-startIter; console.log(`итераций ${iters}, инструкций ${steps}, тактов ${total}, тактов/итерацию ${(total/iters).toFixed(0)} (${(3000000/(total/iters)).toFixed(1)} итер/с на 3 МГц)`);
// на Векторе кадровое прерывание НЕ защёлкивается: обратный ход внутри di = потерянный кадр
if(PC0>=0)console.log(`ограничитель темпа: ${(pcCode/iters).toFixed(0)} тактов/итер кода + ${(pcHalt/iters).toFixed(0)} тактов/итер halt (${(100*(pcCode+pcHalt)/total).toFixed(1)} % всего)`);
console.log(`кадров ${frames}, обратный ход пришёлся на di: ${lost} (${(100*lost/frames).toFixed(1)} %) — на железе это потерянные прерывания; в halt ${(hcyc_halt/iters).toFixed(0)} тактов/итер (${(100*hcyc_halt/total).toFixed(1)} %)`);
// Разброс длительности итерации в кадрах (метрика сглаженности темпа).
// Первая итерация окна — вход в комнату (десятки кадров), она считается отдельно: «без входа».
{ const d=[]; for(let i=1;i<iterFr.length;i++)d.push(iterFr[i]-iterFr[i-1]);
  const dc=[]; for(let i=1;i<iterCy.length;i++)dc.push((iterCy[i]-iterCy[i-1])/59904);
  const w=d.slice(-N), wc=dc.slice(-N);
  if(w.length>1){ const t=w.slice(1), tc=wc.slice(1);
    const sr=[...t].sort((a,b)=>a-b), med=sr[sr.length>>1];
    const off=t.filter(x=>x!==med).length;
    const mean=t.reduce((a,b)=>a+b,0)/t.length, meanc=tc.reduce((a,b)=>a+b,0)/tc.length;
    const sd=Math.sqrt(tc.reduce((a,b)=>a+(b-meanc)*(b-meanc),0)/tc.length);
    const h={}; for(const x of t)h[x]=(h[x]||0)+1;
    const FPS=3000000/59904;
    console.log(`длительность итерации без входа в комнату (${t.length} итер), кадров: среднее ${mean.toFixed(3)} (= ${(FPS/mean).toFixed(2)} итер/с) мин ${Math.min(...t)} макс ${Math.max(...t)} медиана ${med}, не равны медиане ${(100*off/t.length).toFixed(1)} %`);
    console.log(`  по тактам: среднее ${meanc.toFixed(3)} мин ${Math.min(...tc).toFixed(3)} макс ${Math.max(...tc).toFixed(3)} СКО ${sd.toFixed(3)}`);
    console.log(`  гистограмма кадров: ${Object.keys(h).map(Number).sort((a,b)=>a-b).map(k=>k+":"+h[k]).join(" ")}`);
    console.log(`  узор (первые 60): ${t.slice(0,60).join("")}`); } }
{ const hl=Object.entries(L).filter(([n,a])=>/^il_/.test(n)&&a>=0x100).sort((x,y)=>x[1]-y[1]);
  for(let i=0;i<hl.length;i++){let e=i+1<hl.length?hl[i+1][1]:hl[i][1];hsz[hl[i][0]]=e-hl[i][1];}
  const tot_=Object.values(hits).reduce((a,b)=>a+b,0);
  const list=Object.entries(hits).map(([a,n])=>[+a,n]).sort((x,y)=>y[1]-x[1]);
  console.log(`\nМЕСТА ВЫЗОВА ХЕЛПЕРОВ: ${Object.keys(site).length} статических, исполнений ${(tot_/iters).toFixed(0)} на итерацию`);
  console.log("адрес  хелпер          исп./итер   тело Б  цена инлайна Б  где");
  let sb=0,sn=0;
  for(const [a,n] of list.slice(0,40)){const h=site[a],sz=hsz[h]||0;sb+=sz-4;sn+=n/iters;
    console.log(`${a.toString(16).padStart(4,"0")}   ${h.padEnd(14)} ${(n/iters).toFixed(1).padStart(9)}   ${String(sz).padStart(6)}  ${String(sz-4).padStart(14)}  ${fnm[idx(a)]}+${a-fa[idx(a)]}`);}
  console.log(`(верхние 40 мест: ${sn.toFixed(0)} исполнений/итер, ${sb} байт на инлайн)`);}
const rows_=[];for(let i=0;i<fa.length;i++)if(cyc[i]>0)rows_.push([fnm[i],cyc[i],(i+1<fa.length?fa[i+1]:0x10000)-fa[i],calls[i],hcyc[i]]);
rows_.sort((a,b)=>b[1]-a[1]);
console.log("процедура                          тактов%   тактов/итер  байт  вызовов/итер  из них IX-хелперы%");
let acc=0;for(const [n,c,sz,cl,hc] of rows_.slice(0,40)){acc+=c;console.log(`${n.padEnd(34)} ${(100*c/total).toFixed(1).padStart(6)}%  ${(c/iters).toFixed(0).padStart(10)}  ${String(sz).padStart(5)}  ${(cl/iters).toFixed(1).padStart(8)}  ${hc>0?(100*hc/c).toFixed(0)+'%':''}`);}
console.log(`(топ-40 = ${(100*acc/total).toFixed(1)} %)`);
