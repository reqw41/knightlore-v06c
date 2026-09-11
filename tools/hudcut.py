#!/usr/bin/env python3
"""hudcut.py <дамп hudshot_vec.js KLDUMP> — режет ли маска (плоскость 2) связные элементы картинки (плоскость 0).
Печатает по компоненту: целиком белый / целиком цвет комнаты / РАЗРЕЗАН. Код возврата 1, если есть разрезанные."""
import sys, collections
p0={}; p2={}
for l in open(sys.argv[1]):
    p=l.rstrip('\n').split()
    if not p or p[0]=='pal': continue        # первая строка дампа — палитра, не строка плоскости
    if p[1] not in ('p0','p2'): continue     # дамп несёт и p1/p3 — они на цвет не влияют
    (p0 if p[1]=='p0' else p2)[int(p[0])]=p[2]
pix={(x,r) for r,s in p0.items() for x,c in enumerate(s) if c=='#'}
white=lambda x,y: p2[y][x]=='#'
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
    ins=[c for c in comp if white(*c)]; out=[c for c in comp if not white(*c)]
    xs=[x for x,y in comp]; ys=[y for x,y in comp]
    if ins and out:
        bad+=1
        bx=[x for x,y in ins]; by=[y for x,y in ins]
        tag=f"!!! РАЗРЕЗАН !!! белая часть x {min(bx)}..{max(bx)} строки {min(by)}..{max(by)} ({len(ins)}/{len(comp)} пикс)"
    else:
        tag="белый" if ins else "цвет комнаты"
    if len(comp)>=3 or ins: print(f"x {min(xs):3}..{max(xs):3} строки {min(ys):3}..{max(ys):3} пикс {len(comp):4}  {tag}")
print(f"разрезанных элементов: {bad}")
sys.exit(1 if bad else 0)
