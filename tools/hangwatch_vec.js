#!/usr/bin/env node
// Детектор зависания: длинный прогон с ходьбой и принудительной сменой дня/ночи.
// Считает итерации главного цикла (запись старшего байта seed_2) и ловит момент, когда счётчик
// перестал расти, а процессор продолжает работать: снимает PC-гистограмму, SP, стек, переменные.
//   node tools/hangwatch_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [inputs.txt]
// Ключи: KLROOM=<n> комната, KL_SND=ay|vi источник звука, KLGAG=1 погасить seed_3,
//        KLDAY=<N> каждые N итераций подгонять солнце к смене дня/ночи (превращение),
//        KLWALK=1 генерировать ходьбу (вперёд + повороты + прыжки, детерминированный ГСЧ),
//        KLSTALL=<тактов> порог зависания (по умолчанию 20000000 = ~6.7 с),
//        KLSEED=<n> зерно генератора ходьбы, KLTRACE=1 печатать смену комнат.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const N=+NS||2000;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
const fn=Object.entries(L).filter(([n,a])=>a>=0x40).sort((x,y)=>x[1]-y[1]);
const fa=fn.map(x=>x[1]), fnm=fn.map(x=>x[0]);
function lblOf(pc){let lo=0,hi=fa.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(fa[m]<=pc){r=m;lo=m+1;}else hi=m-1;}
  return r<0?"?":fnm[r]+(pc>fa[r]?"+"+(pc-fa[r]):"");}
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
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
const rdb=a=>memory.bytes[memory.tobank(a&0xffff)];
if(process.env.KLGAG){ const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rdb(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");
  ow(at+3,0x00,false); }
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
// Цель vec: игрока читает kl_vec_input прямо из vec_kbmat, раскладка 1 (kl_in_t1) — O/P поворот,
// Q вперёд, A прыжок, цифры взять. Имена клавиш в сценариях остаются ZX-логическими
// (z/x поворот, a вперёд, q прыжок) — здесь они переводятся в клавиши Вектора.
// Ряды Вектора: 2 цифры 0..7, 4 @ABCDEFG, 5 HIJKLMNO, 6 PQRSTUVW, 7 XYZ[\]^ПРБ.
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}

// ---- генератор ходьбы: держим «вперёд», изредка поворот и прыжок ----
let rndState=(+process.env.KLSEED||12345)>>>0;
function rnd(){rndState=(rndState*1103515245+12345)>>>0;return rndState>>>16;}
const WALK=!!process.env.KLWALK;
let walkHeld=null, walkUntil=0;
// ВК (пауза) и отпускания планируются ПО КАДРАМ: пока игра на паузе, итерации не идут,
// и расписание по итерациям само себя не расклинит.
const frameAct=new Map();
function atFrame(f,fn){const a=frameAct.get(f)||[];a.push(fn);frameAct.set(f,a);}
function vk(down){const [r,b]=[0,0x04];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;}
function walkStep(it){
  if(!WALK)return;
  if(it<walkUntil)return;
  if(walkHeld){key(walkHeld,false);walkHeld=null;walkUntil=it+2;return;}
  const r=rnd()%100;
  if(r<64){walkHeld="a";walkUntil=it+60+(rnd()%120);}         // идти вперёд (долго: так и выходят из комнаты)
  else if(r<78){walkHeld="z";walkUntil=it+2+(rnd()%3);}       // поворот влево
  else if(r<92){walkHeld="x";walkUntil=it+2+(rnd()%3);}       // поворот вправо
  else if(r<97){walkHeld="q";walkUntil=it+2;}                 // прыжок
  else if(process.env.KLPAUSE){                               // ВК: пауза и снятие паузы (по кадрам)
    vk(true);atFrame(frames+4,()=>vk(false));
    atFrame(frames+40,()=>vk(true));atFrame(frames+46,()=>vk(false));
    walkUntil=it+4;return;}
  else {walkHeld="0";walkUntil=it+3;}                          // взять/бросить
  key(walkHeld,true);
}

const STALL=+process.env.KLSTALL||20000000;
const DAY=+process.env.KLDAY||0;
const ARCH=+process.env.KLARCH||0;
let FR=0,TOT=0,phase="menu",iter=0,steps=0,lastIterCy=0,maxGap=0,maxGapIter=0;
let lost=0,frames=0,roomSeen={},lastRoom=-1,roomChanges=0,dayForced=0,archForced=0;
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(L.graphic_objs_tbl+8,false)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){
    iter++;
    if(process.env.KLLIVES){ow(L.lives,5,false);   // бессмертие: длинный прогон не должен кончаться по жизням
      if(rdb(L.days)>=0x50)ow(L.days,1,false);}    // и по дням: на 64-м дне игра сама уходит в game over
    const gap=TOT-lastIterCy; if(gap>maxGap&&iter>2){maxGap=gap;maxGapIter=iter;}
    lastIterCy=TOT;
    for(const [k,d] of (inputs[iter]||[]))key(k,d);
    walkStep(iter);
    if(DAY&&iter%DAY===0){ // подогнать солнце к переключению дня/ночи (превращение)
      ow(L.sun_moon_scratchpad+26,224,false); dayForced++; }
    // Комнату меняют ТОЛЬКО через арку: handle_exit_screen требует бит 0 (ix+7) «рядом с аркой»
    // и нулевой старший ниббл (ix+12) «входим в комнату». Случайная ходьба попадает в арку редко,
    // поэтому в стенде флаг взводится принудительно — сам переход дальше делает игра, по своим
    // проверкам границ комнаты и направления героя.
    // Условие выхода — координата у края комнаты в ТУ сторону, куда смотрит герой
    // (screen_west X=0, screen_east X=255, screen_north Y=255, screen_south Y=0), поэтому
    // край подставляется по очереди на все четыре стороны: сработает та, что совпала с лицом.
    if(ARCH&&iter%ARCH===0){
      ow(L.graphic_objs_tbl+7,rdb(L.graphic_objs_tbl+7)|1,false);
      ow(L.graphic_objs_tbl+12,rdb(L.graphic_objs_tbl+12)&0x0F,false);
      const d=(archForced++)&3;
      if(d===0)ow(L.graphic_objs_tbl+1,0x00,false);
      else if(d===1)ow(L.graphic_objs_tbl+1,0xFF,false);
      else if(d===2)ow(L.graphic_objs_tbl+2,0xFF,false);
      else ow(L.graphic_objs_tbl+2,0x00,false); }
    const rm=rdb(L.graphic_objs_tbl+8);
    if(rm!==lastRoom){if(lastRoom>=0){roomChanges++;if(process.env.KLTRACE)console.log(`  итер ${iter}: комната ${lastRoom} -> ${rm}`);}lastRoom=rm;roomSeen[rm]=(roomSeen[rm]||0)+1;}
  }
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter;
let frc=0,tacc=0,hcyc=0;
let hung=false;
const VARS=["transform_flag_graphic","days","curr_room_attrib","all_objs_in_cauldron","lives",
            "msx_play","msx_vic0","msx_end","pace_el","pace_acc","vec_palreq","user_input",
            "vec_kbchg","rising_blocks_z","game_state","kl_nobj","rendered_objs_cnt","objs_wiped_cnt"];
const stalls=[];
function dumpState(tag){
  console.log(`\n===== ${tag} =====`);
  console.log(`итерация ${iter}, шагов ${steps}, тактов ${TOT}, кадров ${frames}`);
  console.log(`PC=${cpu.pc.toString(16).padStart(4,"0")} (${lblOf(cpu.pc)})  SP=${cpu.sp.toString(16)}  iff=${cpu.iff}`);
  let st="";for(let i=0;i<16;i++){const a=(cpu.sp+i*2)&0xffff;const v=rdb(a)|(rdb(a+1)<<8);st+=` ${v.toString(16).padStart(4,"0")}(${lblOf(v)})`;}
  console.log("стек:"+st);
  console.log("переменные: "+VARS.filter(v=>L[v]!==undefined).map(v=>`${v}=${rdb(L[v])}`).join(" "));
  if(L.msx_ch!==undefined)console.log("msx_ch: "+[...Array(21)].map((_,i)=>rdb(L.msx_ch+i).toString(16).padStart(2,"0")).join(" "));
  if(L.msx_sfx!==undefined)console.log("msx_sfx: "+[...Array(12)].map((_,i)=>rdb(L.msx_sfx+i).toString(16).padStart(2,"0")).join(" "));
  // PC-гистограмма за следующие 300000 шагов
  const h={};let hs=0;
  for(let i=0;i<300000;i++){if(frc>=59904){frc-=59904;frames++;FR++;if(!cpu.iff)lost++;v06c.irq=true;v06c.checkInterrupt();const fa_=frameAct.get(frames);if(fa_){for(const f_ of fa_)f_();frameAct.delete(frames);}}
    const pc=cpu.pc;h[pc]=(h[pc]||0)+1;hs++;const c=(cpu.instruction(),cpu.vcycles);TOT+=c;frc+=c;
    tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}if(cpu.last_opcode===0xd3)io.commit();}
  const top=Object.entries(h).sort((a,b)=>b[1]-a[1]).slice(0,15);
  console.log("PC-гистограмма (300000 шагов):");
  for(const [pc,n] of top)console.log(`  ${(+pc).toString(16).padStart(4,"0")} ${lblOf(+pc).padEnd(28)} ${(100*n/hs).toFixed(1)}%`);
  console.log(`итераций за эти 300000 шагов: счётчик = ${iter}`);
}
const MENU=L.start_menu, LOSE=L.lose_life;
let deaths=0, menuHit=false, lastLives=rdb(L.lives);
while(iter-startIter<N&&steps<3e9){
  if(frc>=59904){frc-=59904;frames++;FR++;if(!cpu.iff)lost++;v06c.irq=true;v06c.checkInterrupt();const fa_=frameAct.get(frames);if(fa_){for(const f_ of fa_)f_();frameAct.delete(frames);}}
  const pc=cpu.pc;
  if(pc===LOSE){deaths++;if(process.env.KLTRACE)console.log(`  итер ${iter}: lose_life, lives=${rdb(L.lives)}`);}
  if(pc===MENU){menuHit=true;console.log(`  итер ${iter}: ВОЗВРАТ В МЕНЮ (game over), смертей ${deaths}`);break;}
  const c=(cpu.instruction(),cpu.vcycles);steps++;TOT+=c;frc+=c;
  if(cpu.last_opcode===0x76)hcyc+=c;
  tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
  if(cpu.last_opcode===0xd3)io.commit();
  // Итерации встали (game over, пауза, отрисовка) — отпустить клавиши: живой игрок не держит
  // клавишу во время замершей картинки, а расписание ходьбы идёт по итерациям и само себя не
  // расклинит. Иначе loc_BA87 «ждём отпускания клавиши» выглядит как зависание.
  if(walkHeld&&TOT-lastIterCy>30*59904){key(walkHeld,false);walkHeld=null;walkUntil=0;}
  if(TOT-lastIterCy>STALL){
    const it0=iter;
    dumpState(`ОСТАНОВКА СЧЁТЧИКА №${stalls.length+1}`);
    stalls.push({iter:it0,pc:cpu.pc,lbl:lblOf(cpu.pc),resumed:iter>it0});
    lastIterCy=TOT;
    if(stalls.length>=(+process.env.KLMAXSTALL||3)){hung=true;break;}
  }
}
const iters=iter-startIter;
console.log(`\nитого: итераций ${iters}, шагов ${steps}, тактов ${TOT}, кадров ${frames}`);
console.log(`обратный ход на di: ${lost} (${(100*lost/Math.max(1,frames)).toFixed(1)} %), halt ${(100*hcyc/TOT).toFixed(1)} %`);
console.log(`смен комнат ${roomChanges}, комнат посещено ${Object.keys(roomSeen).length}: ${Object.keys(roomSeen).join(",")}`);
console.log(`принудительных смен дня/ночи ${dayForced}, взводов «рядом с аркой» ${archForced}; дней days=${rdb(L.days)}`);
console.log(`самый длинный промежуток между итерациями ${maxGap} тактов (${(maxGap/59904).toFixed(1)} кадра) на итерации ${maxGapIter}`);
console.log(`смертей ${deaths}${menuHit?", дошло до меню":""}`);
if(stalls.length)for(const s of stalls)console.log(`  остановка на итерации ${s.iter}: PC ${s.pc.toString(16)} (${s.lbl}), счётчик ${s.resumed?"пошёл дальше":"НЕ ВОЗОБНОВИЛСЯ"}`);
console.log(stalls.length?"ВЕРДИКТ: были остановки счётчика (см. выше)":"ВЕРДИКТ: зависания не было");
process.exit(stalls.length?1:0);
