#!/usr/bin/env python3
"""hudcolor.py <дамп hudshot_vec.js KLDUMP> — окрас панели.

Две проверки на каждую связную компоненту пикселей картинки (p0=1) в строках дампа:
  1) РОВНЫЙ ОКРАС: у всей компоненты один байт палитры (индекс p0<<3|p1<<2|p2<<1, p3 на цвет
     не влияет — записи парами). Разноцветная компонента — «окрас тайлами»;
  2) ЦВЕТ ПО ЗОНЕ (§32): зоны берутся ИЗ ОБРАЗА (строки `zone` дампа), а не задаются здесь.
     vec_hudbuf — ЖЁЛТЫЕ зоны (предметы инвентаря, знак солнца/луны): ждём второе перо pal[14];
     vec_hudcols минус жёлтые — БЕЛЫЕ зоны (цифры жизней, фигурка, DAY, счётчик): ждём pal[10];
     всё остальное в панели (рамка, колонны, шнур, скат, арка) — цвет комнаты pal[8].
Код возврата 1, если есть разноцветная компонента ИЛИ компонента не того цвета, что ждёт зона."""
import sys, collections
p0={};p1={};p2={};pal=None;zones=[]
for l in open(sys.argv[1]):
    q=l.rstrip('\n').split()
    if q[0]=='pal': pal=[int(t,16) for t in q[1:]]; continue
    if q[0]=='zone': zones.append((q[1],int(q[2]),int(q[3]),int(q[4]),int(q[5]))); continue
    if q[1]=='p3': continue
    {'p0':p0,'p1':p1,'p2':p2}[q[1]][int(q[0])]=q[2]
assert pal, "в дампе нет строки pal (нужен свежий hudshot_vec.js)"
assert zones, "в дампе нет строк zone (нужен свежий hudshot_vec.js)"
# ДВА ПЕРА ОБЯЗАНЫ ОТЛИЧАТЬСЯ, иначе проверка «цвет по зоне» ничего не проверяет
assert pal[10] != pal[14], "перья HUD совпали (pal[10] == pal[14]) — проверка по зонам пуста"
def rect(tbl, base):
    s=set()
    for name,pg,nc,r0,nr in [(n,p,c,r,h) for n,p,c,r,h in zones if n==tbl]:
        for c in range(pg-base, pg-base+nc):
            for r in range(r0, r0+nr): s |= {(c*8+b, r) for b in range(8)}
    return s
ZY = rect('vec_hudbuf', 0xA0)          # жёлтые зоны: предметы и знак солнца/луны
ZM = rect('vec_hudcols', 0xC0)         # вся маска HUD
ZW = ZM - ZY                           # белые зоны
assert ZY and ZW and ZY <= ZM, "таблицы зон разъехались: жёлтая зона вне маски HUD"
pix={(x,r) for r,s in p0.items() for x,c in enumerate(s) if c=='#'}
def col(x,r):   # ЦВЕТ = байт палитры: p3 не влияет (записи парами), берём p3=0
    i = 8 | (4 if p1[r][x]=='#' else 0) | (2 if p2[r][x]=='#' else 0)
    return pal[i]
def want(x,r):  # какого цвета ЗОНА обязана быть
    return pal[14] if (x,r) in ZY else pal[10] if (x,r) in ZW else pal[8]
def zname(x,r):
    return "жёлтая (предмет/знак)" if (x,r) in ZY else "белая (цифры/DAY)" if (x,r) in ZW else "рамка (цвет комнаты)"
def NAME(c): return "цвет %02X" % c
seen=set(); bad=0; badzone=0
for p in sorted(pix):
    if p in seen: continue
    q=collections.deque([p]); seen.add(p); comp=[]
    while q:
        x,y=q.popleft(); comp.append((x,y))
        for dx in(-1,0,1):
            for dy in(-1,0,1):
                n=(x+dx,y+dy)
                if n in pix and n not in seen: seen.add(n); q.append(n)
    cnt=collections.Counter(NAME(col(*c)) for c in comp)
    xs=[x for x,y in comp]; ys=[y for x,y in comp]
    where=f"x {min(xs):3}..{max(xs):3} строки {min(ys):3}..{max(ys):3} пикс {len(comp):4}"
    if len(cnt)>1:
        bad+=1
        print(f"!!! {where}: " + ", ".join(f"{k} {v}" for k,v in cnt.most_common()))
        continue
    wrong=collections.Counter((zname(*c), NAME(want(*c)), NAME(col(*c))) for c in comp if want(*c)!=col(*c))
    if wrong:
        badzone+=1
        for (zn,w,g),v in wrong.most_common(3):
            print(f"!!! {where}: зона {zn} ждёт {w}, а покрашено в {g} ({v} точек)")
    elif len(comp)>=3:
        print(f"    {where}: {list(cnt)[0]} — {zname(*comp[0])}")
print(f"разноцветных элементов: {bad}, элементов не того цвета, что ждёт зона: {badzone}")
sys.exit(1 if bad or badzone else 0)
