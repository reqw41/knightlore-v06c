#!/usr/bin/env node
// Снимок экрана Вектора (4 плоскости + палитра vec_pal) в PNG. Ядро и разгон — как в tools/profile_vec.js.
//   node tools/hudshot_vec.js build/knightlore_vec.r0m build/knightlore_vec.sld <итераций> [out.png]
// Ключи: KLROOM=<n> комната, KLGAG=1 погасить seed_3 (детерминизм), KLSCALE=<n> масштаб (2),
//        KLCROP=y0,y1 вырезать экранные строки [y0..y1] (0 = верх кадра), KLPLANE=0..3 показать одну плоскость ч/б,
//        KLGRID=1 сетка по знакоместам 8x8 с подписями столбцов/строк ПАМЯТИ Вектора.
// Экран: адрес байта = 0x8000 + (столбец<<8) + строка_памяти; строка памяти 255 = ВЕРХ кадра (скролл 0xFF).
// Индекс палитры = p0<<3|p1<<2|p2<<1|p3 (p0 = 0x8000 ... p3 = 0xE000), байт палитры %BBGGGRRR.
"use strict";
const fs=require("fs"),vm=require("vm"),zlib=require("zlib");
const base="/Users/boris/projects/vector06js";
const [ROM,SLD,NS,OUT]=process.argv.slice(2); const N=+NS||300;
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
const cpu=new ctx.I8080(memory,io),tape={advance:()=>{},sample:0,reset:()=>{}},v06c=new ctx.Vector06c(cpu,memory,io,ay,tape);
v06c.bmp=new Uint32Array(576*288);v06c.displayFrame=()=>{};v06c.check_breakpoint=()=>false;v06c.dbg=null;
memory.init_from_array(new Uint8Array(fs.readFileSync(ROM)).subarray(0,0x10000),0);memory.detach_boot&&memory.detach_boot();
if(L.vec_snd_force!==undefined)memory.write(L.vec_snd_force,process.env.KL_SND==="vi"?2:1,false);
cpu.pc=0;cpu.sp=0xC000;cpu.iff=false;
if(VKBD!==undefined)for(let r=0;r<8;r++)memory.write(VKBD+r,0xFF,false);
const ow=memory.write.bind(memory); const poke=(a,v)=>ow(a&0xffff,v&0xff,false);
if(process.env.KLGAG){ const rdb=a=>memory.bytes[memory.tobank(a&0xffff)];
  const s3=L.seed_3&0xff, s3h=(L.seed_3>>8)&0xff;
  const pat=[0x3A,s3,s3h,0x86,0x85,0x84,0x32,s3,s3h]; let at=-1;
  for(let a=0x100;a<0xF000&&at<0;a++){let ok=true;for(let i=0;i<pat.length;i++)if(rdb(a+i)!==pat[i]){ok=false;break;}if(ok)at=a;}
  if(at<0)throw new Error("не нашёл seed_3 += mem[seed_2]");
  ow(at+3,0x00,false); }
const KEYS={z:[0,1],x:[0,2],a:[1,0],q:[2,0],"0":[4,0],space:[7,0]}; const rows=new Uint8Array(8).fill(0xFF);
const VKEYS={z:[7,0x04],x:[7,0x01],a:[4,0x02],q:[6,0x02],"0":[2,0x01],space:[7,0x80]};
function key(k,down){
  if(VKBD===undefined){const [r,b]=VKEYS[k];if(down)keyboard.matrix[r]|=b;else keyboard.matrix[r]&=~b;return;}
  const [r,b]=KEYS[k];if(down)rows[r]&=~(1<<b);else rows[r]|=(1<<b);poke(VKBD+r,rows[r]);}
let phase="menu",iter=0,steps=0;
memory.write=function(a,w,s){a&=0xffff;
  if(phase==="menu"&&cpu.pc===SYNC){poke(L.seed_1,0x34);poke(L.seed_1+1,0x12);poke(L.seed_2,0x78);poke(L.seed_2+1,0x56);poke(L.seed_3,0x9A);phase="init";}
  if(process.env.KLROOM&&phase==="init"&&a>=0xA000&&a<0xC000&&memory.read(L.graphic_objs_tbl+8,false)!==0&&!g.roomSet){g.roomSet=true;ow(L.graphic_objs_tbl+8,+process.env.KLROOM,false);ow(L.graphic_objs_tbl+40,+process.env.KLROOM,false);}
  if(a===ROOM&&phase==="init"){phase="game";key("0",false);}
  if(a===SEED2HI&&phase==="game"){iter++;}
  return ow(a,w,s);};
for(let f=0;f<60;f++)v06c.oneInterrupt(true); key("0",true);
let guard=0; while(phase!=="game"&&guard++<3000)v06c.oneInterrupt(true);
if(phase!=="game")throw new Error("не дошли до комнаты");
const startIter=iter;
let frc=0,tacc=0;
while(iter-startIter<N&&steps<2e8){if(frc>=59904){frc-=59904;v06c.irq=true;v06c.checkInterrupt();}
  const c=(cpu.instruction(),cpu.vcycles);steps++;frc+=c;tacc+=c;if(tacc>1){const tn=tacc>>1;tacc-=tn*2;timer.Count(tn);}
  if(cpu.last_opcode===0xd3)io.commit();}

// ---- палитра из vec_pal (её ISR льёт в чип) ----
const rd=a=>memory.bytes[memory.tobank(a&0xffff)];
const pal=[];for(let i=0;i<16;i++){const w=rd(L.vec_pal+i);
  const b=(w&0xc0)>>6,gr=(w&0x38)>>3,r=(w&0x07);
  pal.push([r<<5,gr<<5,b<<6]);}
console.log("vec_pal: "+[...Array(16)].map((_,i)=>i.toString(16)+"="+rd(L.vec_pal+i).toString(16).padStart(2,"0")).join(" "));

// KLDUMP=файл: плоскости 0 и 2 строками памяти (строка = "<строка памяти> <256 символов>"), для разбора зон
if(process.env.KLDUMP){const out=[];let r0=32,r1=95;if(process.env.KLDROWS){const m=process.env.KLDROWS.split(",");r0=+m[0];r1=+m[1];}
  for(let mrow=r1;mrow>=r0;mrow--){let s0="",s2="";
    for(let col=0;col<32;col++){const b0=rd(0x8000+(col<<8)+mrow),b2=rd(0xC000+(col<<8)+mrow);
      for(let bit=7;bit>=0;bit--){s0+=((b0>>bit)&1)?"#":".";s2+=((b2>>bit)&1)?"#":".";}}
    out.push(String(mrow).padStart(3)+" p0 "+s0);out.push(String(mrow).padStart(3)+" p2 "+s2);}
  fs.writeFileSync(process.env.KLDUMP,out.join("\n")+"\n");console.log("дамп плоскостей -> "+process.env.KLDUMP);}

const SCALE=+(process.env.KLSCALE||2);
const ONLY=process.env.KLPLANE!==undefined?+process.env.KLPLANE:-1;
let y0=0,y1=255; if(process.env.KLCROP){const m=process.env.KLCROP.split(",");y0=+m[0];y1=+m[1];}
const H=y1-y0+1, W=256;
const GRID=!!process.env.KLGRID, PAD=GRID?16:0;
const IW=W*SCALE+PAD, IH=H*SCALE+PAD;
const img=Buffer.alloc(IW*IH*3, 0x20);
function px(x,y,c){if(x<0||y<0||x>=IW||y>=IH)return;const o=(y*IW+x)*3;img[o]=c[0];img[o+1]=c[1];img[o+2]=c[2];}
// 3x5 цифры для подписей сетки
const DIG=["111101101101111","010110010010111","111001111100111","111001111001111","101101111001001",
           "111100111001111","111100111101111","111001001001001","111101111101111","111101111001111"];
function digit(d,x,y,c){for(let r=0;r<5;r++)for(let k=0;k<3;k++)if(DIG[d][r*3+k]==="1")px(x+k,y+r,c);}
function num(n,x,y,c){const s=String(n);for(let i=0;i<s.length;i++)digit(+s[i],x+i*4,y,c);}
for(let sy=y0;sy<=y1;sy++){            // sy — экранная строка (0 = верх кадра)
  const mrow=255-sy;                    // строка памяти Вектора
  for(let col=0;col<32;col++){
    const b=[rd(0x8000+(col<<8)+mrow),rd(0xA000+(col<<8)+mrow),rd(0xC000+(col<<8)+mrow),rd(0xE000+(col<<8)+mrow)];
    for(let bit=7;bit>=0;bit--){
      let c;
      if(ONLY>=0){const v=(b[ONLY]>>bit)&1?255:0;c=[v,v,v];}
      else{const idx=(((b[0]>>bit)&1)<<3)|(((b[1]>>bit)&1)<<2)|(((b[2]>>bit)&1)<<1)|((b[3]>>bit)&1);c=pal[idx];}
      const x=col*8+(7-bit), yy=(sy-y0);
      for(let dy=0;dy<SCALE;dy++)for(let dx=0;dx<SCALE;dx++)px(PAD+x*SCALE+dx,PAD+yy*SCALE+dy,c);
    }
  }
}
if(GRID){
  const GC=[255,0,255];
  for(let col=0;col<=32;col++){const x=PAD+col*8*SCALE;for(let y=PAD;y<IH;y++)px(x,y,GC);if(col<32)num(col,PAD+col*8*SCALE+1,4,[0,0,0]);}
  for(let sy=y0;sy<=y1+1;sy++){const mrow=255-sy;if(mrow%8!==0&&sy!==y0)continue;const y=PAD+(sy-y0)*SCALE;
    for(let x=PAD;x<IW;x++)px(x,y,GC);num(mrow,1,y+1,[0,0,0]);}
}
// ---- PNG ----
const raw=Buffer.alloc(IH*(IW*3+1));
for(let y=0;y<IH;y++){raw[y*(IW*3+1)]=0;img.copy(raw,y*(IW*3+1)+1,y*IW*3,(y+1)*IW*3);}
function chunk(type,data){const len=Buffer.alloc(4);len.writeUInt32BE(data.length);
  const td=Buffer.concat([Buffer.from(type,"latin1"),data]);const crc=Buffer.alloc(4);crc.writeUInt32BE(crc32(td)>>>0);
  return Buffer.concat([len,td,crc]);}
let TBL=null;function crc32(buf){if(!TBL){TBL=new Int32Array(256);for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;TBL[n]=c;}}
  let c=0xFFFFFFFF;for(const b of buf)c=TBL[(c^b)&0xFF]^(c>>>8);return (c^0xFFFFFFFF)>>>0;}
const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(IW,0);ihdr.writeUInt32BE(IH,4);ihdr[8]=8;ihdr[9]=2;
const png=Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk("IHDR",ihdr),chunk("IDAT",zlib.deflateSync(raw)),chunk("IEND",Buffer.alloc(0))]);
const out=OUT||"build/hudshot.png";fs.writeFileSync(out,png);
console.log(`итераций ${iter-startIter}, комната ${process.env.KLROOM||"(старт)"} -> ${out} (${IW}x${IH})`);
