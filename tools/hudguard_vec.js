#!/usr/bin/env node
// ГЕЙТ «панель не затирают»: ни один блит/заливка объектов не пишет в ПАНЕЛЬ плоскости 0 и в маску
// плоскости 2. Панель живёт ТОЛЬКО на экране: после update_screen её строки в буфере нулевые,
// поэтому любое окно объекта, попавшее в них, копирует на панель нули, и дыра держится до следующей
// смены комнаты.
// ЗОНА ПАНЕЛИ СНИМАЕТСЯ ЗАМЕРОМ, а не задана константой (§30): кроме полосы строк 32..63 панель
// занимает боковые колонны и ЛЕСТНИЦУ, обходящую нижние углы ромба комнаты (столбцы 0..8 и 23..31,
// до строки 95). Зона = адреса, куда процедуры панели (display_panel, print_lives*, display_day,
// print_days, display_objects, display_sun_moon_frame) кладут НЕНУЛЕВЫЕ байты в буфер при
// перерисовке панели; снимается один раз на комнату и замораживается на первом update_screen.
//   node tools/hudguard_vec.js <rom> <sld> [итераций=40] [комнаты: a-b|список|all]
// Ключи: KLDIRS=l,r,u,d,n  какие направления держать в каждой комнате (n = покой), по умолчанию все;
//        KLFORCE=1         дополнительно ПРИНУДИТЕЛЬНО ставить героя в крайние X/Y/Z комнаты
//                          (сетка углов из KLGRID2, по умолчанию замеренные пределы ходьбы);
//        комната «start»   НЕ подменять номер комнаты: только так герой РЕАЛЬНО ходит и выходит
//                          через арки (подменённая комната ставит его в середину чужой комнаты,
//                          где он упирается в препятствие и перебирает ногами на месте);
//        KLCSV=файл        таблица по комнатам.
// Что меряется на каждый вызов blit_to_screen: HL = адрес окна в буфере (L = строка), B = строк,
// C = столбцов, адрес возврата = ХОЗЯИН окна. Плюс поимённый учёт ВСЕХ записей В ЗОНУ ПАНЕЛИ
// плоскостей 0 и 2 (владелец = последняя вошедшая процедура-писатель экрана).
// Код возврата: 1, если есть окно-нарушитель или запись в HUD не от процедуры панели.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,ROOMS]=process.argv.slice(2); const N=+NS||40;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");
  if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}
  if(p.length>=8&&p[6]==="F"){const n=p[7].trim();if(n&&!(n in L))L[n]=+p[5];}}   // .локальные метки (loc_D653.copy_loop)
// имя по адресу: ближайшая предыдущая метка (без внутренних .локальных)
const fn=Object.entries(L).filter(([n,a])=>a>=0x80&&!/^\./.test(n)).sort((x,y)=>x[1]-y[1]);
const fa=fn.map(x=>x[1]), fnm=fn.map(x=>x[0]);
function nameOf(pc){let lo=0,hi=fa.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(fa[m]<=pc){r=m;lo=m+1;}else hi=m-1;}
  return r<0?("?"+pc.toString(16)):(fnm[r]+(pc-fa[r]?"+"+(pc-fa[r]):""));}
const SEED2HI=L.seed_2+1, ROOM=L.curr_room_attrib, VKBD=L.vkbd, OBJ=L.graphic_objs_tbl;
const BLIT=L.blit_to_screen, USCR=L.update_screen, HMASK=L.vec_hud_mask, HOFF=L.vec_hud_off;
// .copy_loop — ЕДИНСТВЕННЫЙ путь объектов в блит. Окно берётся со стека (ещё ДО клипа), поэтому
// видно и то, что клип отрезал: на входе SP -> [ширина/высота][источник][приёмник].
const CLOOP=L["loc_D653.copy_loop"];
// ЗАМЕР ЗОНЫ идёт от входа в display_panel до update_screen — это ровно хвост блока перерисовки
// панели (display_panel -> знак солнца -> DAY -> счётчик дней -> фигурка и цифры жизней -> выгрузка
// на экран), то есть всё, что панель рисует ВЫШЕ строки 63 (строки 32..63 в зоне всегда).
// СТОРОЖИТЬ НАДО ИМЕННО display_panel, и только его: у него ровно один вызыватель, и он живёт
// только в этом блоке. Ловушки, каждая из которых красила гейт на ИСПРАВНОЙ сборке (§30.5):
//   display_objects — в него ПРОВАЛИВАЕТСЯ display_objects_carried из объектного рендера;
//   print_lives — второй вызыватель сидит в player_dies, а с KLFORCE герой гибнет постоянно.
// В обоих случаях «фаза панели» включалась посреди отрисовки объектов, и в зону уезжали их пиксели
// по всему полю (замер: комната 10, строки 124..137 столбца 14).
const PDRAW=[L.display_panel].filter(x=>x!==undefined);
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
// clr_byte и update_screen <- loc_BA79 — экраны БЕЗ панели (game over, финал): там строки 32..63
// чистит и перерисовывает сама игра, а маску снимает vec_hud_off из print_border (§17.5.1).
const PANEL=/^(display_frame\+|loc_BF91\+|blit_2x8\+|update_screen( |$)|vec_hud_mask$|kl_unbar\+|clr_byte|вход в комнату)/;
// «start» — НЕ подменять номер комнаты. Это единственный сценарий, в котором герой РЕАЛЬНО ходит:
// при подмене номера игра ставит его в середину чужой комнаты, где он чаще всего упирается в
// препятствие и только перебирает ногами на месте (замер §30 — из-за этого прежний прогон по 128
// комнатам ни разу не довёл окно объекта до кромки комнаты).
const rooms = !ROOMS||ROOMS==="all" ? [...Array(256).keys()]
  : ROOMS.includes("-") ? (([a,b])=>Array.from({length:b-a+1},(_,i)=>a+i))(ROOMS.split("-").map(Number))
  : ROOMS.split(",").map(x=>x==="start"?-1:Number(x));
const DIRS=(process.env.KLDIRS||"n,left,right,up,down").split(",");
const FORCE=!!process.env.KLFORCE;
// KLFORCEZ=список — принудительно занижать ТОЛЬКО Z героя (падение в яму/провал пола), X и Y
// оставлены игре: так проверяется случай, до которого ходьба по комнате может не дойти.
const FZ=(process.env.KLFORCEZ||"").split(",").filter(Boolean).map(Number);
// Сетка принудительных положений героя: крайние X/Y комнаты (стены Knight Lore стоят по 0x20/0xE0)
// и два Z — пол и прыжок. Порядок обхода — по одной позиции на итерацию.
// KLGRID2=x-список/y-список/z-список (шестнадцатеричные, через запятую) — сузить сетку до
// ДОСТИЖИМЫХ ходьбой положений: 0x20 и 0xE0 — это сами стены, туда герой сам не заходит.
const GRID=[]; {const d=(process.env.KLGRID2||"20,28,E0,D8/20,28,E0,D8/20,30,50").split("/")
  .map(t=>t.split(",").map(v=>parseInt(v,16)));
  for(const y of d[1]) for(const x of d[0]) for(const z of d[2]) GRID.push([x,y,z]);}

// --- сводные счётчики ---
// ---- ВТОРОЕ ПЕРО HUD (§32): цвет внутри зон маски выбирает p1 (буфер рендера) ----
// Зоны читаются ИЗ ОБРАЗА: vec_hudcols — вся маска HUD, vec_hudbuf — ЖЁЛТЫЕ зоны (предметы и знак
// солнца/луны). Инвариант, который здесь сторожится НА КАЖДОЙ ГРАНИЦЕ ИТЕРАЦИИ:
//   в ЖЁЛТОЙ зоне  p0=1 -> p1=1  (иначе элемент выйдет белым: буфер под ним потеряли);
//   в БЕЛОЙ  зоне  p0=1 -> p1=0  (иначе цифра выйдет жёлтой: под ней осталась копия в буфере).
// Байт целиком лежит внутри зоны или вне её (зоны нарезаны по столбцам), поэтому проверка побайтная.
function readZones(base){const z=[];if(L[base]===undefined)return z;
  for(let a=L[base];rd(a);a+=5)z.push({pg:rd(a),nc:rd(a+1),r0:rd(a+2),nr:rd(a+3)});return z;}
function zoneBytes(z,base){const s=new Set();
  for(const e of z)for(let c=e.pg-base;c<e.pg-base+e.nc;c++)for(let r=e.r0;r<e.r0+e.nr;r++)s.add((c<<8)|r);
  return s;}
const S={blits:0, hudBlits:0, clipped:0, insideHud:0, writesHud:0, badBlits:new Map(), owners:new Map(),
         xyz:{x0:255,x1:0,y0:255,y1:0,z0:255,z1:0}, penBad:0, penFrames:0, penWorst:new Map(), penIter:new Map(), penKill:new Map(),
         writeOwners:new Map(), minRow:255, minRowOwner:"", roomsOk:0, roomsBad:0, plane2:0,
         objMinL:255, objMinTop:255, objMinTopWin:"", objHud:0, objClip:0, objSkip:0, exits:0, footAbove:0, exitBy:new Map()};
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
  // след панели: 1 = сюда панель кладёт своё (кроме полосы 32..63, она в зоне всегда)
  const foot=new Uint8Array(32*256); let panelPhase=false, footFrozen=false;
  const r={room, blits:0, hud:0, clip:0, inside:0, wr:0, minRow:255, bad:0, objTop:255, exits:0};
  memory.write=function(a,w,s){a&=0xffff;
    if(phase==="menu"&&cpu.pc===SYNC){ow(L.seed_1,0x34,false);ow(L.seed_1+1,0x12,false);ow(L.seed_2,0x78,false);ow(L.seed_2+1,0x56,false);ow(L.seed_3,0x9A,false);phase="init";}
    if(phase==="init"&&!roomSet&&a>=0xA000&&a<0xC000){roomSet=true;
      if(room>=0){ow(OBJ+8,room,false);ow(OBJ+40,room,false);}}   // room < 0 — стартовая комната как есть
    if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
    // След панели в буфере: считаем только БИТЫ, КОТОРЫЕ ПАНЕЛЬ ДОБАВИЛА (w & ~старое). print_sprite
    // кладёт спрайт по ИЛИ, и если под панелью в буфере уже лежал объект, запись «ненулевая» даже
    // там, где у самой панели точек нет: прогон с принудительной сеткой ловил на этом 3 ложных
    // нарушителя (зона раздувалась пикселями объектов, и законный блит объекта попадал в неё).
    if(panelPhase&&a>=0xA000&&a<0xC000&&(w&~rd(a)&0xFF))foot[(((a>>8)&0x1F)<<8)|(a&0xFF)]=1;
    // КТО ГАСИТ ВТОРОЕ ПЕРО: запись в буфер, которая снимает p1 там, где в плоскости 0 точка есть,
    // внутри ЖЁЛТОЙ зоны (элемент побелеет). Имя — последняя вошедшая процедура-писатель экрана.
    if(g.ZY&&phase==="game"&&a>=0xA000&&a<0xC000){const k=(((a>>8)&0x1F)<<8)|(a&0xFF);
      if(g.ZY.has(k)){const lost=rd(a)&~w&rd(0x8000+((a&0x1F00))+(a&0xFF))&0xFF;
        if(lost)bump(S.penKill, nameOf(curPC), 1);}}
    if(phase==="game"){
      const row=a&0xFF, col=(a>>8)&0x1F;
      if(((row>=HUD0&&row<=HUD1)||foot[(col<<8)|row])&&((a>=0x8000&&a<0xA000)||(a>=0xC000&&a<0xE000))){
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
  // зоны второго пера — из образа (см. выше); маска минус жёлтые = белые зоны
  const ZY=zoneBytes(readZones("vec_hudbuf"),0xA0), ZM=zoneBytes(readZones("vec_hudcols"),0xC0); g.ZY=ZY;
  // KLNOPEN=1 — НЕ проверять второе перо. Нужно ровно для прогона с ПОЛНОЙ сеткой KLFORCE:
  // она ставит героя на 0x30..0x60 НИЖЕ пола (естественная высота Z = 0x80), и стирание объекта
  // законно трёт буфер под ячейками предметов — инвариант второго пера там неприменим (§32).
  const penCheck=ZY.size>0&&ZM.size>0&&!process.env.KLNOPEN;
  function checkPen(){ S.penFrames++;
    for(const k of ZM){ const col=k>>8, row=k&0xFF;
      const b0=rd(0x8000+(col<<8)+row), b1=rd(0xA000+(col<<8)+row);
      const bad = ZY.has(k) ? (b0&~b1&0xFF) : (b0&b1&0xFF);
      if(bad){ S.penBad++; bump(S.penWorst, `${ZY.has(k)?"жёлтая":"белая"} зона, столбец ${col} строка ${row}`, 1);
               bump(S.penIter, `итерация ${iter}`, 1); } } }
  let frc=0,tacc=0,start=iter,dir=0,held=null,curRoom=rd(OBJ+8);
  const stepLimit=3e8;
  const step=()=>{
    if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
    // ---- инструментовка: вход в процедуры-писатели экрана ----
    const pc=cpu.pc;
    if(!footFrozen&&PDRAW.includes(pc))panelPhase=true;
    if(pc===USCR){panelPhase=false;if(scripted)footFrozen=true;}   // первый показ комнаты — конец замера следа
    if(pc===BLIT){
      // ХОЗЯИН окна — МЕСТО ВЫЗОВА (PC предыдущей инструкции), а не адрес возврата: display_frame
      // и blit_2x8 уходят в блит через `jp`, и на стеке лежит уже их собственный вызыватель.
      owner=nameOf(curPC);
      const l=cpu.l(), b=cpu.b(), c=cpu.c(), h=cpu.h();
      S.blits++; r.blits++;
      if(b>0&&c>0&&h>=0xA0&&h<0xC0){                   // окно в буфере (иначе vec_clip_cols его выбросит)
        const top=l+b-1;
        if(l<r.minRow){r.minRow=l;}
        if(l<=HUD1){                                    // окно достаёт до строк панели
          S.hudBlits++; r.hud++; bump(S.owners, (top<=HUD1?"целиком в HUD: ":"пересекает границу: ")+owner+` ${h.toString(16)}:${l}..${top} x${c}`, 1);
          if(top<=HUD1){ S.insideHud++; r.inside++;      // ЦЕЛИКОМ внутри HUD — клип его не тронет
            if(!PANEL.test(owner)){
              r.bad++; bump(S.badBlits, "окно целиком в HUD не от панели: "+owner+` ${h.toString(16)}:${l}..${top} x${c}`, 1);}}
          else { S.clipped++; r.clip++; }
        }
      }
    } else if(pc===CLOOP){                     // окно объекта ДО клипа
      const sp=cpu.sp&0xffff, b=rd(sp+1), l=rd(sp+2);
      if(b>0){ const top=l+b-1;
        if(l<S.objMinL)S.objMinL=l;
        if(top<S.objMinTop){S.objMinTop=top;S.objMinTopWin=`${rd(sp+3).toString(16)}:${l}..${top} x${rd(sp)}`;}
        if(top<r.objTop)r.objTop=top;
        if(l<=HUD1){ S.objHud++; if(top<=HUD1)S.objSkip++; else S.objClip++; } }
    } else if(pc===USCR){ owner="update_screen <- "+nameOf(curPC); }
    else if(pc===HMASK||pc===HOFF){ owner="vec_hud_mask"; }
    curPC=pc;
    const cy=(cpu.instruction(),cpu.vcycles);steps++;frc+=cy;tacc+=cy;
    if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xd3)io.commit();
  };
  // ЗАМЕР ЗОНЫ ПАНЕЛИ. Первый показ комнаты идёт ДО сценария (через oneInterrupt, там инструментовки
  // нет), поэтому панель заставляем перерисоваться ещё раз: render_status_info != 0 — это и есть
  // признак «нарисовать панель и выгрузить буфер на экран». Ненулевые записи её процедур в буфер
  // за эти две итерации и дают зону; дальше зона заморожена.
  if(L.render_status_info!==undefined){
    ow(L.render_status_info, 1, false);
    for(let k=0;k<2;k++){const it0=iter; while(iter===it0&&steps<stepLimit)step();}
  }
  footFrozen=true;
  { let n=0; for(let v=0;v<32*256;v++)if(foot[v]&&(v&0xFF)>HUD1)n++; r.footAbove=n; S.footAbove=n; }

  for(const d of DIRS){
    if(held){key(held,false);held=null;}
    if(d!=="n"){key(d,true);held=d;}
    const to=iter+N;
    while(iter<to&&steps<stepLimit){
      if(FZ.length){const z=FZ[pos++%FZ.length];ow(OBJ+3,z,false);ow(OBJ+35,(z+12)&0xff,false);}
      if(FORCE){const [x,y,z]=GRID[pos++%GRID.length];
        ow(OBJ+1,x,false);ow(OBJ+2,y,false);ow(OBJ+3,z,false);
        ow(OBJ+33,x,false);ow(OBJ+34,y,false);ow(OBJ+35,(z+12)&0xff,false);}
      // KLITEMS=1 — три предмета в инвентаре с первой же итерации: без них ЖЁЛТАЯ зона предметов
      // пуста (p0=0), и проверка второго пера по ней ничего не проверяет.
      if(process.env.KLITEMS&&L.objects_carried!==undefined&&rd(L.objects_carried)!==96){
        ow(L.objects_carried,96,false);ow(L.objects_carried+4,97,false);ow(L.objects_carried+8,98,false);
        ow(L.objects_carried_changed,1,false);}
      const it0=iter; while(iter===it0&&steps<stepLimit)step();
      { const x=rd(OBJ+1),y=rd(OBJ+2),z=rd(OBJ+3);   // ДИАПАЗОН ХОДЬБЫ героя (для выбора сетки KLFORCE)
        S.xyz.x0=Math.min(S.xyz.x0,x);S.xyz.x1=Math.max(S.xyz.x1,x);
        S.xyz.y0=Math.min(S.xyz.y0,y);S.xyz.y1=Math.max(S.xyz.y1,y);
        S.xyz.z0=Math.min(S.xyz.z0,z);S.xyz.z1=Math.max(S.xyz.z1,z); }
      if(penCheck)checkPen();
      if(process.env.KLLIVES)ow(L.lives,5,false);
      const rr=rd(OBJ+8); if(rr!==curRoom){bump(S.exitBy,`${d}: ${curRoom}->${rr}`,1);curRoom=rr;r.exits++;S.exits++;}   // РЕАЛЬНЫЙ выход через арку
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
  if(!r){csv.push(`${room<0?"start":room},,,,,,,невалидная`);continue;}
  valid++;
  csv.push(`${r.room<0?"start":r.room},${r.blits},${r.hud},${r.clip},${r.inside},${r.wr},${r.minRow},${r.objTop},${r.bad}`);
  if(r.bad)console.log(`комната ${room}: НАРУШЕНИЙ ${r.bad}`);
}
fs.writeFileSync(process.env.KLCSV||"build/hudguard.csv",csv.join("\n")+"\n");
console.log(`комнат валидных ${valid} из ${rooms.length}, направлений ${DIRS.join("/")} по ${N} итераций, РЕАЛЬНЫХ выходов через арки ${S.exits}${FORCE?", + принудительные крайние X/Y/Z":""}`);
console.log(`зона панели выше строки 63 (замер): ${S.footAbove} байт`);
if(S.exitBy.size)console.log("выходы через арки (направление: комната -> комната): "+[...S.exitBy].map(([k,v])=>k+(v>1?" x"+v:"")).join(", "));
console.log(`блитов всего ${S.blits}; окон, достающих до строк HUD (<=63): ${S.hudBlits}; из них ОТКЛИПАНО (пересекали границу) ${S.clipped}, целиком внутри HUD ${S.insideHud}`);
console.log(`окна ОБЪЕКТОВ на входе .copy_loop (ДО клипа): достают до строк панели ${S.objHud}, из них ОБРЕЗАНО по строке 64: ${S.objClip}, ПРОПУЩЕНО целиком (окно внутри панели): ${S.objSkip}`);
console.log(`   самое нижнее окно объекта ДО клипа: начало ${S.objMinL}, верх ${S.objMinTop} (${S.objMinTopWin}) -> запас до полосы панели ${S.objMinTop-63} строк`);
console.log(`записей в строки 32..63 плоскостей 0/2: ${S.writesHud} (в плоскость 2: ${S.plane2}); самая нижняя строка окна за прогон: ${S.minRow}`);
console.log("окна, достающие до строк HUD (место вызова blit_to_screen, окно = страница:строки xстолбцов):");
for(const [k,v] of [...S.owners].sort((a,b)=>b[1]-a[1]))console.log(`   ${String(v).padStart(9)}  ${k}`);
console.log("владельцы записей в строки 32..63 плоскостей 0 и 2:");
for(const [k,v] of [...S.writeOwners].sort((a,b)=>b[1]-a[1]))console.log(`   ${String(v).padStart(9)}  ${k}`);
for(const [k,v] of S.writeOwners) if(!PANEL.test(k.replace(/ \[p2\]$/,""))) bump(S.badBlits,"записи в строки HUD не от панели: "+k,v);
console.log(`герой за прогон: X ${S.xyz.x0.toString(16)}..${S.xyz.x1.toString(16)}, Y ${S.xyz.y0.toString(16)}..${S.xyz.y1.toString(16)}, Z ${S.xyz.z0.toString(16)}..${S.xyz.z1.toString(16)} (шестнадцатеричные)`);
console.log(process.env.KLNOPEN?"второе перо (p1 под зонами маски): НЕ ПРОВЕРЯЛОСЬ (KLNOPEN=1)"
  :`второе перо (p1 под зонами маски): проверено границ итераций ${S.penFrames}, байт-нарушителей ${S.penBad}`);
if(S.penKill.size)console.log("   кто гасит p1 под жёлтой зоной: "+[...S.penKill].sort((a,b)=>b[1]-a[1]).slice(0,8).map(([k,v])=>k+" x"+v).join(", "));
if(S.penBad){console.log("   по итерациям: "+[...S.penIter].map(([k,v])=>k+" x"+v).join(", ").slice(0,600));
  for(const [k,v] of [...S.penWorst].sort((a,b)=>b[1]-a[1]).slice(0,10))console.log(`   ${String(v).padStart(7)}  ${k}`);
  bump(S.badBlits,`p1 под зонами маски разошёлся с ожиданием: ${S.penBad} байт`,S.penBad);}
if(S.badBlits.size){console.log("НАРУШИТЕЛИ:");
  for(const [k,v] of [...S.badBlits].sort((a,b)=>b[1]-a[1]).slice(0,40))console.log(`   ${String(v).padStart(7)}  ${k}`);}
const bad=S.badBlits.size;
console.log(bad?`ГЕЙТ КРАСНЫЙ: нарушителей ${bad}`:"ГЕЙТ ЗЕЛЁНЫЙ: панель не затирает никто, кроме процедур панели");
process.exit(bad?1:0);
