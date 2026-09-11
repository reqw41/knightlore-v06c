#!/usr/bin/env python3
"""hudcolor.py <дамп hudshot_vec.js KLDUMP> — проверка «каждый элемент HUD одного цвета».
Берёт связные компоненты пикселей картинки (p0=1) в строках дампа и считает для каждого
индекс палитры (p0<<3|p1<<2|p2<<1, p3 на цвет не влияет -> берём пару). Компонента,
в которой встречается больше одного цвета, — дефект «окрас тайлами». Код возврата 1, если есть."""
import sys, collections
p0={};p1={};p2={};pal=None
for l in open(sys.argv[1]):
    q=l.rstrip('\n').split()
    if q[0]=='pal': pal=[int(t,16) for t in q[1:]]; continue
    if q[1]=='p3': continue
    {'p0':p0,'p1':p1,'p2':p2}[q[1]][int(q[0])]=q[2]
assert pal, "в дампе нет строки pal (нужен свежий hudshot_vec.js)"
pix={(x,r) for r,s in p0.items() for x,c in enumerate(s) if c=='#'}
def col(x,r):   # ЦВЕТ = байт палитры: p3 не влияет (записи парами), берём p3=0
    i = 8 | (4 if p1[r][x]=='#' else 0) | (2 if p2[r][x]=='#' else 0)
    return pal[i]
def NAME(c): return "цвет %02X" % c
seen=set(); bad=0
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
    if len(cnt)>1:
        bad+=1
        print(f"!!! x {min(xs):3}..{max(xs):3} строки {min(ys):3}..{max(ys):3} пикс {len(comp):4}: "
              + ", ".join(f"{k} {v}" for k,v in cnt.most_common()))
    elif len(comp)>=3:
        print(f"    x {min(xs):3}..{max(xs):3} строки {min(ys):3}..{max(ys):3} пикс {len(comp):4}: {list(cnt)[0]}")
print(f"разноцветных элементов: {bad}")
sys.exit(1 if bad else 0)
