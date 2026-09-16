#!/usr/bin/env python3
"""САМОПРОВЕРКА ГЕЙТОВ: в каждый гейт вносится порча, которую он ОБЯЗАН поймать.

Гейт, который никогда не краснел, ничего не доказывает: он может сравнивать сборку саму с собой
или проверять не тот код, что идёт в игру. Здесь по одной порче на гейт — в tools/ (а не в src/),
потому что именно там живёт вся склейка и весь нативный код.

  python3 tools/gatecheck.py [имя_порчи ...]      (без аргументов — все)

Порча вносится в рабочее дерево и ОТКАТЫВАЕТСЯ `git checkout --` в любом случае (в том числе по
Ctrl+C). Перед запуском рабочее дерево должно быть чистым.
"""
import os, re, subprocess, sys, signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIVE = None
for d in ("../knightlore-research", "../../knightlore-research"):
    p = os.path.join(ROOT, d, "zx-stand")
    if os.path.isdir(p):
        ALIVE = os.path.abspath(p)
        break

T = "\t"

MUTS = [
    dict(name="render", gate="render_gate",
         why="окно update_screen смещено на строку вниз (чистый ВЫВОД: логика не меняется)",
         file="tools/kl_z80_to_8080.py",
         old="us_dst:\t\t\tld\t\th, 0\t\t\t\t\t\t; патч: страница экрана\n\t\t\t\tld\t\tl, 32",
         new="us_dst:\t\t\tld\t\th, 0\t\t\t\t\t\t; патч: страница экрана\n\t\t\t\tld\t\tl, 33"),
    dict(name="logic", gate="lockstep",
         why="save_2d_info пишет поля obj+29/obj+30 наоборот (игровое состояние)",
         file="tools/kl_native_render.py",
         old="\t\t\t\tld\t\t(hl), d\n\t\t\t\tinc\t\thl\n\t\t\t\tld\t\t(hl), e\n",
         new="\t\t\t\tld\t\t(hl), e\n\t\t\t\tinc\t\thl\n\t\t\t\tld\t\t(hl), d\n"),
    dict(name="nobj", gate="nobjcheck",
         why="граница занятости таблицы объектов на единицу НИЖЕ последнего живого",
         file="tools/kl_native_logic.py",
         old="zeg_got:\t\tld\t\ta, b\n",
         new="zeg_got:\t\tld\t\ta, b\n\t\t\t\tdec\t\ta\n"),
    dict(name="flip", gate="flipcheck",
         why="flip_sprite_data сравнивает только бит V (and 0x80 вместо 0xC0) — H-разворот теряется",
         file="tools/kl_native.py",
         old="\t\t\t\tld\t\ta, (de)\n\t\t\t\txor\t\t(hl)\n\t\t\t\tand\t\t0xC0\n\t\t\t\tret\t\tz\t\t\t\t\t\t\t\t\t; быстрый путь: спрайт уже как надо\n",
         new="\t\t\t\tld\t\ta, (de)\n\t\t\t\txor\t\t(hl)\n\t\t\t\tand\t\t0x80\n\t\t\t\tret\t\tz\t\t\t\t\t\t\t\t\t; быстрый путь: спрайт уже как надо\n"),
    dict(name="kbd", gate="kbcheck",
         why="клавиша A ряда ZX 1 отображена не на тот бит матрицы Вектора (ловится только эталоном tools/kbref.txt)",
         file="tools/kl_z80_to_8080.py",
         old="DB\tvec_kbmat+4,0x02, vec_kbmat+6,0x08, vec_kbmat+4,0x10, vec_kbmat+4,0x40, vec_kbmat+4,0x80\t; A S D F G",
         new="DB\tvec_kbmat+4,0x04, vec_kbmat+6,0x08, vec_kbmat+4,0x10, vec_kbmat+4,0x40, vec_kbmat+4,0x80\t; A S D F G"),
    dict(name="input", gate="inputcheck",
         why="в раскладке 1 «вперёд» повешено на W вместо Q",
         file="tools/kl_z80_to_8080.py",
         old="DB      low (vec_kbmat+6), 0x02, 0x04           ; Q",
         new="DB      low (vec_kbmat+6), 0x80, 0x04           ; Q"),
    dict(name="joy", gate="inputcheck",
         why="джойстик: в раскладке 3 FIRE2 сведена к биту 4 («взять» поворотных), а не к биту 5",
         file="tools/kl_z80_to_8080.py",
         old="DB      0x80, 0x20                              ; FIRE2 -> взять/бросить  (как цифры)",
         new="DB      0x80, 0x10                              ; FIRE2 -> взять/бросить  (как цифры)"),
    dict(name="joyzx", gate="inputcheck",
         why="кнопка джойстика не прижимает клавишу '0' в рядах ZX — меню с джойстика не запускается",
         file="tools/kl_z80_to_8080.py",
         old="vj_fire:\t\tld\t\thl, vec_zxrows+4\t; FIRE -> клавиша '0' (ряд ZX 4, бит 0): старт игры в меню\n\t\t\t\tld\t\ta, (hl)\t\t\t\t; и «любая клавиша» на экранах мелодий\n\t\t\t\tand\t\t0xFE\n",
         new="vj_fire:\t\tld\t\thl, vec_zxrows+4\t; FIRE -> клавиша '0' (ряд ZX 4, бит 0): старт игры в меню\n\t\t\t\tld\t\ta, (hl)\t\t\t\t; и «любая клавиша» на экранах мелодий\n\t\t\t\tand\t\t0xFF\n"),
    dict(name="pal", gate="palcheck",
         why="палитра новой комнаты подаётся БЕЗ гашения (состояние bw-1.1) — старая картинка перекрашивается на глазах",
         file="tools/kl_z80_to_8080.py",
         old="'\\t\\t\\t\\tcall    vec_scr_off                             ; погасить, дождаться, скопировать\\n'",
         new="'\\t\\t\\t\\tcall    update_screen\\n'"),
    dict(name="pallate", gate="palcheck",
         why="гашение переставлено ПОСЛЕ копии — новая комната выезжает столбцами в цвете старой (первый инвариант этого НЕ видит)",
         file="tools/kl_z80_to_8080.py",
         old="vec_scr_off:" + T + "xor" + T*2 + "a" + T*5 + "; чернила 0 = ЧЁРНЫЙ (см. vec_zxcol): гасим поле комнаты\n"
             + T*4 + "call" + T + "vec_set_pal" + T*3 + "; заливает vec_pal и ставит vec_palreq\n"
             + "pof_w:" + T*3 + "ld" + T*2 + "a, (vec_palreq)\n" + T*4 + "or" + T*2 + "a\n"
             + T*4 + "jp" + T*2 + "nz, pof_w\n" + T*4 + "jp" + T*2 + "update_screen",
         new="vec_scr_off:" + T + "call" + T + "update_screen\n"
             + T*4 + "xor" + T*2 + "a\n"
             + T*4 + "call" + T + "vec_set_pal\n"
             + "pof_w:" + T*3 + "ld" + T*2 + "a, (vec_palreq)\n" + T*4 + "or" + T*2 + "a\n"
             + T*4 + "jp" + T*2 + "nz, pof_w\n" + T*4 + "ret"),
    dict(name="hud", gate="hudcolor",
         why="зона маски HUD «цифры жизней» сдвинута на 4 строки — цифра РЕЖЕТСЯ на два цвета",
         file="tools/kl_z80_to_8080.py",
         old="DB\t\t0xC4, 2, 64,  8, 0xFF\t; цифры жизней: столбцы 4..5, строки 64..71",
         new="DB\t\t0xC4, 2, 68,  8, 0xFF\t; цифры жизней: столбцы 4..5, строки 64..71"),
    dict(name="hudclip", gate="hudguard",
         why="клип по краю панели снят целиком — окно объекта затирает полосу 32..63",
         file="tools/kl_z80_to_8080.py",
         old="\t\t\t\tcp\t\t96\n\t\t\t\tjp\t\tnc, blit_to_screen\t; окно выше всей лестницы",
         new="\t\t\t\tcp\t\t96\n\t\t\t\tjp\t\tblit_to_screen\t; окно выше всей лестницы"),
    dict(name="hudstair", gate="hudguard_exit", env={"KLDIRS": "down", "KLLIVES": "1"},
         why="верхний край панели вырождается в строку 64 (состояние до §30): лестницу по нижним "
             "углам ромба затирает шаг героя за кромку комнаты",
         file="tools/kl_z80_to_8080.py",
         old="\t\t\t\tadd\t\ta, 10\t\t\t\t; 9 - m, перенос при m <= 9",
         new="\t\t\t\tadd\t\ta, 0\t\t\t\t; 9 - m, перенос при m <= 9"),
    dict(name="hudmin", gate="hudguard_exit", env={"KLDIRS": "down", "KLLIVES": "1"},
         why="из двух краёв окна берётся не тот: граница считается по ПРАВОМУ краю всегда, "
             "и левый край окна проходит под лестницей",
         file="tools/kl_z80_to_8080.py",
         old=".kh2:\t\t\tcp\t\te\n\t\t\t\tjp\t\tc, khc_3",
         new=".kh2:\t\t\tcp\t\te\n\t\t\t\tjp\t\tkhc_3"),
    dict(name="hudrow", gate="hudguard_exit", env={"KLDIRS": "down", "KLLIVES": "1"},
         why="обрезанное окно копируется на ИСХОДНУЮ строку приёмника, а не на первую строку поля",
         file="tools/kl_z80_to_8080.py",
         old="\t\t\t\tld\t\te, a\t\t\t\t; копировать с первой строки поля",
         new="\t\t\t\tld\t\te, l\t\t\t\t; копировать с первой строки поля"),
    # --- ВТОРОЕ ПЕРО HUD (§32) ---
    dict(name="hudpen", gate="hudguard_items", env={"KLDIRS": "down", "KLITEMS": "1", "KLLIVES": "1"},
         why="vec_hud_p1 берёт силуэт не из плоскости 0, а из чужой страницы — под жёлтыми зонами "
             "p1 расходится с картинкой, предметы и знак красятся наполовину",
         file="tools/kl_z80_to_8080.py",
         old="\t\t\t\tld\t\ta, 0x20\t\t\t\t; приёмник 0xA0+столбец (буфер), источник 0x80+столбец",
         new="\t\t\t\tld\t\ta, 0x40\t\t\t\t; приёмник 0xA0+столбец (буфер), источник 0x80+столбец"),
    dict(name="hudcsb", gate="hudguard_items", env={"KLDIRS": "down", "KLITEMS": "1", "KLLIVES": "1"},
         why="clear_scrn_buffer не возвращает p1 после обнуления буфера — на итерации смены комнаты "
             "предметы выходят белыми (ловится ТОЛЬКО сценарием реального выхода через арку)",
         file="tools/kl_z80_to_8080.py",
         old="\\t\\t\\t\\tjp      vec_hud_p1\\t\\t\\t\\t; буфер под жёлтыми зонами обнулён -> вернуть p1 := p0 (§32)",
         new="\\t\\t\\t\\tret\\t\\t\\t\\t; буфер под жёлтыми зонами обнулён -> вернуть p1 := p0 (§32)"),
    dict(name="hud2x8", gate="hudcolor_day",
         why="blit_2x8 оставляет цифры в буфере — после смены дня счётчик красится вторым пером "
             "(дефект 1 из 2cdd694)",
         file="tools/kl_z80_to_8080.py",
         old="        '\\t\\t\\t\\tjp      vidbuf_fill_window\\n', src, count=1)",
         new="        '\\t\\t\\t\\tret\\n', src, count=1)"),
    dict(name="tune", gate="tunecheck",
         why="команда END потока мелодии не распознаётся — проигрыватель не кончает мелодию",
         file="tools/kl_sound_ay.py",
         old="\t\t\t\tcp\t\t7\n\t\t\t\tjp\t\tz, mf_end\n",
         new="\t\t\t\tcp\t\t9\n\t\t\t\tjp\t\tz, mf_end\n"),
    dict(name="plane2", gate="check_plane2",
         why="под код отданы строки ВНУТРИ картинки плоскости 2 (белит точки поля)",
         file="tools/kl_z80_to_8080.py",
         old="def vec_holes(base, pages=32):",
         new="def vec_holes(base, pages=32):\n    import os as _os\n    if _os.environ.get('KL_GATECHECK_PLANE2') and base == 0xC000:\n        return [(base + (p << 8) + 100, 60) for p in range(pages)]"),
    dict(name="ixflags", gate="check_ixflags",
         why="хелпер il_ix_dec (портит перенос) поставлен прямо перед чтением переноса — исторический баг upd_103",
         file="tools/kl_native_logic.py",
         old="\t\t\t\tjp\t\tnc, loc_C1EE\t\t\t\t\t\t; не рядом — пропустить",
         new="\tcall il_ix_dec\n\tdb 4\n\t\t\t\tjp\t\tnc, loc_C1EE\t\t\t\t\t\t; не рядом — пропустить"),
    dict(name="packlen", gate="packgate",
         why="распаковщик теряет последний байт каждой копии с новым смещением (длина на 1 меньше)",
         file="tools/mkrelease.py",
         old="dz_lend:\tinc\thl\n\t\tcall\tdz_copy",
         new="dz_lend:\tcall\tdz_copy"),
    dict(name="packmargin", gate="packgate",
         why="запас для распаковки «на месте» меньше delta: поток нижнего региона обгонит запись",
         file="tools/mkrelease.py",
         old="TAIL = 256                       # хвост потока B, уезжающий в плоскость 0",
         new="TAIL = 1                         # хвост потока B, уезжающий в плоскость 0"),
    dict(name="packtune", gate="packgate",
         why="зеркало регистров AY не переносится в ВИ53 — на заставке тишина",
         file="tools/mkrelease.py",
         old="\t\tcall\tmsx_viflush\t\t; зеркало регистров AY -> ВИ53",
         new="\t\t;call\tmsx_viflush\t\t; зеркало регистров AY -> ВИ53"),
    dict(name="i8080", gate="check_i8080",
         why="одна инструкция осталась Z80-only (bit n,r не транслируется)",
         file="tools/kl_z80_to_8080.py",
         old="def _bit(n, reg):",
         new="def _bit(n, reg):\n    import os as _os\n    if _os.environ.get('KL_GATECHECK_I8080'):\n        return ['bit %s, %s' % (n, reg)]"),
]

GATES = {
    "render_gate": ["tools/render_gate.sh", "HEAD", "120", "39"],
    "lockstep": None,          # особый: собрать цель lockstep и прогнать журнал ZX
    "nobjcheck": ["node", "tools/nobjcheck_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld", "60"],
    "flipcheck": ["node", "tools/flipcheck_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld", "3"],
    "kbcheck": ["node", "tools/kbcheck_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld", "gc"],
    "inputcheck": ["node", "tools/inputcheck_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld"],
    "tunecheck": ["node", "tools/tunecheck_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld", "3000"],
    "hudcolor": None,          # особый: снять дамп и прогнать hudcolor.py
    "hudguard": ["node", "tools/hudguard_vec.js", "build/knightlore_vec.r0m",
                 "build/knightlore_vec.sld", "120", "1-4"],
    # сценарий РЕАЛЬНОГО выхода через арки: комната «start» (номер не подменять) — только там
    # герой ходит по-настоящему и доводит окно объекта до кромки комнаты, к лестнице панели (§30)
    "hudguard_exit": ["node", "tools/hudguard_vec.js", "build/knightlore_vec.r0m",
                      "build/knightlore_vec.sld", "120", "start"],
    # тот же сценарий, но с предметами в инвентаре: без них ЖЁЛТАЯ зона предметов пуста,
    # и инвариант второго пера (§32) по ней ничего не проверяет
    "hudguard_items": ["node", "tools/hudguard_vec.js", "build/knightlore_vec.r0m",
                       "build/knightlore_vec.sld", "120", "start"],
    "hudcolor_day": None,      # особый: снимок ПОСЛЕ смены дня (счётчик печатает blit_2x8)
    "palcheck": None,          # особый: свой набор ключей сценария
    "check_plane2": None,      # особый: гейт живёт внутри build.sh vec
    "check_i8080": None,       # то же
    "check_ixflags": None,     # то же
    "packgate": None,          # особый: собрать цель release и прогнать tools/packgate_vec.js
}


def run(cmd, env=None, cwd=ROOT):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True)


def build(target, env=None):
    return run(["./build.sh", target], env=env)


def gate_result(gate, env=None):
    """-> (rc, хвост вывода). rc != 0 = гейт покраснел."""
    if gate in ("check_plane2", "check_i8080", "check_ixflags"):
        r = build("vec", env)
        return r.returncode, (r.stdout + r.stderr)[-400:]
    if gate == "lockstep":
        b = build("lockstep", env)
        if b.returncode:
            return b.returncode, (b.stdout + b.stderr)[-400:]
        r = run(["node", "tools/lockstep_vec.js", "build/knightlore_8080.r0m", "build/knightlore_8080.sld",
                 "600", ALIVE + "/inputs_alive.txt", ALIVE + "/zx_states600.bin"], env)
        return r.returncode, (r.stdout + r.stderr)[-300:]
    if gate == "packgate":
        b = build("release", env)
        if b.returncode:
            return b.returncode, (b.stdout + b.stderr)[-400:]
        r = run(["node", "tools/packgate_vec.js"], env)
        return r.returncode, (r.stdout + r.stderr)[-500:]
    if gate == "palcheck":
        b = build("vec", env)
        if b.returncode:
            return b.returncode, (b.stdout + b.stderr)[-400:]
        e = {"KLGAG": "1", "KLLIVES": "1", "KLARCH": "40"}
        e.update(env or {})
        r = run(["node", "tools/palcheck_vec.js", "build/knightlore_vec.r0m",
                 "build/knightlore_vec.sld", "500"], e)
        return r.returncode, (r.stdout + r.stderr)[-400:]
    if gate == "hudcolor_day":
        # СМЕНА ДНЯ БЕЗ ТЫСЯЧИ ИТЕРАЦИЙ: знак идёт по арке на 1 точку за 8 итераций и меняет
        # день, дойдя до X=225. Кладём X=224 дважды (первый переход — на луну, день растёт на
        # втором), и счётчик перепечатывается через print_days + blit_2x8 уже к итерации 60.
        b = build("vec", env)
        if b.returncode:
            return b.returncode, (b.stdout + b.stderr)[-400:]
        e = {"KLGAG": "1", "KLLIVES": "1", "KLDUMP": "build/gc_dump.txt",
             "KLPOKE": "sun_moon_scratchpad+26=224@5;sun_moon_scratchpad+26=224@30"}
        e.update(env or {})
        run(["node", "tools/hudshot_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld",
             "60", "build/gc.png"], e)
        r = run(["python3", "tools/hudcolor.py", "build/gc_dump.txt"], env)
        return r.returncode, (r.stdout + r.stderr)[-400:]
    if gate == "hudcolor":
        b = build("vec", env)
        if b.returncode:
            return b.returncode, (b.stdout + b.stderr)[-400:]
        e = {"KLGAG": "1", "KLDUMP": "build/gc_dump.txt"}
        e.update(env or {})
        run(["node", "tools/hudshot_vec.js", "build/knightlore_vec.r0m", "build/knightlore_vec.sld",
             "80", "build/gc.png"], e)
        r = run(["python3", "tools/hudcolor.py", "build/gc_dump.txt"], env)
        return r.returncode, (r.stdout + r.stderr)[-400:]
    b = build("vec", env)
    if b.returncode:
        return b.returncode, (b.stdout + b.stderr)[-400:]
    r = run(GATES[gate], env)
    return r.returncode, (r.stdout + r.stderr)[-400:]


def main():
    want = sys.argv[1:]
    dirty = run(["git", "status", "--porcelain", "tools", "src"]).stdout.strip()
    if dirty:
        print("рабочее дерево не чистое — сначала закоммить:\n" + dirty)
        return 2
    bad = 0
    for m in MUTS:
        if want and m["name"] not in want:
            continue
        env = dict(m.get("env") or {})
        if m["name"] == "plane2":
            env["KL_GATECHECK_PLANE2"] = "1"
        if m["name"] == "i8080":
            env["KL_GATECHECK_I8080"] = "1"
        path = os.path.join(ROOT, m["file"])
        src = open(path, encoding="utf-8").read()
        n = src.count(m["old"])
        if n != 1:
            print(f"[{m['name']}] ЯКОРЬ ПОРЧИ НЕ НАЙДЕН в {m['file']} (вхождений {n}) — порча устарела")
            bad += 1
            continue
        open(path, "w", encoding="utf-8").write(src.replace(m["old"], m["new"]))
        try:
            rc, out = gate_result(m["gate"], env)
        finally:
            run(["git", "checkout", "--", m["file"]])
            drop_pyc()
            # пересобрать ОБЕ цели: иначе в build/ остаётся испорченный образ, и следующий
            # ручной прогон гейта краснеет «на ровном месте» (наступали)
            build("lockstep"); build("vec")
        ok = rc != 0
        print(f"[{m['name']}] гейт {m['gate']}: {'ПОЙМАЛ' if ok else 'ПРОПУСТИЛ'} — {m['why']}")
        if not ok:
            print("   вывод гейта: " + out.replace("\n", " | ")[-300:])
            bad += 1
    print("все гейты ловят свою порчу" if not bad else f"ГЕЙТОВ-СЛЕПЫХ (или устаревших порч): {bad}")
    return 1 if bad else 0


def drop_pyc():
    """Снести кэш байткода Python в tools/.

    ТРЕТЬЯ ЛОВУШКА САМОПРОВЕРКИ (стоила красного render_gate «на ровном месте», 2026-09-12).
    Python держит .pyc валидным, пока совпадают mtime источника В СЕКУНДАХ и его размер. Порча
    вида «0xC0 -> 0x80» РАЗМЕР НЕ МЕНЯЕТ, а быстрый гейт успевает отработать внутри одной
    секунды, так что после `git checkout --` у восстановленного файла та же секунда mtime —
    и кэш считается годным. Дальше ВСЕ сборки берут ИСПОРЧЕННЫЙ байткод: исходник чистый,
    git status чистый, а в образе живёт порча. Ловится только сверкой .asm с эталоном.
    """
    d = os.path.join(ROOT, "tools", "__pycache__")
    if os.path.isdir(d):
        for f in os.listdir(d):
            os.unlink(os.path.join(d, f))
        os.rmdir(d)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *a: (run(["git", "checkout", "--", "tools"]), drop_pyc(), sys.exit(130)))
    sys.exit(main())
