#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Инвентаризация сборки по РЕАЛЬНО выпущенным байтам (адресные дельты строк листинга, без промежутков ORG).
   python3 tools/inventory_vec.py build/knightlore_vec.lst [build/knightlore_z80.lst] [--top N]
Категории: перевод (код игры Z80->i8080), хелперы il_ (IX/IY и пр.), склейка Вектора (vec_/ps_/bts_/…),
звук MSX (msx_/mf_/msp_), kl_ (нативные переписки), спрайты spr_, таблицы данных игры, переменные, таблицы сдвига.
Со вторым листингом (z80vec, замороженная Z80-сборка) печатает для переведённых процедур байты Z80 и коэффициент."""
import re,sys
VARS=set()
def parse(path):
    rows=[]  # (addr, label, is_data, src)
    lab=None
    for l in open(path,encoding='latin1'):
        m=re.match(r'^\s*\d+\+?\s+([0-9A-F]{4})\s(.*)$',l.rstrip('\n'))
        if not m: continue
        addr=int(m.group(1),16); rest=m.group(2)
        # байты (пары hex) впереди, потом исходник
        src=rest[13:] if len(rest)>13 else ''   # колонка байтов фиксированной ширины (11 + 2 пробела); 'DB' сам похож на hex-байт
        src=re.sub(r'^\s+','',src)
        if re.match(r'^(ORG|ASSERT|DEFINE|equ|EQU|IF|ENDIF|ELSE|MACRO|ENDM|END|DEVICE|OUTPUT)\b',src,re.I) or re.search(r'\s(equ|EQU)\s',src):
            rows.append((addr,None,None,'ORG' if src.upper().startswith('ORG') else 'PSEUDO')); continue
        lm=re.match(r'^([A-Za-z_.@][\w.@]*):',src)
        if lm and not lm.group(1).startswith('.'):
            lab=lm.group(1); src=src[len(lab)+1:].strip()
        if not src or src.startswith(';'):
            if not src and rows and rows[-1][2] is not None and not lm:
                rows.append((addr,rows[-1][1],rows[-1][2],'CONT')); continue
            rows.append((addr,lab,None,'LABEL')); continue
        isdata=bool(re.match(r'^(db|dw|ds|defb|defw|defs|dz|dh|dc|byte|word|block|incbin)\b',src,re.I))
        rows.append((addr,lab,isdata,src))
    # размер строки = адрес следующей строки − адрес; ORG рвёт сегмент (адрес строки ORG = конец предыдущей)
    sizes={}  # label -> [code, data]
    for i,(a,lab,isd,src) in enumerate(rows):
        if isd is None: continue
        nxt=None
        for j in range(i+1,len(rows)):
            if rows[j][3]=='PSEUDO': continue
            nxt=rows[j][0]; break
        if nxt is None and a>=0xF000: nxt=0x10000
        if nxt is None or nxt<a or nxt-a>8192: continue
        if 0x7A80<=a<0x8000: VARS.add(lab or '?')
        s=sizes.setdefault(lab or '?',[0,0]); s[1 if isd else 0]+=nxt-a
    return sizes
import os
NATIVE=set()
for f in ('tools/kl_native_logic.py','tools/kl_native_render.py','tools/kl_native.py'):
    if os.path.exists(f):
        NATIVE.update(re.findall(r'^([A-Za-z_][\w]*):',open(f,encoding='utf8').read(),re.M))
def cat(lab):
    if lab in NATIVE: return 'нативные переписки (kl_native_*)'
    if lab in VARS: return 'переменные'
    if lab.startswith('spr_'): return 'спрайты'
    if lab.startswith(('il_','IL_')): return 'хелперы il_'
    if lab.startswith(('msx_','mf_','msp_','audio_msx','vec_snd','vi_')): return 'звук MSX/ВИ53'
    if lab.startswith(('vec_','ps_','bts_','vfw_','vdb_','vpb_','us_','usl_','vb_','rp_','kl_ctl','kl_direct','kl_kb','screen_calc','vidbuf_calc','N_','ret_from_tbl')): return 'склейка Вектора/рендер'
    if lab.startswith('kl_'): return 'kl_ нативные переписки'
    if lab in ('reverse_bits_table','shift_bits_table'): return 'таблицы сдвига'
    return 'перевод Z80->i8080'
def main():
    args=[a for a in sys.argv[1:] if not a.startswith('--')]; top=30
    for a in sys.argv[1:]:
        if a.startswith('--top='): top=int(a[6:])
    vec=parse(args[0]); z80=parse(args[1]) if len(args)>1 else {}
    cats={}
    for lab,(c,d) in vec.items():
        k=cat(lab)
        if k=='перевод Z80->i8080' and d and not c: k='таблицы данных игры'
        if k=='перевод Z80->i8080' and d and c: k='перевод Z80->i8080'
        cc=cats.setdefault(k,[0,0]); cc[0]+=c; cc[1]+=d
    print('%-28s %7s %7s %7s'%('категория','код','данные','всего'))
    tc=td=0
    for k,(c,d) in sorted(cats.items(),key=lambda x:-(x[1][0]+x[1][1])):
        print('%-28s %7d %7d %7d'%(k,c,d,c+d)); tc+=c; td+=d
    print('%-28s %7d %7d %7d'%('ИТОГО',tc,td,tc+td))
    if z80:
        print('\nперевод: процедуры по байтам (vec / z80 / коэффициент), топ %d'%top)
        rows=[]
        for lab,(c,d) in vec.items():
            if cat(lab)!='перевод Z80->i8080' or c==0: continue
            zc=z80.get(lab,[0,0])[0]
            rows.append((c,zc,lab))
        rows.sort(reverse=True)
        sv=sz=0
        for c,zc,lab in rows:
            sv+=c; sz+=zc
        print('всего кода перевода: vec %d, z80 %d, коэффициент %.2f'%(sv,sz,sv/max(1,sz)))
        for c,zc,lab in rows[:top]:
            print('%-34s %5d %5d %5s'%(lab,c,zc,('%.2f'%(c/zc)) if zc else '-'))
main()
