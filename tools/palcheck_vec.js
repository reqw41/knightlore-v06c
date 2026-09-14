#!/usr/bin/env node
// ГЕЙТ ПАЛИТРЫ ПРИ СМЕНЕ ЭКРАНА: на экране не должно быть НИ ОДНОГО кадра, где точка, которую
// никто не перерисовывал, вдруг поменяла цвет. Иначе пользователь видит, как СТАРАЯ картинка
// перекрашивается в цвет НОВОЙ комнаты (владелец: «неожиданное изменение цветов старого экрана»).
//
//   node tools/palcheck_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld [кадров=1500] [inputs.txt]
//
// Ключи: KLROOM=<n> стартовая комната, KLARCH=<N> принудительный переход между комнатами каждые N
//        итераций (как в hangwatch_vec.js: взводится «рядом с аркой», край подставляется по очереди
//        на 4 стороны), KLGAG=1 детерминизм, KLLIVES=1 бессмертие, KLDEATH=<N> убивать игрока каждые
//        N итераций (повтор комнаты), KLGO=1 довести до game over (жизни в 1 и убить),
//        KLJP=метка@кадров перейти на метку (финал), KLMENU0=1 остановиться на меню,
//        KLTRACE=1 печатать КАЖДЫЙ кадр (покадровый разбор), KLVERB=1 печатать события переходов.
//
// КАК МЕРЯЕТСЯ ЦВЕТ. Индекс цвета точки = p0<<3|p1<<2|p2<<1|p3 (p0 = 0x8000 ... p3 = 0xE000),
// цвет = палитра чипа по этому индексу. Палитра чипа СОБИРАЕТСЯ ИЗ OUT-ов (порт 0x02 — индекс,
// порт 0x0C — значение), а не читается из vec_pal: vec_pal лишь источник, а ISR льёт в чип
// записи 15..8 каждый кадр и 7..0 только по запросу vec_palreq (см. vec_isr).
// Снимок берётся НА ГРАНИЦЕ КАДРА, до прерывания, — это ровно то, что показывалось весь кадр.
//
// ДЕФЕКТ = точка, у которой индекс (все четыре плоскости) НЕ изменился с прошлого кадра, а цвет
// изменился. Такую точку никто не перерисовывал — значит её перекрасила палитра.
// Погасание (палитра сменилась, когда на экране чёрное) дефектом НЕ является: у чёрной точки
// цвет чёрный при любой палитре, и счётчик её не видит.
"use strict";
const fs=require("fs"),vm=require("vm");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,INPUTS]=process.argv.slice(2); const NF=+NS||1500;
const L={};
for(const l of fs.readFileSync(SLD,"latin1").split("\n")){const p=l.split("|");if(p.length>=8&&p[6]==="L"){const n=p[7].split(",")[1];if(n&&!(n in L)){L[n]=+p[5];}}}
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
const JOY_IDLE=0xFF; let JOY=JOY_IDLE;
{ const _rd=io.input.bind(io); io.input=p=>((p&0xFF)===0x0E?JOY:_rd(p)); }
// ---- палитра ЧИПА из OUT-ов: 0x02 = индекс (цвет бордюра), 0x0C = значение ----
const chipPal=new Uint8Array(16); let palIdx=0, palOuts=0;
{ const _out=io.output.bind(io);
  io.output=(p,v)=>{ p&=0xFF;
    if(p===0x02){palIdx=v&0x0F;}
    else if(p===0x0C){chipPal[palIdx]=v&0xFF;palOuts++;}
    return _out(p,v); }; }
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
const VKEYS={z:[5,0x80],x:[6,0x01],a:[6,0x02],q:[4,0x02],"0":[2,0x01],"1":[2,0x02],"2":[2,0x04],"3":[2,0x08],space:[7,0x80],
             vk:[0,0x04],left:[0,0x10],right:[0,0x40],up:[0,0x20],down:[0,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
const inputs={}; if(INPUTS&&fs.existsSync(INPUTS))for(const l of fs.readFileSync(INPUTS,"utf8").split("\n")){
  const m=l.trim().match(/^(\d+)\s+(\S+)\s+(down|up)/i);if(m)(inputs[+m[1]+1]=inputs[+m[1]+1]||[]).push([m[2].toLowerCase(),m[3].toLowerCase()==="down"]);}

const ARCH=+process.env.KLARCH||0, DEATH=+process.env.KLDEATH||0;
let phase="menu",iter=0,steps=0,frames=0,archForced=0,deaths=0;
const RMB=L.graphic_objs_tbl+8;
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(RMB,false)!==0&&!g.roomSet){g.roomSet=true;ow(RMB,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;
    if(process.env.KLLIVES){ow(L.lives,5,false); if(rdb(L.days)>=0x35)ow(L.days,1,false);}
    for(const [k,d] of (inputs[iter]||[]))key(k,d);
    if(ARCH&&iter%ARCH===0){
      ow(L.graphic_objs_tbl+7,rdb(L.graphic_objs_tbl+7)|1,false);
      ow(L.graphic_objs_tbl+12,rdb(L.graphic_objs_tbl+12)&0x0F,false);
      const d=(archForced++)&3;
      if(d===0)ow(L.graphic_objs_tbl+1,0x00,false);
      else if(d===1)ow(L.graphic_objs_tbl+1,0xFF,false);
      else if(d===2)ow(L.graphic_objs_tbl+2,0xFF,false);
      else ow(L.graphic_objs_tbl+2,0x00,false); }
    // KLDEATH: «съели» — обнулить жизни объекта игрока нельзя, поэтому ставим флаг смерти игры
    if(DEATH&&iter%DEATH===0&&L.game_state!==undefined){deaths++;ow(L.game_state,1,false);}
  }
  return ow(a,w,s);};

// ---- снимок кадра: 4 плоскости + палитра чипа ----
const PLANE=[0x8000,0xA000,0xC000,0xE000];
function snapPlanes(){const b=Buffer.alloc(4*0x2000);
  for(let p=0;p<4;p++)for(let i=0;i<0x2000;i++)b[p*0x2000+i]=rdb(PLANE[p]+i);
  return b;}
let prev=null, prevPal=null;
const events=[]; let recolorFrames=0, recolorTotal=0, worst={n:0,frame:-1};
const trace=!!process.env.KLTRACE, verb=!!process.env.KLVERB;
const traceLines=[];
let lastRoom=-1;
function onFrame(){
  const pl=snapPlanes(), pal=Buffer.from(chipPal);
  let recol=0, redrawn=0, lit=0;
  if(prev){
    // изменившиеся записи палитры
    const dif=[]; for(let i=0;i<16;i++)if(pal[i]!==prevPal[i])dif.push(i);
    // байты плоскостей, где что-то изменилось
    for(let i=0;i<0x2000;i++){
      const a0=prev[i],b0=pl[i],a1=prev[0x2000+i],b1=pl[0x2000+i],
            a2=prev[0x4000+i],b2=pl[0x4000+i],a3=prev[0x6000+i],b3=pl[0x6000+i];
      const same=(a0===b0&&a1===b1&&a2===b2&&a3===b3);
      if(!same){ // точка перерисована — считаем сколько битов p0 изменилось (объём копирования)
        redrawn+=popc(a0^b0); }
      if(dif.length===0)continue;
      if(!same){
        // разбор по битам: часть точек байта могла остаться прежней
        for(let bit=0;bit<8;bit++){
          const m=1<<bit;
          const ia=((a0&m)?8:0)|((a1&m)?4:0)|((a2&m)?2:0)|((a3&m)?1:0);
          const ib=((b0&m)?8:0)|((b1&m)?4:0)|((b2&m)?2:0)|((b3&m)?1:0);
          if(ia===ib&&pal[ia]!==prevPal[ia])recol++;
        }
      } else {
        for(let bit=0;bit<8;bit++){
          const m=1<<bit;
          const ia=((a0&m)?8:0)|((a1&m)?4:0)|((a2&m)?2:0)|((a3&m)?1:0);
          if(pal[ia]!==prevPal[ia])recol++;
        }
      }
    }
    if(dif.length&&(recol||verb)){
      events.push({frame:frames,iter,room:rdb(RMB),attr:rdb(ROOM),recol,
        pal:dif.map(i=>`${i.toString(16)}:${prevPal[i].toString(16)}->${pal[i].toString(16)}`).join(",")});
    }
    if(recol){recolorFrames++;recolorTotal+=recol;if(recol>worst.n)worst={n:recol,frame:frames,iter,room:rdb(RMB)};}
  }
  for(let i=0;i<0x2000;i++)lit+=popc(pl[i]);
  if(trace){
    const rm=rdb(RMB);
    traceLines.push(`${String(frames).padStart(5)} итер=${String(iter).padStart(5)} комн=${String(rm).padStart(3)} `+
      `точек_p0=${String(lit).padStart(6)} перерисовано_бит_p0=${String(redrawn).padStart(6)} перекрашено=${String(recol).padStart(6)} `+
      `пал=${[8,9,10,11,12,13,14,15].map(i=>pal[i].toString(16).padStart(2,"0")).join("")}`);
    if(rm!==lastRoom){traceLines.push(`      ^^^ комната ${lastRoom} -> ${rm}`);lastRoom=rm;}
  }
  prev=pl;prevPal=pal;
}
function popc(x){x=x-((x>>1)&0x55);x=(x&0x33)+((x>>2)&0x33);return (x+(x>>4))&0x0F;}

// ---- прогон ----
for(let f=0;f<60;f++){onFrame();frames++;v06c.oneInterrupt(true);}
if(!process.env.KLMENU0){
  key("0",true);
  let guard=0; while(phase!=="game"&&guard++<4000){onFrame();frames++;v06c.oneInterrupt(true);}
  if(phase!=="game")throw new Error("не дошли до комнаты");
}
let frc=0,tacc=0;
while(frames<NF&&steps<4e8){
  if(frc>=59904){frc-=59904;frames++;onFrame();v06c.irq=true;v06c.checkInterrupt();}
  const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
  if(cpu.last_opcode===0xd3)io.commit();}
if(process.env.KLJP){const m=process.env.KLJP.split("@"); const lbl=m[0], nf=+(m[1]||400);
  if(L[lbl]===undefined)throw new Error("KLJP: нет метки "+lbl);
  cpu.pc=L[lbl]; cpu.sp=L.clear_start; let f2=0;
  while(f2<nf&&steps<8e8){if(frc>=59904){frc-=59904;f2++;frames++;onFrame();v06c.irq=true;v06c.checkInterrupt();}
    const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
    if(cpu.last_opcode===0xd3)io.commit();}}

if(trace)fs.writeFileSync(process.env.KLTRACEOUT||"build/palcheck_trace.txt",traceLines.join("\n")+"\n");
console.log(`palcheck: кадров ${frames}, итераций ${iter}, принудительных арок ${archForced}, OUT палитры ${palOuts}`);
console.log(`события смены палитры: ${events.length}`);
for(const e of events.slice(0,60))
  console.log(`  кадр ${String(e.frame).padStart(5)} итер ${String(e.iter).padStart(5)} комната ${String(e.room).padStart(3)} attr ${e.attr}: ${e.pal}  ПЕРЕКРАШЕНО ТОЧЕК: ${e.recol}`);
if(events.length>60)console.log(`  ... ещё ${events.length-60}`);
console.log(`кадров с перекраской: ${recolorFrames}, точек всего: ${recolorTotal}, худший кадр ${worst.frame} (${worst.n} точек, комната ${worst.room})`);
const LIMIT=+process.env.KLPALLIMIT||0;
if(recolorTotal>LIMIT){console.log(`ГЕЙТ КРАСНЫЙ: старая картинка перекрашивается (допуск ${LIMIT})`);process.exit(1);}
console.log("ГЕЙТ ЗЕЛЁНЫЙ: ни одной точки, перекрашенной без перерисовки");
