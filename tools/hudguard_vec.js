#!/usr/bin/env node
// ГЕЙТ «панель не затирают»: ни один блит/заливка объектов не пишет в строки HUD (32..63) плоскости 0
// и в маску плоскости 2. Панель живёт ТОЛЬКО на экране: после update_screen строки 32..63 буфера
// нулевые, поэтому любое окно объекта, попавшее в эти строки, копирует на панель нули (клип
// blit_to_screen режет окна, ПЕРЕСЕКАЮЩИЕ границу; окно ЦЕЛИКОМ внутри HUD не режется — это и
// проверяем).
//   node tools/hudguard_vec.js <rom> <sld> [итераций=40] [комнаты: a-b|список|all]
// Ключи: KLDIRS=l,r,u,d,n  какие направления держать в каждой комнате (n = покой), по умолчанию все;
//        KLFORCE=1         дополнительно ПРИНУДИТЕЛЬНО ставить героя в крайние X/Y/Z комнаты
//                          (сетка углов из KLGRID2, по умолчанию замеренные пределы ходьбы);
//        KLCSV=файл        таблица по комнатам.
// Что меряется на каждый вызов blit_to_screen: HL = адрес окна в буфере (L = строка), B = строк,
// C = столбцов, адрес возврата = ХОЗЯИН окна. Плюс поимённый учёт ВСЕХ записей в строки 32..63
// плоскостей 0 и 2 (владелец = последняя вошедшая процедура-писатель экрана).
// Код возврата: 1, если есть окно-нарушитель или запись в HUD не от процедуры панели.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||40;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
// имя по адресу: ближайшая предыдущая метка (без внутренних .локальных)
const fn=Object.entries(L).filter(([n,a])=>a>=0x80&&!/^\./.test(n)).sort((x,y)=>x[1]-y[1]);
const fa=fn.map(x=>x[1]), fnm=fn.map(x=>x[0]);
function nameOf(pc){let lo=0,hi=fa.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(fa[m]<=pc){r=m;lo=m+1;}else hi=m-1;}
  return r<0?("?"+pc.toString(16)):(fnm[r]+(pc-fa[r]?"+"+(pc-fa[r]):""));}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd, OBJ=L.graphic_objs_tbl;
const BLIT=L.blit_to_screen, USCR=L.update_screen, HMASK=L.vec_hud_mask, HOFF=L.vec_hud_off;
for(const [n,v] of [["blit_to_screen",BLIT],["update_screen",USCR],["vec_hud_mask",HMASK]])
  if(v===undefined)throw new Error("нет метки "+n);
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
{ const _rd=io.input.bind(io); io.input=p=>((p&0xFF)===0x0E?0xFF:_rd(p)); }   // джойстик в покое
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
const ROMBYTES=new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000);
const ow=memory.write.bind(memory);
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
// клавиши матрицы Вектора (как в hudshot_vec.js): раскладка 3 = стрелки по сторонам света
const VKEYS={"3":[2,0x08],"0":[2,0x01],left:[0,0x10],right:[0,0x40],up:[0,0x20],down:[0,0x80],a:[6,0x02],space:[7,0x80]};
function key(k,down){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;}

const HUD0=32, HUD1=63;                        // строки памяти, где живёт панель
// ПРОЦЕДУРЫ ПАНЕЛИ — единственные, кому позволено писать в строки 32..63 плоскостей 0 и 2.
// display_frame+  рамка и знак солнца/луны;  loc_BF91+  блит ячейки предмета (display_object);
// blit_2x8+       счётчик дней и цифры жизней;  update_screen  копия буфера на экран в блоке
// перерисовки панели и при смене комнаты;  vec_hud_mask  маска плоскости 2;  kl_unbar  выход из меню.
const PANEL=/^(display_frame\+|loc_BF91\+|blit_2x8\+|update_screen( |$)|vec_hud_mask$|kl_unbar\+|вход в комнату)/;
const rooms = !ROOMS||ROOMS==="all" ? [...Array(256).keys()]
  : ROOMS.includes("-") ? (([a,b])=>Array.from({length:b-a+1},(_,i)=>a+i))(ROOMS.split("-").map(Number))
  : ROOMS.split(",").map(Number);
const DIRS=(process.env.KLDIRS||"n,left,right,up,down").split(",");
const FORCE=!!process.env.KLFORCE;
// Сетка принудительных положений героя: крайние X/Y комнаты (стены Knight Lore стоят по 0x20/0xE0)
// и два Z — пол и прыжок. Порядок обхода — по одной позиции на итерацию.
const GRID=[];
for(const y of [0x20,0x28,0xE0,0xD8]) for(const x of [0x20,0x28,0xE0,0xD8]) for(const z of [0x20,0x30,0x50]) GRID.push([x,y,z]);

// --- сводные счётчики ---
const S={blits:0, hudBlits:0, clipped:0, insideHud:0, writesHud:0, badBlits:new Map(), owners:new Map(),
         writeOwners:new Map(), minRow:255, minRowOwner:"", roomsOk:0, roomsBad:0, plane2:0,
         objMinL:255, objMinTop:255, objMinTopWin:""};
const bump=(m,k,n)=>m.set(k,(m.get(k)||0)+n);

function runRoom(room){
  // ЛОВУШКА СТЕНДА: init_from_array идёт через memory.write, и хук ПРЕДЫДУЩЕЙ комнаты (его phase
  // остался "game") записывал заливку образа как 1024 записи в строки HUD плоскости 0 и столько же
  // в плоскость 2. Снимаем хук ДО заливки.
  memory.write=ow;
  memory.init_from_array(ROMBYTES,0);
  if(L.vec_snd_force!==undefined)ow(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
  cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;for(let r=0;r<8;r++)keyboard.matrix[r]=0;
  let phase="menu",iter=0,roomSet=false,steps=0,owner="?",pos=0,curPC=0,scripted=false;
  const r={room, blits:0, hud:0, clip:0, inside:0, wr:0, minRow:255, bad:0, objTop:255};
  memory.write=function(a,w,s){a&=0xffff;
    if(phase==="menu"&&cpu.pc===SYNC){ow(L.seed_1,0x34,false);ow(L.seed_1+1,0x12,false);ow(L.seed_2,0x78,false);ow(L.seed_2+1,0x56,false);ow(L.seed_3,0x9A,false);phase="init";}
    if(phase==="init"&&!roomSet&&a>=0xA000&&a<0xC000){roomSet=true;ow(OBJ+8,room,false);ow(OBJ+40,room,false);}
    if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
    if(phase==="game"){
      const row=a&0xFF;
      if(row>=HUD0&&row<=HUD1&&((a>=0x8000&&a<0xA000)||(a>=0xC000&&a<0xE000))){
        r.wr++; S.writesHud++; bump(S.writeOwners, (scripted?(/^(bts_|us_|vhm_|vfw_)/.test(nameOf(curPC))?owner:nameOf(curPC))
                             :"вход в комнату (первый показ, до сценария)")+(a>=0xC000?" [p2]":""), 1);
        if(a>=0xC000)S.plane2++;
      }
      if(a===SEED2HI)iter++;
    }
    return ow(a,w,s);};
  for(let f=0;f<60;f++)v06c.oneInterrupt(true);
  // раскладка 3 (стрелки = стороны света), затем старт
  key("3",true); for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("3",false);
  for(let f=0;f<20;f++)v06c.oneInterrupt(true);
  key("0",true);
  let guard=0; while(phase!=="game"&&guard++<4000)v06c.oneInterrupt(true);
  if(phase!=="game")return null;                       // номер комнаты невалиден
  scripted=true;
  let frc=0,tacc=0,start=iter,dir=0,held=null;
  const stepLimit=3e8;
  const step=()=>{
    if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
    // ---- инструментовка: вход в процедуры-писатели экрана ----
    const pc=cpu.pc;
    if(pc===BLIT){
      // ХОЗЯИН окна — МЕСТО ВЫЗОВА (PC предыдущей инструкции), а не адрес возврата: display_frame
      // и blit_2x8 уходят в блит через `jp`, и на стеке лежит уже их собственный вызыватель.
      owner=nameOf(curPC);
      const l=cpu.l(), b=cpu.b(), c=cpu.c(), h=cpu.h();
      S.blits++; r.blits++;
      if(b>0&&c>0&&h>=0xA0&&h<0xC0){                   // окно в буфере (иначе vec_clip_cols его выбросит)
        const top=l+b-1;
        if(l<r.minRow){r.minRow=l;}
        if(!PANEL.test(owner)){            // ЗАПАС ОБЪЕКТОВ: насколько низко доходит окно НЕ панели
          if(l<S.objMinL)S.objMinL=l;
          if(top<S.objMinTop){S.objMinTop=top;S.objMinTopWin=`${owner} ${h.toString(16)}:${l}..${top} x${c}`;}
          if(top<r.objTop)r.objTop=top;
        }
        if(l<=HUD1){                                    // окно достаёт до строк панели
          S.hudBlits++; r.hud++; bump(S.owners, (top<=HUD1?"целиком в HUD: ":"пересекает границу: ")+owner+` ${h.toString(16)}:${l}..${top} x${c}`, 1);
          if(top<=HUD1){ S.insideHud++; r.inside++;      // ЦЕЛИКОМ внутри HUD — клип его не тронет
            if(!PANEL.test(owner)){
              r.bad++; bump(S.badBlits, "окно целиком в HUD не от панели: "+owner+` ${h.toString(16)}:${l}..${top} x${c}`, 1);}}
          else { S.clipped++; r.clip++; }
        }
      }
    } else if(pc===USCR){ owner="update_screen <- "+nameOf(curPC); }
    else if(pc===HMASK||pc===HOFF){ owner="vec_hud_mask"; }
    curPC=pc;
    const cy=(cpu.instruction(),cpu.vcycles);steps++;frc+=cy;tacc+=cy;
    if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xd3)io.commit();
  };
  for(const d of DIRS){
    if(held){key(held,false);held=null;}
    if(d!=="n"){key(d,true);held=d;}
    const to=iter+N;
    while(iter<to&&steps<stepLimit){
      if(FORCE){const [x,y,z]=GRID[pos++%GRID.length];
        ow(OBJ+1,x,false);ow(OBJ+2,y,false);ow(OBJ+3,z,false);
        ow(OBJ+33,x,false);ow(OBJ+34,y,false);ow(OBJ+35,(z+12)&0xff,false);}
      const it0=iter; while(iter===it0&&steps<stepLimit)step();
    }
    dir++;
  }
  if(held)key(held,false);
  if(r.minRow<S.minRow){S.minRow=r.minRow;}
  if(r.bad)S.roomsBad++;else S.roomsOk++;
  return r;
}

const csv=["room,blits,hud_blits,clipped,inside_hud,hud_writes,min_row,obj_min_top,bad"];
let valid=0;
for(const room of rooms){
  const r=runRoom(room);
  if(!r){csv.push(`${room},,,,,,,невалидная`);continue;}
  valid++;
  csv.push(`${r.room},${r.blits},${r.hud},${r.clip},${r.inside},${r.wr},${r.minRow},${r.objTop},${r.bad}`);
  if(r.bad)console.log(`комната ${room}: НАРУШЕНИЙ ${r.bad}`);
}
fs.writeFileSync(process.env.KLCSV||"build/hudguard.csv",csv.join("\n")+"\n");
console.log(`комнат валидных ${valid} из ${rooms.length}, направлений ${DIRS.join("/")} по ${N} итераций${FORCE?", + принудительные крайние X/Y/Z":""}`);
console.log(`блитов всего ${S.blits}; окон, достающих до строк HUD (<=63): ${S.hudBlits}; из них ОТКЛИПАНО (пересекали границу) ${S.clipped}, целиком внутри HUD ${S.insideHud}`);
console.log(`окна ОБЪЕКТОВ (не панели): самая нижняя строка начала ${S.objMinL}, самая нижняя ВЕРХНЯЯ строка ${S.objMinTop} (${S.objMinTopWin})`);
console.log(`   порог: окно объекта, у которого и начало, и верх ниже 64, клип НЕ режет — значит запас = ${S.objMinTop-63} строк`);
console.log(`записей в строки 32..63 плоскостей 0/2: ${S.writesHud} (в плоскость 2: ${S.plane2}); самая нижняя строка окна за прогон: ${S.minRow}`);
console.log("окна, достающие до строк HUD (место вызова blit_to_screen, окно = страница:строки xстолбцов):");
for(const [k,v] of [...S.owners].sort((a,b)=>b[1]-a[1]))console.log(`   ${String(v).padStart(9)}  ${k}`);
console.log("владельцы записей в строки 32..63 плоскостей 0 и 2:");
for(const [k,v] of [...S.writeOwners].sort((a,b)=>b[1]-a[1]))console.log(`   ${String(v).padStart(9)}  ${k}`);
if(S.badBlits.size){console.log("НАРУШИТЕЛИ (окно целиком в строках HUD, хозяин — не процедура панели):");
  for(const [k,v] of [...S.badBlits].sort((a,b)=>b[1]-a[1]).slice(0,40))console.log(`   ${String(v).padStart(7)}  ${k}`);}
for(const [k,v] of S.writeOwners) if(!PANEL.test(k.replace(/ \[p2\]$/,""))) bump(S.badBlits,"записи в строки HUD не от панели: "+k,v);
const bad=S.badBlits.size;
console.log(bad?`ГЕЙТ КРАСНЫЙ: нарушителей ${bad}`:"ГЕЙТ ЗЕЛЁНЫЙ: панель не затирает никто, кроме процедур панели");
process.exit(bad?1:0);
