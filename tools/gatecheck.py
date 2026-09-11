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
         why="клавиша A ряда ZX 1 отображена не на тот бит матрицы Вектора",
         file="tools/kl_z80_to_8080.py",
         old="DB\tvec_kbmat+4,0x02, vec_kbmat+6,0x08, vec_kbmat+4,0x10, vec_kbmat+4,0x40, vec_kbmat+4,0x80\t; A S D F G",
         new="DB\tvec_kbmat+4,0x04, vec_kbmat+6,0x08, vec_kbmat+4,0x10, vec_kbmat+4,0x40, vec_kbmat+4,0x80\t; A S D F G"),
    dict(name="input", gate="inputcheck",
         why="в раскладке 1 «вперёд» повешено на W вместо Q",
         file="tools/kl_z80_to_8080.py",
         old="DB      low (vec_kbmat+6), 0x02, 0x04           ; Q",
         new="DB      low (vec_kbmat+6), 0x80, 0x04           ; Q"),
    dict(name="hud", gate="hudcolor",
         why="зона маски HUD «цифры жизней» на столбец короче — цифры выходят двухцветными",
         file="tools/kl_z80_to_8080.py",
         old="DB\t\t0xC4, 2, 64,  8, 0xFF\t; цифры жизней: столбцы 4..5, строки 64..71",
         new="DB\t\t0xC4, 1, 64,  8, 0xFF\t; цифры жизней: столбцы 4..5, строки 64..71"),
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
    dict(name="i8080", gate="check_i8080",
         why="одна инструкция осталась Z80-only (ex af,af' не транслируется)",
         file="tools/kl_z80_to_8080.py",
         old="def _exaf():",
         new="def _exaf():\n    import os as _os\n    if _os.environ.get('KL_GATECHECK_I8080'):\n        return [\"ex af, af'\"]"),
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
    "check_plane2": None,      # особый: гейт живёт внутри build.sh vec
    "check_i8080": None,       # то же
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
    if gate in ("check_plane2", "check_i8080"):
        r = build("vec", env)
        return r.returncode, (r.stdout + r.stderr)[-400:]
    if gate == "lockstep":
        b = build("lockstep", env)
        if b.returncode:
            return b.returncode, (b.stdout + b.stderr)[-400:]
        r = run(["node", "tools/lockstep_vec.js", "build/knightlore_8080.r0m", "build/knightlore_8080.sld",
                 "600", ALIVE + "/inputs_alive.txt", ALIVE + "/zx_states600.bin"], env)
        return r.returncode, (r.stdout + r.stderr)[-300:]
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
        env = {}
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
        ok = rc != 0
        print(f"[{m['name']}] гейт {m['gate']}: {'ПОЙМАЛ' if ok else 'ПРОПУСТИЛ'} — {m['why']}")
        if not ok:
            print("   вывод гейта: " + out.replace("\n", " | ")[-300:])
            bad += 1
    print("все гейты ловят свою порчу" if not bad else f"ГЕЙТОВ-СЛЕПЫХ (или устаревших порч): {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *a: (run(["git", "checkout", "--", "tools"]), sys.exit(130)))
    sys.exit(main())
