#!/usr/bin/env python3
"""kl_z80_to_8080.py — source-level Z80 -> i8080 translation for Knight Lore (sjasmplus syntax).
Core (split_line/expand/_ixiy/_exx/_classify_exx/_bit/_resset/_shift/_ldir/_adcsbc_hl/translate)
is lifted verbatim from kvalley-v06c/tools/kv_z80_to_8080.py (tag asm-freeze); glue is Knight Lore specific.
Usage: python3 tools/kl_z80_to_8080.py <in.asm> <out.asm> [--target lockstep|vector]
"""
import sys, re, os
sys.path.insert(0, __import__('os').path.dirname(__file__))
import kl_native
import kl_native_render
import kl_native_logic
import kl_sound_ay


# scratch cell names (defined as MAP fields appended to the work area)
SCRATCH = ['z80_ix', 'z80_iy', 'z80_af_alt', 'z80_bc_alt', 'z80_de_alt',
           'z80_hl_alt', 'il_tmp', 'il_tmp2', 'il_a']
SCRATCH_DEFS = "\n".join(f"{n}:\t# 2" for n in SCRATCH) + "\nil_outstub:\t# 3"


def split_line(line):
    """Return (label, body, comment). label includes trailing ':' if present."""
    # comment
    comment = ''
    # find ';' not inside quotes (rare in this source); simple split is fine
    if ';' in line:
        i = line.index(';')
        comment = line[i:]
        line = line[:i]
    # label: leading token at col 0 ending with ':' (or a bare label at col0)
    label = ''
    m = re.match(r'^([A-Za-z_@?.][\w@?.$]*:)\s*(.*)$', line)
    if m:
        label = m.group(1)
        body = m.group(2)
    else:
        body = line.strip() if line.strip() else ''
        if line and not line[0].isspace():
            # bare label at col0 without colon (Sjasm MAP fields use this) -> leave as-is
            return (line.rstrip(), '', comment)
    return (label, body.strip(), comment)


def operands(body):
    """mnemonic, [op1, op2]"""
    parts = body.split(None, 1)
    mn = parts[0].lower()
    ops = [x.strip() for x in parts[1].split(',')] if len(parts) > 1 else []
    return mn, ops


def reg8(r):
    """normalize an 8-bit register token; (ix+d)/(iy+d) handled separately."""
    return r.strip().lower()


# ----- IX/IY effective-address helper: HL = ix/iy + disp -----
def _idx_addr(idxcell, disp):
    """Build effective address (idx+disp) into HL, clobbering A ONLY (caller saves
    A+flags via push af where needed). disp is symbolic; the assembler splits it into
    low/high bytes (handles negative via two's complement masks). HL is overwritten;
    caller must have saved the original HL (il_tmp2)."""
    out = [f"ld hl,({idxcell})"]
    d = disp.strip()
    if d and d not in ('0', '+0'):
        d = d.lstrip('+')
        out += ["ld a,l", f"add a,({d})&255", "ld l,a",
                "ld a,h", f"adc a,(({d})>>8)&255", "ld h,a"]
    return out


def expand(mn, ops, idxinfo):
    """Return list of i8080 asm lines (no label/comment) for a Z80-only instruction,
    or None if not Z80-only (pass through)."""
    # --- JR / DJNZ ---
    if mn == 'jr':
        if len(ops) == 1:
            return [f"jp {ops[0]}"]
        cc = ops[0].lower()
        return [f"jp {cc},{ops[1]}"]
    if mn == 'djnz':
        return ["dec b", f"jp nz,{ops[0]}"]
    # --- EXX / EX AF,AF' ---
    # exx approach-B: a PRE-PASS (_classify_exx) retags provably paired-temporary
    # exx into exxo (opening) / exxc (closing) -> a 3-byte push/pop instead of the
    # 33-byte per-pair RAM swap. Order is mirrored (LIFO): push bc/de/hl ... pop
    # hl/de/bc, so the two OTHER pairs and all flags are preserved. Only the main
    # bank is touched; the z80_*_alt cells are NOT written (safe iff the alt-bank
    # residue is dead - guaranteed by the classifier's rules, see _classify_exx).
    if mn == 'exxo':
        return ["push bc", "push de", "push hl"]
    if mn == 'exxc':
        return ["pop hl", "pop de", "pop bc"]
    if mn == 'exx':
        return _exx()
    if mn == 'ex' and ops and ops[0].lower() == 'af' and ops[1].replace(' ', '').lower() == "af'":
        return _exaf()
    # --- IX/IY (detected via idxinfo: (cell, regtoken/None)) ---
    if idxinfo is not None:
        return _ixiy_compact(mn, ops, idxinfo) if _IX_COMPACT else _ixiy(mn, ops, idxinfo)
    # --- Z80-only LD A,I / LD A,R (ED 57 / ED 5F) ---
    # The i8080 has no I/R register AND no ED prefix: passed through verbatim, the
    # ED byte is an UNDOCUMENTED 3-byte CALL on the 8080, so `ld a,i` (ED 57) plus
    # the following opcode byte become `CALL <next>57h` -> a wild jump into random
    # code (here momiaDecide7's ld a,i CALLed into lanzaCuchillo, corrupting MapaRAM
    # with 0x14 solid cells). KV uses `ld a,i` once (momiaDecide7, "valor aleatorio"):
    # on the MSX the BIOS runs IM 1 and never writes I, so I stays 0 (Z80 reset) ->
    # the "random" read is always 0. Emit A=0 via MVI A (LD A,n leaves flags intact,
    # matching Z80 ld a,i which preserves CY - the very next op is RRA and needs it).
    if mn == 'ld' and len(ops) == 2 and ops[0].lower() == 'a' \
            and ops[1].lower() in ('i', 'r'):
        return [f"ld a, 0\t; Z80 'ld a,{ops[1].lower()}' -> I/R=0 on MSX "
                "(no I/R reg on i8080; MVI preserves flags)"]
    # --- Z80-only 16-bit memory loads: ld bc/de,(nn) and ld (nn),bc/de ---
    # (i8080 has LHLD/SHLD for HL only; ED 4B/5B/43/53 are Z80-only opcodes that
    #  would execute as garbage on the vanilla i8080 Vector -> wrong BC/DE.)
    if mn == 'ld':
        r = _ld16(ops)
        if r is not None:
            return r
        # ld (nn),sp / ld sp,(nn): ED 73 / ED 7B — Z80-only. Через HL (HL сохраняется через
        # стек; SP корректируется на 2 за push). Флаги портятся (Z80 их не трогает) — в KL
        # оба сайта в print_sprite, где следом ex de,hl / ret: флаги не читаются.
        if len(ops) == 2 and ops[1].strip().lower() == 'sp' and ops[0].strip().startswith('('):
            nn = ops[0].strip()[1:-1]
            return ["push hl", "ld hl,2", "add hl,sp", f"ld ({nn}),hl", "pop hl"]
        if len(ops) == 2 and ops[0].strip().lower() == 'sp' and ops[1].strip().startswith('('):
            nn = ops[1].strip()[1:-1]
            return [f"ld hl,({nn})", "ld sp,hl"]
        return None
    # --- CB: bit/res/set/shifts on registers ---
    if mn == 'bit':
        return _bit(ops[0], ops[1])
    if mn in ('res', 'set'):
        return _resset(mn, ops[0], ops[1])
    if mn in ('sla', 'sra', 'srl', 'sll', 'rl', 'rr', 'rlc', 'rrc'):
        return _shift(mn, ops[0])
    # --- ED block / 16-bit / misc ---
    if mn == 'ldir':
        return _ldir(+1)
    if mn == 'lddr':
        return _ldir(-1)
    if mn == 'ldi':
        return _ldi(+1)
    if mn == 'ldd':
        return _ldi(-1)
    if mn in ('adc', 'sbc') and len(ops) == 2 and ops[0].lower() == 'hl':
        return _adcsbc_hl(mn, ops[1])
    if mn == 'neg':
        return ["cpl", "inc a"]
    if mn == 'im':
        # i8080 has no IM; but the MSX C-BIOS interrupt dispatch requires IM 1.
        # Pass the real Z80 opcode through for openMSX(Z80) validation. On the
        # vanilla Vector-06C target the interrupt setup is replaced (RST 7), so
        # this line is stripped/adapted there.
        return [f"im {ops[0]}\t; keep for MSX C-BIOS (IM 1); replace on Vector"]
    if mn in ('reti', 'retn'):
        return ["ret"]
    if mn == 'out' and ops and ops[0].lower() == '(c)':
        return _out_c(ops[1])
    if mn == 'in' and len(ops) == 2 and ops[1].lower() == '(c)':
        return _in_c(ops[0])
    return None


def _ld16(ops):
    """Translate Z80-only 16-bit memory loads to i8080 via HL (preserving HL and
    flags). Returns None for i8080-native ld forms (pass through unchanged)."""
    if len(ops) != 2:
        return None
    dst, src = ops[0].strip(), ops[1].strip()
    dl, sl = dst.lower(), src.lower()
    mem = lambda t: t.startswith('(') and t.endswith(')')
    # ld bc,(nn) / ld de,(nn)  -> LHLD then move to rp
    if dl in ('bc', 'de') and mem(src):
        nn = src[1:-1].strip()
        hi, lo = dl[0], dl[1]
        return ["push hl", f"ld hl,({nn})", f"ld {hi},h", f"ld {lo},l", "pop hl"]
    # ld (nn),bc / ld (nn),de  -> move rp to HL then SHLD
    if sl in ('bc', 'de') and mem(dst):
        nn = dst[1:-1].strip()
        hi, lo = sl[0], sl[1]
        return ["push hl", f"ld h,{hi}", f"ld l,{lo}", f"ld ({nn}),hl", "pop hl"]
    return None


def _exx():
    out = []
    for rp, cell in (('bc', 'z80_bc_alt'), ('de', 'z80_de_alt'), ('hl', 'z80_hl_alt')):
        out += _swap_rp(rp, cell)
    return out


def _swap_rp(rp, cell):
    # Compact stack-based swap of a register pair with its alt RAM cell.
    # Semantics identical to exx per-pair: rp <-> (cell). No flag/A effects
    # (push/pop/ld/mov/xthl leave flags untouched). Only HL is used as scratch
    # and is always restored, so the OTHER two pairs are preserved.
    if rp == 'hl':
        # HL <-> (cell): 5 insns / 9 bytes (was 6 / 18).
        return ["push hl", f"ld hl,({cell})", "ex (sp),hl",
                f"ld ({cell}),hl", "pop hl"]
    hi, lo = rp[0], rp[1]
    # BC/DE <-> (cell): 8 insns / 12 bytes (was 10 / 22), HL preserved.
    return ["push hl", f"ld hl,({cell})", f"push {rp}",
            f"ld {lo},l", f"ld {hi},h", "pop hl",
            f"ld ({cell}),hl", "pop hl"]


def _exaf():
    return ["ld (il_tmp2),hl", "push af", "pop hl", "ld (il_tmp),hl",
            "ld hl,(z80_af_alt)", "push hl", "pop af", "ld hl,(il_tmp)",
            "ld (z80_af_alt),hl", "ld hl,(il_tmp2)"]


# ---------------------------------------------------------------------------
# EXX approach-B: paired-temporary exx -> push/pop (6 bytes/pair vs 66).
# ---------------------------------------------------------------------------
# The default exx expansion (_swap_rp x3) swaps each register pair with a RAM
# cell (z80_bc/de/hl_alt) = 33 bytes/exx. When a matched `exx ... exx` pair is
# used purely as "give me scratch registers" (the alt bank is FRESH scratch, not
# carrying data between pairs), the whole swap is unnecessary: the opening exx
# can just `push bc/de/hl` (preserve the main bank on the stack) and the closing
# exx `pop hl/de/bc` (restore it) = 3 bytes each, 6 bytes/pair vs 66.
#
# A pre-pass classifies the ROLE of every standalone `exx` and retags the safe
# ones to `exxo`/`exxc`; the rest stay `exx` (RAM swap). A pair (open i, its very
# next exx j) is a SAFE temporary iff ALL hold in the body between them:
#   1. no label (a jump-in target) and no branch leaving the pair
#      (jr/jp/djnz/ret/rst/reti/retn). call/ldir/lddr are stack-balanced and act
#      as a write-barrier (a subroutine defines its outputs) -> allowed.
#   2. no alt register b/c/d/e/h/l is READ before it is WRITTEN. A read-before-
#      write means the alt reg carries a value produced by a PRIOR exx pair =
#      long-lived data (e.g. an alt-bank loop counter, or a caller-passed alt
#      pointer) -> the swap must stay so the value is read from the RAM cell.
#   3. the body's observable effect is a MEMORY write (its purpose is to poke
#      memory using the alt bank as scratch). If the sole effect is loading an
#      alt REGISTER (e.g. setVDPWrite: `ld c,a` sets the VDP data port that a
#      later `exx / out(c),a / exx` consumes), push/pop would NOT reproduce that
#      alt-register residue -> exclude, keep the swap.
# Rules 1-3 are LOCAL and sound for the observable memory effect; the z80 logic
# gate is the backstop for any cross-routine alt residue.

_R8 = ('b', 'c', 'd', 'e', 'h', 'l')
_RP = {'bc': ('b', 'c'), 'de': ('d', 'e'), 'hl': ('h', 'l')}


def _tok_regs(tok):
    """Return (regset, is_mem) for an operand token: which of b/c/d/e/h/l it
    references and whether it is a memory reference via a register pair."""
    t = tok.strip().lower()
    if t in _RP:
        return set(_RP[t]), False
    if t in _R8:
        return {t}, False
    if t.startswith('(') and t.endswith(')'):
        inner = t[1:-1].strip().lower()
        if inner == 'hl':
            return {'h', 'l'}, True
        if inner in ('bc', 'de'):
            return set(_RP[inner]), True
        return set(), True          # (nn) immediate address
    return set(), False


def _exx_regs(mn, ops):
    """Effect of one instruction on the tracked 8-bit regs.
    Returns (reads, writes, mem_write, is_branch, ok). ok=False => unparseable
    (caller must treat the pair as unsafe)."""
    if mn in ('jr', 'jp', 'djnz', 'ret', 'rst', 'reti', 'retn'):
        return set(), set(), False, True, True
    if mn in ('call', 'ldir', 'lddr'):
        # stack-balanced; treat as a write-barrier for all tracked regs so a
        # subsequent (hl)/reg read is not flagged read-before-write.
        return set(), set(_R8), mn in ('ldir', 'lddr'), False, True
    if mn == 'ld' and len(ops) == 2:
        reads, _ = _tok_regs(ops[1])
        writes, mem_write = set(), False
        dl = ops[0].strip().lower()
        if dl in _RP:
            writes |= set(_RP[dl])
        elif dl in _R8:
            writes.add(dl)
        elif dl.startswith('(') and dl.endswith(')'):
            r, _m = _tok_regs(ops[0])
            reads |= r               # addr regs are READ to form the pointer
            mem_write = True
        return reads, writes, mem_write, False, True
    if mn in ('inc', 'dec') and len(ops) == 1:
        t = ops[0].strip().lower()
        if t in _RP:
            return set(_RP[t]), set(_RP[t]), False, False, True
        if t in _R8:
            return {t}, {t}, False, False, True
        if t == '(hl)':
            return {'h', 'l'}, set(), True, False, True
        return set(), set(), False, False, False
    if mn == 'push' and ops:
        t = ops[0].strip().lower()
        if t in _RP:
            return set(_RP[t]), set(), False, False, True
        if t == 'af':
            return set(), set(), False, False, True
        return set(), set(), False, False, False
    if mn == 'pop' and ops:
        t = ops[0].strip().lower()
        if t in _RP:
            return set(), set(_RP[t]), False, False, True
        if t == 'af':
            return set(), set(), False, False, True
        return set(), set(), False, False, False
    if mn in ('add', 'adc') and len(ops) == 2 and ops[0].strip().lower() == 'hl':
        r, _ = _tok_regs(ops[1])
        return {'h', 'l'} | r, {'h', 'l'}, False, False, True
    if mn == 'ex' and len(ops) == 2:
        s = set()
        for t in (ops[0].strip().lower(), ops[1].strip().lower()):
            if t in _RP:
                s |= set(_RP[t])
            elif t != '(sp)':
                return set(), set(), False, False, False
        return s, s, False, False, True
    if mn in ('add', 'adc', 'sub', 'sbc', 'and', 'or', 'xor', 'cp') and ops:
        r, _ = _tok_regs(ops[-1])    # `op a,x` or `op x`; A is untracked
        return r, set(), False, False, True
    if mn in ('rra', 'rla', 'rrca', 'rlca', 'cpl', 'scf', 'ccf', 'daa', 'neg',
              'nop', 'ei', 'di', 'halt') and not ops:
        return set(), set(), False, False, True
    if mn in ('add', 'adc', 'sub', 'sbc', 'and', 'or', 'xor', 'cp') and not ops:
        return set(), set(), False, False, True
    return set(), set(), False, False, False   # unknown -> unsafe


def _classify_exx(src):
    """Retag provably paired-temporary `exx` into `exxo`/`exxc`. Returns
    (new_src, n_pairs_converted). Long-lived exx are left untouched."""
    lines = src.split('\n')
    exx_idx = []
    for i, l in enumerate(lines):
        lbl, body, _ = split_line(l)
        if not lbl and body.strip().lower() == 'exx':
            exx_idx.append(i)
    roles = {}
    conv = 0
    k = 0
    while k + 1 < len(exx_idx):
        oi, ci = exx_idx[k], exx_idx[k + 1]
        written, membw, safe = set(), False, True
        for j in range(oi + 1, ci):
            lbl, body, _ = split_line(lines[j])
            if lbl:                      # label = possible jump-in target
                safe = False
                break
            if not body:
                continue
            mn, ops = operands(body)
            reads, writes, mw, branch, ok = _exx_regs(mn, ops)
            if not ok or branch:
                safe = False
                break
            if reads - written:          # read-before-write of an alt reg
                safe = False
                break
            written |= writes
            membw = membw or mw
        if safe and membw:
            roles[oi], roles[ci] = 'exxo', 'exxc'
            conv += 1
            k += 2
        else:
            k += 1
    for i, newmn in roles.items():
        line = lines[i]
        indent = line[:len(line) - len(line.lstrip())]
        _, _, cmt = split_line(line)
        lines[i] = indent + newmn + (('\t' + cmt) if cmt else '')
    return '\n'.join(lines), conv


def _bit(n, reg):
    mask = f"1<<{n}"
    if reg == 'a':
        return ["ld (il_a),a", f"and {mask}", "ld a,(il_a)"]
    if reg in ('(hl)',):
        return ["ld (il_a),a", "ld a,(hl)", f"and {mask}", "ld a,(il_a)"]
    return ["ld (il_a),a", f"ld a,{reg}", f"and {mask}", "ld a,(il_a)"]


def _resset(op, n, reg):
    # Sjasm computes the mask constant. RES/SET must preserve ALL flags (Z80 does).
    alu = f"and (~(1<<{n}))&255" if op == 'res' else f"or 1<<{n}"
    if reg == 'a':
        return ["push af", alu, "ld (il_a),a", "pop af", "ld a,(il_a)"]
    if reg == '(hl)':
        return ["push af", "ld a,(hl)", alu, "ld (hl),a", "pop af"]
    return ["push af", f"ld a,{reg}", alu, f"ld {reg},a", "pop af"]


def _shift_body(op):
    if op == 'sla':
        return ["add a,a"]
    if op == 'sll':
        return ["add a,a", "or 1"]
    if op == 'rlc':
        return ["rlca"]
    if op == 'rrc':
        return ["rrca"]
    if op == 'rl':
        return ["rla"]
    if op == 'rr':
        return ["rra"]
    if op == 'srl':
        return ["or a", "rra"]
    if op == 'sra':
        return ["ld (il_tmp),a", "add a,a", "ld a,(il_tmp)", "rra"]
    return [f"; UNHANDLED SHIFT {op}"]


def _shift(op, reg):
    body = _shift_body(op)
    if reg == 'a':
        return body
    if reg == '(hl)':
        return ["ld (il_a),a", "ld a,(hl)", *body, "ld (hl),a", "ld a,(il_a)"]
    return ["ld (il_a),a", f"ld a,{reg}", *body, f"ld {reg},a", "ld a,(il_a)"]


_LD_UID = [0]


def _uniq(stem):
    _LD_UID[0] += 1
    return f".il_{stem}{_LD_UID[0]}"


def _ldir(step):
    # Deflation: fold the 8-instruction inline copy loop into a shared subroutine
    # (vec_ldir_fwd for ldir/+1, vec_ldir_bwd for lddr/-1). Params already in the
    # right regs (HL=src, DE=dst, BC=count). Semantics identical: copies BC bytes,
    # leaves BC=0 and HL/DE past the range, Z set (from the final `or c`). call/ret
    # touch only the stack + PC, so flags/regs on return match the inline form.
    return ["call vec_ldir_fwd" if step > 0 else "call vec_ldir_bwd"]


def _ldi(step):
    s = "inc" if step > 0 else "dec"
    return ["ld a,(hl)", "ld (de),a", f"{s} hl", f"{s} de", "dec bc"]


# Shared ldir/lddr copy loops (deflation): each Z80 ldir/lddr becomes a single
# `call` to one of these instead of an 8-instruction inline expansion. Injected in
# a code region just before the MAP block, so they exist for BOTH targets (the
# `call` is emitted target-independently by _ldir). All-i8080 (passes through
# translate untouched). vec_ldir_fwd = ldir (inc), vec_ldir_bwd = lddr (dec).
#
# STACK-SAFE: a plain call/ret would leave the return address on the stack DURING
# the copy. KV's boot variable-fill (`ld (hl),0` + ldir over GameStatus..+0x6FF)
# has the stack sitting INSIDE that range on MSX (SP=stackTop 0xE700, fill hits
# 0xE6FE/FF = the pushed return slot) -> the fill zeroes the return address ->
# ret to 0x0000 -> reboot loop. The inline loop never touched the stack, so it was
# immune. Fix: at entry stash the return address in il_tmp2 (a scratch cell OUTSIDE
# any fill region on both targets: MSX 0xF00E, Vector 0xDFCE) and pop it off the
# stack, so the copy can never clobber it; at exit restore it and jump back while
# leaving HL/DE/BC exactly as ldir does (HL/DE past range, BC=0). il_tmp2 is saved/
# restored by BOTH ISRs (vec_isr + tickMainISR), so an interrupt mid-copy (or a
# nested ldir inside tickMain) preserves the stashed return address.
_LDIR_LOOP_FWD = ("\tld\ta,(hl)\n\tld\t(de),a\n\tinc\thl\n\tinc\tde\n"
                  "\tdec\tbc\n\tld\ta,b\n\tor\tc\n\tjp\tnz,{lp}\n")
_LDIR_LOOP_BWD = ("\tld\ta,(hl)\n\tld\t(de),a\n\tdec\thl\n\tdec\tde\n"
                  "\tdec\tbc\n\tld\ta,b\n\tor\tc\n\tjp\tnz,{lp}\n")


def _ldir_helper(name, loop_tmpl):
    lp = name + "_lp"
    return (
        f"{name}:\n"
        "\tex\t(sp),hl\n\tld\t(il_tmp2),hl\n\tpop\thl\n"   # stash ret addr, restore HL=src
        f"{lp}:\n" + loop_tmpl.format(lp=lp) +
        "\tpush\thl\n\tld\thl,(il_tmp2)\n\tex\t(sp),hl\n\tret\n")  # restore ret, keep final HL


_LDIR_HELPERS = (
    "\n; ===== ldir/lddr deflation: shared stack-safe copy loops (call, not inline) =====\n"
    "; Copy BC bytes (HL)->(DE); on return BC=0, HL/DE past the range. Return addr is\n"
    "; kept in il_tmp2 (not on the stack) so a fill whose dest overlaps SP is safe.\n"
    + _ldir_helper("vec_ldir_fwd", _LDIR_LOOP_FWD)
    + _ldir_helper("vec_ldir_bwd", _LDIR_LOOP_BWD)
)


def _adcsbc_hl(op, rp):
    # Value and carry come from the byte-wise adc/sbc chain. Z must reflect the FULL
    # 16-bit result (Z80 semantics) - KV branches on it (e.g. `sbc hl,bc / ret nz`
    # trap X-coincidence check). A naive chain leaves Z = (high byte == 0), so a
    # difference of 1..255 would read as "equal". Fix-up: when the high byte is 0,
    # recompute Z from L; restore carry via SCF (STC touches ONLY CY on 8080).
    # Clobbers A (Z80 clobbers only F); S reflects L's bit7 in the fix-up path -
    # fine for KV (only z/nz/c/nc used after 16-bit adc/sbc).
    rp = rp.lower()
    hi, lo = rp[0], rp[1]
    a = 'adc' if op == 'adc' else 'sbc'
    done = _uniq('AS16')
    cset = _uniq('AS16C')
    return ["ld a,l", f"{a} a,{lo}", "ld l,a",
            "ld a,h", f"{a} a,{hi}", "ld h,a",
            f"jp nz,{done}",                 # high!=0: Z=0 and C already correct
            f"jp c,{cset}",
            "ld a,l", "or a",                # C was 0: Z=(L==0), C stays 0
            f"jp {done}",
            f"{cset}:", "ld a,l", "or a", "scf",   # C was 1: fix Z, then restore C
            f"{done}:"]


# MSX fixed I/O ports for OUT (C),r / IN r,(C): the only out(c) sites are PSG (port
# A0/A1 via C). On MSX the value of C at these sites is the PSG register port. We
# keep C-as-port via a self-modified OUT (rare, 3 sites).
def _guniq(stem):
    _LD_UID[0] += 1
    return f"IL_{stem}{_LD_UID[0]}"          # GLOBAL label (self-mod target)


def _out_c(reg):
    pre = [] if reg == 'a' else [f"ld a,{reg}"]
    # i8080 has no variable-port OUT. The code lives in ROM (cartridge), so we
    # cannot self-modify it. Build an `out (port),a ; ret` stub in RAM (il_outstub)
    # and CALL it. Interrupt-safe: the ISR wrapper preserves il_a, and out(c) is
    # not used on any nested-interrupt path (updateSound uses BIOS PSG calls).
    return pre + ["ld (il_a),a", "ld a,c", "ld (il_outstub+1),a",
                  "ld a,0D3h", "ld (il_outstub),a",
                  "ld a,0C9h", "ld (il_outstub+2),a",
                  "ld a,(il_a)", "call il_outstub"]


def _in_c(reg):
    lbl = _guniq("INC")
    seq = ["ld a,c", f"ld ({lbl}+1),a", f"{lbl}:", "in a,(0)"]
    if reg != 'a':
        seq += [f"ld {reg},a"]
    return seq


def _ixiy(mn, ops, idxinfo):
    cell, _ = idxinfo
    full = (mn + ' ' + ','.join(ops)).lower()

    # push/pop ix
    if mn == 'push':
        return ["ld (il_tmp),hl", f"ld hl,({cell})", "push hl", "ld hl,(il_tmp)"]
    if mn == 'pop':
        return ["ld (il_tmp),hl", "pop hl", f"ld ({cell}),hl", "ld hl,(il_tmp)"]
    # ld ix,nn
    if mn == 'ld' and ops[0].lower() in ('ix', 'iy'):
        nn = ops[1]
        return ["ld (il_tmp),hl", f"ld hl,{nn}", f"ld ({cell}),hl", "ld hl,(il_tmp)"]
    # ld ix,(nn) / ld (nn),ix
    if mn == 'ld' and ops[0].lower() in ('ix', 'iy') and ops[1].startswith('('):
        nn = ops[1][1:-1]
        return [f"ld hl,({nn})", f"ld ({cell}),hl"]
    if mn == 'ld' and ops[1].lower() in ('ix', 'iy') and ops[0].startswith('('):
        nn = ops[0][1:-1]
        return ["ld (il_tmp),hl", f"ld hl,({cell})", f"ld ({nn}),hl", "ld hl,(il_tmp)"]
    # inc/dec ix
    if mn in ('inc', 'dec') and ops[0].lower() in ('ix', 'iy'):
        s = 'inc' if mn == 'inc' else 'dec'
        return ["ld (il_tmp),hl", f"ld hl,({cell})", f"{s} hl", f"ld ({cell}),hl", "ld hl,(il_tmp)"]
    # add ix,rp
    if mn == 'add' and ops[0].lower() in ('ix', 'iy'):
        rp = ops[1].lower()
        rp = cell if rp in ('ix', 'iy') else rp
        if rp == cell:
            return ["ld (il_tmp),hl", f"ld hl,({cell})", "add hl,hl", f"ld ({cell}),hl", "ld hl,(il_tmp)"]
        return ["ld (il_tmp),hl", f"ld hl,({cell})", f"add hl,{rp}", f"ld ({cell}),hl", "ld hl,(il_tmp)"]
    # jp (ix)
    if mn == 'jp' and ops[0].lower() in ('(ix)', '(iy)'):
        return [f"ld hl,({cell})", "jp (hl)"]
    # (ix+d) memory operand forms: parse which op is the memory ref
    midx = None
    for i, o in enumerate(ops):
        if '(ix' in o.lower() or '(iy' in o.lower():
            midx = i
    if midx is None:
        return [f"; UNHANDLED IX/IY {full}"]
    inner = ops[midx]
    disp = re.sub(r'^\((ix|iy)', '', inner, flags=re.I)[:-1]   # strip "(ix" and ")"
    addr = _idx_addr(cell, disp)

    if mn == 'ld':
        if midx == 1:                      # ld r,(ix+d)
            r = ops[0].lower()
            if r == 'a':
                # ld a,(ix+d): Z80 leaves FLAGS unchanged, but the addr build's
                # add/adc clobber them. Save incoming A+F, load the target, then
                # restore F while keeping the loaded value (stashed in il_a).
                # (Was: no push af -> flags leaked, breaking e.g.
                #  `dec (ix+TIMER); ld a,(ix+TIMER); jr z,...` -> jr never taken.)
                return (["push af", "ld (il_tmp2),hl"] + addr
                        + ["ld a,(hl)", "ld hl,(il_tmp2)", "ld (il_a),a",
                           "pop af", "ld a,(il_a)"])
            seq = ["push af", "ld (il_tmp2),hl"] + addr + ["ld a,(hl)",
                   "ld hl,(il_tmp2)", f"ld {r},a", "pop af"]
            return seq
        else:                              # ld (ix+d),r  or  ld (ix+d),n
            src = ops[1]
            s = src.lower()
            if s == 'a':
                # Source is A, but the addr build clobbers A (it computes idx+disp via
                # ld a,l/add/ld a,h/adc). `ld a,a` cannot recover it -> stash A in il_a
                # first, build addr, then write the stashed value. (Was: wrote the
                # clobbered A = high byte of the pointer -> corrupted every actor field.)
                return (["push af", "ld (il_a),a", "ld (il_tmp2),hl"] + addr
                        + ["ld a,(il_a)", "ld (hl),a", "ld hl,(il_tmp2)", "pop af"])
            seq = ["push af", "ld (il_tmp2),hl"] + addr
            if s in ('b', 'c', 'd', 'e'):
                seq += [f"ld a,{src}", "ld (hl),a"]
            elif s in ('h', 'l'):
                # h/l were clobbered by addr build; read from saved il_tmp2
                byte = "(il_tmp2)" if s == 'l' else "(il_tmp2+1)"
                seq += [f"ld a,{byte}", "ld (hl),a"]
            else:                          # immediate n
                seq += [f"ld a,{src}", "ld (hl),a"]
            seq += ["ld hl,(il_tmp2)", "pop af"]
            return seq
    if mn in ('inc', 'dec'):
        # inc/dec (ix+d) sets flags from the result -> preserve A via il_a (ld doesn't
        # touch flags), NOT push/pop af (which would restore old flags).
        s = 'inc' if mn == 'inc' else 'dec'
        return ["ld (il_a),a", "ld (il_tmp2),hl"] + addr + [f"{s} (hl)",
                "ld hl,(il_tmp2)", "ld a,(il_a)"]
    if mn == 'bit':
        # BIT n,(ix+d): set Z = (bit==0). Consumed flag is Z only. Preserve A.
        n = ops[0]
        return ["ld (il_a),a", "ld (il_tmp2),hl"] + addr + ["ld a,(hl)",
                f"and 1<<{n}", "ld hl,(il_tmp2)", "ld a,(il_a)"]
    if mn in ('res', 'set'):
        n = ops[0]
        alu = f"and (~(1<<{n}))&255" if mn == 'res' else f"or 1<<{n}"
        return ["push af", "ld (il_tmp2),hl"] + addr + ["ld a,(hl)", alu, "ld (hl),a",
                "ld hl,(il_tmp2)", "pop af"]
    if mn in ('add', 'adc', 'sub', 'sbc', 'and', 'xor', 'or', 'cp'):
        # alu a,(ix+d): A is the operand, but the addr build clobbers A -> save A in
        # il_a, build addr, restore A, then operate on (hl). Flags = the alu result.
        alu = {'add': 'add a,', 'adc': 'adc a,', 'sub': 'sub ', 'sbc': 'sbc a,',
               'and': 'and ', 'xor': 'xor ', 'or': 'or ', 'cp': 'cp '}[mn]
        return ["ld (il_a),a", "ld (il_tmp2),hl"] + addr + ["ld a,(il_a)",
                f"{alu}(hl)", "ld hl,(il_tmp2)"]
    return [f"; UNHANDLED IX/IY {full}"]


# ---------------------------------------------------------------------------
# КОМПАКТНАЯ ЭМУЛЯЦИЯ IX/IY (KL): обращение (ix+d) = `call il_<ix|iy>_<форма>` + inline-байт(ы).
# Хелпер читает байты за адресом возврата и возвращается за них. Контракты регистров/флагов
# те же, что у встроенного разворачивания (_ixiy). Эмитятся только использованные хелперы.
# KL_IX_INLINE=1 -> старое встроенное разворачивание.
# ---------------------------------------------------------------------------
_IX_COMPACT = not __import__('os').environ.get('KL_IX_INLINE')
# ---- раскладка нижнего региона (цель vec) ----
VEC_VARS = 0x7A80          # переменные clear_start..clear_end (0x568)
VEC_CODE_TOP = 0x7A00      # потолок кода: ниже 128 байт стека под переменными (замер: главный стек 38 Б,
                           # временный `ld sp,seed_1` 72 Б, ISR добавляет 10 -> максимум 82)
VEC_HOLE_BYTES = int(__import__('os').environ.get('KL_HOLE_BYTES', '-1'))   # сколько байт увозить в дыры (-1 = сколько влезет)
VEC_HOLE_SKIP = tuple(x for x in __import__('os').environ.get('KL_HOLE_SKIP', '').split(',') if x)
VEC_HOLE_PLANES = [0xA000, 0xC000]   # плоскости, чьи строки вне картинки отданы под код (1 = буфер, 2 = селектор HUD)
VEC_UPPER = int(__import__('os').environ.get('KL_VEC_UPPER', '0xE000'), 16)   # начало верхнего региона (плоскость 3)
# Разрез массива спрайтов: с этой метки хвост уезжает в верхний регион. Ниже сколько влезет —
# граница подобрана замером (см. ASSERT нижнего региона: свободно должно оставаться ~0), сдвигать
# при любом изменении размера кода. Спрайты адресуются через sprite_tbl, разрез произволен.
VEC_SPLIT = __import__('os').environ.get('KL_VEC_SPLIT', 'spr_077')

_ix_used = set()
# горячие процедуры (по профилю): встроенное разворачивание ради темпа, остальные — компактно
_IX_INLINE_FUNCS = {'print_sprite', 'wipe_next_object', 'is_near_to', 'is_object_not_ignored', 'adj_2_4_hflip', 'chk_plyr_spec_near_arch', 'upd_2_4', 'add_dXYZ', 'render_dynamic_objects',
                    # добавлено раундом speed2 по профилю МЕСТ вызова (tools/ilhot_vec.js): верх списка
                    # исполнений call il_* на итерацию в комнатах 39 и 163
                    'no_delay', 'left_right_rotational', 'handle_forward', 'animate_human_legs', 'audio_B467',
                    'calc_ghost_sprite', 'set_ghost_hflip', 'clr_ghost_hflip', 'next_fg_obj_sprite',
                    'upd_182_183'}
_cur_func = ['']

_ix_form_cnt = {}
# Редкую форму дешевле развернуть на месте, чем держать хелпер: тело 27..46 байт против
# 4 байт на `call+db` плюс 13..18 на развёртку. Порог выбран замером (низ региона):
#   N<=0: 0x7514 (все компактные)  N<=1: 0x74BB  N<=2: 0x7439  N<=3: 0x7597  N<=4: 0x7597
# То есть выгодно разворачивать формы с 1-2 обращениями; с 3 и более уже дороже.
# Побочный эффект — темп: развёртка быстрее call+хелпер.
_IX_RARE = int(__import__('os').environ.get('KL_IX_RARE', '2'))


def _ix_form(mn, ops, idxinfo):
    """(px, form) для обращения к (ix+d)/(iy+d) или None, если форма не компактная."""
    cell, _ = idxinfo
    px = 'ix' if cell == 'z80_ix' else 'iy'
    if mn in ('push', 'pop') or (mn == 'ld' and ops[0].lower() in ('ix', 'iy')) \
            or (mn == 'ld' and len(ops) == 2 and ops[1].lower() in ('ix', 'iy')) \
            or (mn in ('inc', 'dec', 'add') and ops[0].lower() in ('ix', 'iy')) \
            or (mn == 'jp'):
        return None
    midx = None
    for i, o in enumerate(ops):
        if '(ix' in o.lower() or '(iy' in o.lower():
            midx = i
    if midx is None:
        return None
    if mn == 'ld':
        if midx == 1:
            r = ops[0].lower()
            return (px, 'ld' + r) if r in ('a', 'b', 'c', 'd', 'e', 'h', 'l') else None
        sl = ops[1].strip().lower()
        return (px, 'st' + sl) if sl in ('a', 'b', 'c', 'd', 'e', 'h', 'l') else (px, 'stn')
    if mn in ('inc', 'dec', 'bit', 'res', 'set', 'add', 'adc', 'sub', 'sbc', 'and', 'xor', 'or', 'cp'):
        return (px, mn)
    return None


def count_ix_forms(src):
    """Предпроход: сколько раз встречается каждая компактная форма (ix+d)/(iy+d)."""
    _ix_form_cnt.clear()
    cur = ''
    for line in src.split('\n'):
        label, body, _c = split_line(line)
        if label and not label.startswith(('loc_', '.')):
            cur = label.rstrip(':')
        if not body or cur in _IX_INLINE_FUNCS:
            continue
        mn, ops = operands(body)
        idx = has_idx(body)
        if not idx:
            continue
        f = _ix_form(mn, ops, idx)
        if f:
            _ix_form_cnt[f] = _ix_form_cnt.get(f, 0) + 1


def _ixiy_compact(mn, ops, idxinfo):
    cell, _ = idxinfo
    if _cur_func[0] in _IX_INLINE_FUNCS:
        return _ixiy(mn, ops, idxinfo)
    f = _ix_form(mn, ops, idxinfo)
    if f and _ix_form_cnt.get(f, 99) <= _IX_RARE:
        return _ixiy(mn, ops, idxinfo)
    px = 'ix' if cell == 'z80_ix' else 'iy'
    full = (mn + ' ' + ','.join(ops)).lower()
    # формы, которые остаются встроенными (регистр IX целиком)
    if mn in ('push', 'pop') or (mn == 'ld' and ops[0].lower() in ('ix', 'iy')) \
            or (mn == 'ld' and len(ops) == 2 and ops[1].lower() in ('ix', 'iy')) \
            or (mn in ('inc', 'dec', 'add') and ops[0].lower() in ('ix', 'iy')) \
            or (mn == 'jp'):
        return _ixiy(mn, ops, idxinfo)
    midx = None
    for i, o in enumerate(ops):
        if '(ix' in o.lower() or '(iy' in o.lower():
            midx = i
    if midx is None:
        return _ixiy(mn, ops, idxinfo)
    disp = re.sub(r'^\((ix|iy)', '', ops[midx], flags=re.I)[:-1].strip().lstrip('+') or '0'
    def use(form, *extra):
        name = f"il_{px}_{form}"
        _ix_used.add((px, form))
        return [f"call {name}", "db " + ", ".join([disp] + list(extra))]
    if mn == 'ld':
        if midx == 1:
            r = ops[0].lower()
            if r in ('a', 'b', 'c', 'd', 'e', 'h', 'l'):
                return use('ld' + r)
        else:
            src = ops[1].strip(); sl = src.lower()
            if sl in ('a', 'b', 'c', 'd', 'e', 'h', 'l'):
                return use('st' + sl)
            return use('stn', src)
    if mn in ('inc', 'dec'):
        return use(mn)
    if mn == 'bit':
        return use('bit', f"1<<{ops[0]}")
    if mn == 'res':
        return use('res', f"(~(1<<{ops[0]}))&255")
    if mn == 'set':
        return use('set', f"1<<{ops[0]}")
    if mn in ('add', 'adc', 'sub', 'sbc', 'and', 'xor', 'or', 'cp'):
        return use(mn)
    return _ixiy(mn, ops, idxinfo)

_ADDR = ["ld hl,({C})", "add a,l", "ld l,a", "ld a,h", "adc a,0", "ld h,a"]   # HL = cell + A
_PRO_AF = ["ld (il_tmp2),hl", "push af", "pop hl", "ld (il_f),hl", "pop hl", "ld a,(hl)", "inc hl", "push hl"]
_EPI_AF = ["ld hl,(il_f)", "push hl", "pop af", "ld hl,(il_tmp2)", "ret"]
_PRO_A  = ["ld (il_tmp2),hl", "ld (il_a),a", "pop hl", "ld a,(hl)", "inc hl", "push hl"]   # только A (флаги — результат)
_PRO_AF2 = ["ld (il_tmp2),hl", "push af", "pop hl", "ld (il_f),hl", "pop hl", "ld a,(hl)", "ld (il_tmp),a",
            "inc hl", "ld a,(hl)", "ld (il_tmp+1),a", "inc hl", "push hl", "ld a,(il_tmp)"]     # d + второй байт в il_tmp+1
_PRO_A2  = ["ld (il_tmp2),hl", "ld (il_a),a", "pop hl", "ld a,(hl)", "ld (il_tmp),a",
            "inc hl", "ld a,(hl)", "ld (il_tmp+1),a", "inc hl", "push hl", "ld a,(il_tmp)"]

def _ix_helper(px, form):
    C = 'z80_' + px
    A = [x.replace('{C}', C) for x in _ADDR]
    body = None
    if form == 'lda':
        body = _PRO_AF + A + ["ld a,(hl)", "ld (il_a),a", "ld hl,(il_f)", "push hl", "pop af", "ld a,(il_a)", "ld hl,(il_tmp2)", "ret"]
    elif form in ('ldb', 'ldc', 'ldd', 'lde'):
        body = _PRO_AF + A + [f"ld {form[2]},(hl)"] + _EPI_AF
    elif form == 'ldl':
        body = _PRO_AF + A + ["ld a,(hl)", "ld (il_tmp2),a"] + _EPI_AF
    elif form == 'ldh':
        body = _PRO_AF + A + ["ld a,(hl)", "ld (il_tmp2+1),a"] + _EPI_AF
    elif form == 'sta':
        body = _PRO_AF + A + ["ld a,(il_f+1)", "ld (hl),a"] + _EPI_AF
    elif form in ('stb', 'stc', 'std', 'ste'):
        body = _PRO_AF + A + [f"ld (hl),{form[2]}"] + _EPI_AF
    elif form == 'stl':
        body = _PRO_AF + A + ["ld a,(il_tmp2)", "ld (hl),a"] + _EPI_AF
    elif form == 'sth':
        body = _PRO_AF + A + ["ld a,(il_tmp2+1)", "ld (hl),a"] + _EPI_AF
    elif form == 'stn':
        body = _PRO_AF2 + A + ["ld a,(il_tmp+1)", "ld (hl),a"] + _EPI_AF
    elif form in ('inc', 'dec'):
        body = _PRO_A + A + [f"{form} (hl)", "ld a,(il_a)", "ld hl,(il_tmp2)", "ret"]
    elif form == 'bit':
        body = _PRO_A2 + A + ["ld a,(il_tmp+1)", "and (hl)", "ld a,(il_a)", "ld hl,(il_tmp2)", "ret"]
    elif form == 'res':
        body = _PRO_AF2 + A + ["ld a,(il_tmp+1)", "and (hl)", "ld (hl),a"] + _EPI_AF
    elif form == 'set':
        body = _PRO_AF2 + A + ["ld a,(il_tmp+1)", "or (hl)", "ld (hl),a"] + _EPI_AF
    elif form in ('add', 'adc', 'sub', 'sbc', 'and', 'xor', 'or', 'cp'):
        alu = {'add': 'add a,', 'adc': 'adc a,', 'sub': 'sub ', 'sbc': 'sbc a,', 'and': 'and ', 'xor': 'xor ', 'or': 'or ', 'cp': 'cp '}[form]
        body = _PRO_A + A + ["ld a,(il_a)", f"{alu}(hl)", "ld hl,(il_tmp2)", "ret"]
    assert body, form
    return f"il_{px}_{form}:\n" + "\n".join("\t" + x for x in body) + "\n"

def ix_helpers_text():
    if not _ix_used:
        return ""
    out = ["\n; ===== компактные IX/IY-хелперы (только использованные формы) ====="]
    for px, form in sorted(_ix_used):
        out.append(_ix_helper(px, form))
    return "\n".join(out) + "\n"

def has_idx(body):
    """Detect IX/IY usage; return (cell, None) or None."""
    b = body.lower()
    if re.search(r'\(ix', b) or re.search(r'\bix\b', b):
        return ('z80_ix', None)
    if re.search(r'\(iy', b) or re.search(r'\biy\b', b):
        return ('z80_iy', None)
    return None


def translate(src):
    count_ix_forms(src)
    out = []
    inserted_scratch = False
    for line in src.split('\n'):
        label, body, comment = split_line(line)
        if label and not label.startswith(('loc_', '.')):
            _cur_func[0] = label.rstrip(':')
        if not body:
            out.append(line)
            continue
        mn, ops = operands(body)
        idx = has_idx(body)
        seq = expand(mn, ops, idx)
        if seq is None:
            out.append(line)              # pass through (i8080 / directive)
            continue
        # emit: source label on its own line, then the i8080 sequence (tab-indented;
        # sub-labels at col0). Attach the source comment to the first emitted line.
        if label:
            out.append(label)
        emitted_comment = False
        for s in seq:
            is_label = s.endswith(':')
            ln = s if is_label else ('\t' + s)
            if not emitted_comment and comment:
                ln += '\t' + comment
                emitted_comment = True
            out.append(ln)
    return "\n".join(out)




def kl_pace(src):
    """Потолок темпа: итерация главного цикла не короче заданного времени (кадрами, с дробью).

    KLPACE — потолок в итерациях в секунду (по умолчанию 22.4 = максимум оригинала ZX
    по замеренным комнатам); KLPACE=off (или 0) — ограничитель не собирается вовсе.
    KLPACEMODE=a — только потолок (в тяжёлых комнатах ожидания нет, длительность не округляется);
    KLPACEMODE=b — длительность итерации всегда целое число кадров (минимум = потолок).
    Возвращает (src, описание).
    """
    env = os.environ.get('KLPACE', '22.4').strip().lower()
    if env in ('off', 'no', 'none', '0', ''):
        # ограничителя нет: снять код и переменные, чтобы не платить ни байта
        src = src.replace("@PACE_ROUND@", "")
        i0 = src.index("; ---- ОГРАНИЧИТЕЛЬ ТЕМПА")
        i1 = src.index("vp_done:", i0)
        i1 = src.index("\n", src.index("ret", i1)) + 1
        src = src[:i0] + src[i1:]
        for cell in ('pace_el', 'pace_acc'):
            src = re.sub(r'(?m)^' + cell + r':[ \t]*DS[ \t]+1[^\n]*\n', '', src, count=1)
        src = re.sub(r'(?m)^[ \t]*ld[ \t]+hl, pace_el[^\n]*\n[ \t]*inc[ \t]+\(hl\)[^\n]*\n'
                     r'[ \t]*jp[ \t]+nz, vi_pace[^\n]*\n[ \t]*dec[ \t]+\(hl\)[^\n]*\n[ \t]*vi_pace:[^\n]*\n',
                     '', src, count=1)
        src = src.replace("vi_pace:\n", "", 1)
        return src, 'выключен (KLPACE=off)'
    rate = float(env)
    FPS = 3000000.0 / 59904.0          # кадр Вектора = 59904 такта на 3 МГц -> 50.08 Гц
    period = FPS / rate
    pint = int(period)
    pfrc = int(round((period - pint) * 256))
    if pfrc == 256:
        pint, pfrc = pint + 1, 0
    assert 0 <= pint <= 255, 'потолок темпа вне диапазона'
    mode = os.environ.get('KLPACEMODE', 'a').strip().lower()
    assert mode in ('a', 'b'), 'KLPACEMODE = a или b'
    src = src.replace("@PACE_ROUND@",
                      "\t\t\t\tjp\t\tvp_h\t\t\t\t; вариант Б: хотя бы один кадр ожидания всегда\n" if mode == 'b' else "", 1)
    src = src.replace("vec_pace:\t\t",
                      "PACE_INT\t\tequ\t\t%d\t\t\t; потолок темпа: целых кадров на итерацию\n"
                      "PACE_FRC\t\tequ\t\t%d\t\t\t; и дробь, 1/256 кадра (%.1f итер/с)\n"
                      "vec_pace:\t\t" % (pint, pfrc, rate), 1)
    old = "loc_B000:\n"
    assert old in src, 'loc_B000'
    src = src.replace(old, old + "\t\t\t\tcall    vec_pace                                ; KL: потолок темпа (вариант %s)\n" % mode.upper(), 1)
    eff = FPS / (pint + pfrc / 256.0)
    return src, 'вариант %s, %d+%d/256 кадра = %.2f итер/с' % (mode.upper(), pint, pfrc, eff)


def kl_no_delay(src):
    """Искусственная задержка оригинала (при <6 объектов) — снять: на i8080 темп и так ниже; состояние не меняет."""
    old = "game_delay:\n\t\t\t\tld      hl, 0x500\n"
    assert old in src, "game_delay"
    return src.replace(old, "game_delay:\n\t\t\t\tjp      no_delay\t\t\t\t; KL: задержка снята\n\t\t\t\tld      hl, 0x500\n", 1)

def kl_logic_fixes(src):
    """Самомодифицирующийся код логики -> переменные; .copy_loop как в оригинале (счётчик в памяти)."""
    # --- самомодифицирующийся код ЛОГИКИ (3 сайта) -> переменные ---
    # 1) upd_182_183 (мяч): опкод jr nc/jr c писался в loc_B65D и loc_B676
    old = "\t\t\t\tld      (loc_B65D), a\n\t\t\t\tld      (loc_B676), a                           ; self-modifying code\n"
    assert old in src, "ball self-mod"
    src = src.replace(old, "\t\t\t\tld      (kl_ball_cc), a                         ; KL: условие перехода в переменной\n", 1)
    for lbl, tgt in (("loc_B65D", "loc_B661"), ("loc_B676", "loc_B67A")):
        i = src.index(lbl + ":"); j = src.index("\n", i) + 1
        line = src[j:src.index("\n", j)]
        assert "jr      NC, " + tgt in line, line
        new = ("\t\t\t\tpush    af\n\t\t\t\tld      a, (kl_ball_cc)\n\t\t\t\tcp      0x38\n\t\t\t\tjp      z, kl_%s_c\n"
               "\t\t\t\tpop     af\n\t\t\t\tjp      nc, %s\n\t\t\t\tjp      kl_%s_n\n"
               "kl_%s_c:\t\tpop     af\n\t\t\t\tjp      c, %s\nkl_%s_n:") % (lbl, tgt, lbl, lbl, tgt, lbl)
        src = src[:j] + new + src[j + len(line):]
    # 2) upd_54/55 (блоки): смещения (ix+d) патчились в код
    old = "\t\t\t\tld      a, h\n\t\t\t\tld      (loc_B6DE+2), a                         ; IX+2/1\n\t\t\t\tld      a, l\n\t\t\t\tld      (loc_B6EF+2), a                         ; IX+10/9\n"
    assert old in src, "block self-mod"
    src = src.replace(old, "\t\t\t\tld      (kl_blk_off), hl                        ; KL: смещения в переменной\n", 1)
    i = src.index("loc_B6DE:"); j = src.index("\n", i) + 1; line = src[j:src.index("\n", j)]
    assert "ld      a, (ix+1)" in line, line
    src = src[:j] + ("\t\t\t\tpush    hl\n\t\t\t\tpush    de\n\t\t\t\tld      hl, (z80_ix)\n\t\t\t\tld      a, (kl_blk_off+1)\n"
                     "\t\t\t\tld      e, a\n\t\t\t\tld      d, 0\n\t\t\t\tadd     hl, de\n\t\t\t\tld      a, (hl)\n\t\t\t\tpop     de\n\t\t\t\tpop     hl") + src[j + len(line):]
    i = src.index("loc_B6EF:"); j = src.index("\n", i) + 1; line = src[j:src.index("\n", j)]
    assert "ld      (ix+9), a" in line, line
    src = src[:j] + ("\t\t\t\tpush    hl\n\t\t\t\tpush    de\n\t\t\t\tpush    af\n\t\t\t\tld      hl, (z80_ix)\n\t\t\t\tld      a, (kl_blk_off)\n"
                     "\t\t\t\tld      e, a\n\t\t\t\tld      d, 0\n\t\t\t\tadd     hl, de\n\t\t\t\tpop     af\n\t\t\t\tld      (hl), a\n\t\t\t\tpop     de\n\t\t\t\tpop     hl") + src[j + len(line):]
    # 3) flag_room_visited: SET n,(HL) как данные (CB-префикс) -> маска через сдвиг
    src, n_frv = re.subn(r'flag_room_visited:.*?\n\t+ret\n',
        "flag_room_visited:\t\t\t\t; KL: переписано без самомодификации (i8080)\n"
        "\t\t\t\tld      a, (graphic_objs_tbl+8)\n\t\t\t\tld      c, a\n\t\t\t\trrca\n\t\t\t\trrca\n\t\t\t\trrca\n\t\t\t\tand     0x1F\n"
        "\t\t\t\tld      e, a\n\t\t\t\tld      d, 0\n\t\t\t\tld      hl, scrn_visited\n\t\t\t\tadd     hl, de\n"
        "\t\t\t\tld      a, c\n\t\t\t\tand     7\n\t\t\t\tld      b, a\n\t\t\t\tld      a, 1\n\t\t\t\tinc     b\n"
        "kl_frv_m:\t\tdec     b\n\t\t\t\tjp      z, kl_frv_d\n\t\t\t\trlca\n\t\t\t\tjp      kl_frv_m\n"
        "kl_frv_d:\t\tor      (hl)\n\t\t\t\tld      (hl), a\n\t\t\t\tret\n", src, count=1, flags=re.S)
    assert n_frv == 1
    # --- print_sprite: ld a,(ix+25) стояло ПОСЛЕ ld sp,hl (SP = данные спрайта). На i8080 это call il_ix_lda,
    #     и адрес возврата + push хелпера пишутся в стек = в спрайт (порча со spr-2). Читать высоту ДО смены SP.
    src, n_ps = re.subn(r'(\t+ld      \(tmp_SP\), sp\n\t+ex      de, hl\n\t+ld      sp, hl\n\t+ex      de, hl\n)(\t+ld      a, \(ix\+25\)[^\n]*\n)',
                        lambda m: m.group(2) + m.group(1), src, count=1)
    assert n_ps == 1, "print_sprite ld sp order"
    return src

def kl_seed_oracle(src, target):
    # loc_B000: seed_3 += mem[seed_2] + L + H, где HL = ЗНАЧЕНИЕ seed_2 (0x5678+N) — игра читает
    # байт памяти по адресу-счётчику (на ZX это экранный битмап!). Зависит от раскладки и от
    # рендера => в цели lockstep байт подаёт стенд через kl_rnd_feed (вычисляется из журнала ZX).
    old = "\t\t\t\tld      a, (seed_3)\n\t\t\t\tadd     a, (hl)\n\t\t\t\tadd     a, l\n\t\t\t\tadd     a, h\n\t\t\t\tld      (seed_3), a\n"
    assert src.count(old) == 1, "seed_3 mem-mix"
    if target == 'lockstep':
        src = src.replace(old, "\t\t\t\tld      a, (seed_3)\n\t\t\t\tpush    hl\n\t\t\t\tld      hl, kl_rnd_feed\t\t; KL(lockstep): байт mem[seed_2] подаёт стенд\n\t\t\t\tadd     a, (hl)\n\t\t\t\tpop     hl\n\t\t\t\tadd     a, l\n\t\t\t\tadd     a, h\n\t\t\t\tld      (seed_3), a\n", 1)
    # render_dynamic_objects/.copy_loop: оригинал (0xD666) ДЕКРЕМЕНТИРУЕТ objs_wiped_cnt в памяти на
    # каждом копировании (в конце = 0); jonsole считает в AF' и память не трогает -> журнал расходится.
    # Восстанавливаем семантику оригинала.
    src, n_cl = re.subn(r'\t+ld\s+a,\s*\(hl\)\r?\n\t+and\s+a\r?\n\t+jr\s+z,\s*loc_D679\r?\n\s*\.copy_loop:.*?jr\s+nz,\s*\.copy_loop\r?\n',
        "kl_copy_loop:\t\tld      hl, objs_wiped_cnt\t\t; KL: как в оригинале 0xD666 — счётчик в памяти\n"
        "\t\t\t\tld      a, (hl)\n\t\t\t\tand     a\n\t\t\t\tjp      z, loc_D679\n\t\t\t\tdec     (hl)\n"
        "\t\t\t\tpop     bc\n\t\t\t\tpop     hl\n\t\t\t\tpop     de\n\t\t\t\tcall    blit_to_screen\n\t\t\t\tjp      kl_copy_loop\n",
        src, count=1, flags=re.S)
    assert n_cl == 1, "copy_loop"
    return src

# ============================ Knight Lore glue ============================
KL_HEADER = """
; ---- Vector-06C boot stub (.r0m грузится с 0x0000, PC=0) ----
				ORG		0x0000
				di
				ld		sp, 0xC000
				jp		START
; ---- scratch i8080-эмуляции + виртуальная клавиатура (область ПЗУ ZX, на Векторе это RAM) ----
				ORG		0x0040
vkbd:			DS		8			; 8 рядов ZX-матрицы, активный ноль, 0xFF = ничего не нажато
z80_ix:			DS		2
z80_iy:			DS		2
z80_af_alt:		DS		2
z80_bc_alt:		DS		2
z80_de_alt:		DS		2
z80_hl_alt:		DS		2
il_tmp:			DS		2
il_tmp2:		DS		2
il_a:			DS		2
il_f:			DS		2			; компактные IX-хелперы: сохранённый AF
il_outstub:		DS		3
kl_seed:		DS		1			; seed_1 при старте (у ZX — FRAMES 0x5C78); стенд задаёт
kl_ball_cc:		DS		1			; мяч: 0x30=jr nc / 0x38=jr c (в оригинале самомод. опкод)
kl_blk_off:		DS		2			; блок: +0 смещение dX/dY (9/10), +1 смещение X/Y (1/2)
kl_rnd_feed:	DS		1			; lockstep: байт mem[seed_2] из журнала ZX
__KL_SCRATCH__
				ORG		0x00A0			; хелперы ldir (scratch выше кончается ~0x85 — не пересекать!)
"""
KL_READ_PORT = """read_port:		; A = маска рядов (бит i=0 -> ряд i опрошен). Вместо ULA: AND выбранных рядов vkbd,
				; затем cpl/and 0x1F как в оригинале. Портит только A и флаги (как оригинал).
				push	hl
				push	bc
				push	de
				ld		c, a
				ld		hl, vkbd
				ld		b, 8
				ld		d, 0xFF
rp_loop:		ld		a, c
				rra
				ld		c, a
				jp		c, rp_skip
				ld		a, (hl)
				and		d
				ld		d, a
rp_skip:		inc		hl
				dec		b
				jp		nz, rp_loop
				ld		a, d
				cpl
				and		0x1F
				pop		de
				pop		bc
				pop		hl
				ret
; End of function read_port"""

def _stub_after_label(src, label, note):
    """Вставить `ret` сразу после строки `label:` (первое вхождение) — тело становится мёртвым."""
    pat = re.compile(r'^(' + re.escape(label) + r':[^\n]*\n)', re.M)
    m = pat.search(src)
    if not m:
        raise SystemExit(f"glue: метка {label} не найдена")
    return src[:m.end()] + f"\t\t\t\tret\t\t\t; STUB({note})\n" + src[m.end():]


KL_SETMODE_VEC = """kl_setmode:		; номер режима (old_input_method 0..2) -> user_input_method + полоса подсветки.
				; ПОДСВЕТКА — фон строки, а не инверсия картинки: буквы остаются белыми, как у Городецкого
				; (инверсию байтов пробовали — шрифт игры «жирный», негатив читается как каша).
				; Как: в плоскости 0 знакоместа строки заливаются 0xFF, а различает буквы от фона ПЛОСКОСТЬ 1
				; (буфер кадра): в меню в нём лежит тот же текст и та же рамка, что на экране. Палитра меню:
				; индексы 8..11 (p0=1, p1=0) — синий, 12..15 (p0=1, p1=1) — белый. Точки с p0=0 остаются
				; чёрными при любых p1..p3 — поэтому КОД, лежащий в плоскостях 1..3 вне картинки (строки 0..31
				; и 224..255) и в плоскости 3 внутри неё, не проступает. Заливать фон в плоскости 2 нельзя
				; именно поэтому: пришлось бы красить индексы с p0=0, и весь этот код вылез бы синей рябью.
				; Экран Вектора столбцовый: адрес = 0x8000 + столбец*256 + (Y + 32); глиф print_8x8 идёт ВНИЗ
				; по младшему байту, поэтому нижняя строка знакоместа = Y + 25.
				call    kl_unbar								; снять прежнюю полосу
				ld      a, (old_input_method)
				ld      e, a
				add     a, a
				add     a, e									; *3
				ld      e, a
				ld      d, 0
				ld      hl, kl_modetab
				add     hl, de
				ld      a, (hl)
				ld      (user_input_method), a
				inc     hl
				ld      d, (hl)									; нижняя строка полосы
				inc     hl
				ld      b, (hl)									; ширина в знакоместах
				ld      h, high (0x8000 + 6*256)				; X = 48 -> столбец 6
				ld      a, 0xFF
ksm_col:		ld      l, d
				ld      c, 8
ksm_row:		ld      (hl), a
				inc     l
				dec     c
				jp      nz, ksm_row
				inc     h
				dec     b
				jp      nz, ksm_col
				ret												; ПРОВАЛИТЬСЯ В kl_menu_exit НЕЛЬЗЯ: он снимает полосу
kl_menu_exit:	; Картинка меню висит на экране, пока не отрисована комната (несколько секунд:
				; мелодия старта, тасовка предметов, первый рендер), а буфер к тому времени уже
				; очищен. Поэтому уходим из меню начисто: полосу снять, палитру вернуть белую —
				; иначе всё это время висели бы белый прямоугольник вместо строки и синий текст
				; (в палитре меню p1=0 — синий, а буфер пуст).
				; ЗАМОК КНОПОК: игру запустили с зажатой FIRE — в игре она не должна тут же читаться
				; как прыжок и «взять». Замок снимает сам kl_vec_input, когда кнопку отпустят.
				ld      a, 0xC0
				ld      (vec_joylck), a
				call    kl_unbar
				ld      a, 7
				jp      vec_set_pal
kl_unbar:		; чистый текст трёх строк режимов из буфера в плоскость 0: столбцы 6..26,
				; строки 112..167. Два блита по 28 строк — быстрый путь blit_to_screen до 32.
				ld      hl, vidbuf + 6*256 + 112
				ld      de, 0x8000 + 6*256 + 112
				ld      bc, 28*256 + 21
				call    blit_to_screen
				ld      hl, vidbuf + 6*256 + 140
				ld      de, 0x8000 + 6*256 + 140
				ld      bc, 28*256 + 21
				jp      blit_to_screen
kl_modetab:		; режим: user_input_method, нижняя строка полосы, ширина в знакоместах
				DB      0, 160, 10								; 1 KEYBOARD            (Y = 135, 10 знаков)
				DB      4, 136, 8								; 2 CURSOR              (Y = 111,  8 знаков)
				DB      8, 112, 21								; 3 DIRECTIONAL CONTROL (Y =  87, 21 знак)
"""


def kl_cut_controls(src, vec=False):
    """Борис 2026-09-10/11: варианты ввода ZX (Kempston/курсор/Interface II) вырезаны — на Векторе только
    клавиатура. Меню повторяет порт Городецкого (caglrc.cc/scalar/ware/873): шесть строк — заголовок,
    три режима управления, «0 START GAME», копирайт; выбранный режим подсвечен цветной полосой.

        1 KEYBOARD              O / P поворот, Q вперёд, A прыжок, цифры 0..7 взять
        2 CURSOR                стрелки влево/вправо поворот, вверх вперёд, пробел прыжок, вниз взять
        3 DIRECTIONAL CONTROL   стрелки = стороны света, пробел прыжок, цифры 0..7 взять

    user_input_method: 0 / 4 / 8 (бит 3 = directional, как в оригинале; 0 у режима 1 ОБЯЗАТЕЛЕН —
    ячейка сравнивается в локстепе, на ZX там 0). Номер режима 0..2 живёт в old_input_method: та
    ячейка освободилась (toggle_audio_hw_x16 вырезан) и лежит ВЫШЕ objs_wiped_cnt, то есть переживает
    игру — режим сохраняется между заходами в меню. debounce_5 больше не нужен: перерисовка идёт не по
    антидребезгу, а по факту смены режима."""
    def cut(pat, name, repl='', flags=re.S, count=1):
        nonlocal src
        src, n = re.subn(pat, repl, src, count=count, flags=flags)
        assert n == count, "kl_cut_controls: " + name
    # меню: 6 строк. Y с шагом 24 (как у Городецкого), X строк режимов 48 — как в оригинале,
    # самая длинная («3 DIRECTIONAL CONTROL», 21 знак) стоит по центру кадра. Цвета на Векторе не
    # используются (атрибутов нет), но display_text_list читает по байту на строку — нужно 6.
    cut(r'menu_colours:   DB 0x43, 0xC4, 0x44, 0x44, 0x44, 0x45, 0x47, 0x47', 'menu_colours', 'menu_colours:   DB 0x43, 0x44, 0x44, 0x44, 0x45, 0x47')
    cut(r'menu_xy:        DB 88, 159, 48, 143, 48, 127, 48, 111, 48, 95, 48, 79\r?\n\t+DB 48, 63, 80, 39', 'menu_xy', 'menu_xy:        DB 88, 159, 48, 135, 48, 111, 48, 87\n\t\t\t\tDB 48, 63, 80, 39')
    # строки режимов: «1 KEYBOARD» и «3 DIRECTIONAL CONTROL» — оригинальные (у второй сменён номер),
    # «2 CURSOR» набрана заново. Токены: цифра = значение, A=0xA .. Z=0x23, пробел 0x26, бит 7 = конец.
    cut(r'; "2 KEMPSTON JOYSTICK"\r?\n.*?(?=; "0 START GAME")', 'menu_text 2..5',
        '; "2 CURSOR"\n'
        '\t\t\t\tDB 2, 0x26, 0xC, 0x1E, 0x1B, 0x1C, 0x18, 0x9B\n\n'
        '; "3 DIRECTIONAL CONTROL"\n'
        '\t\t\t\tDB 3, 0x26, 0xD, 0x12, 0x1B, 0xE, 0xC, 0x1D, 0x12, 0x18\n'
        '\t\t\t\tDB 0x17, 0xA, 0x15, 0x26, 0xC, 0x18, 0x17, 0x1D, 0x1B\n'
        '\t\t\t\tDB 0x18, 0x95\n\n')
    cut(r'(display_menu:\r?\n(?:[^\n]*\n){4}\t+ld      b, )8(\s+; 8 lines to display)', 'display_menu b', r'\g<1>6\g<2>')
    # вход в меню: рамку и выгрузку на экран делаем сами — ПОСЛЕ инверсии выбранной строки
    # (display_text_list зовёт print_border/update_screen только при suppress_border = 0).
    # вход в меню: как в оригинале (текст + рамка + выгрузка на экран), но потом буфер набивается
    # заново: полоса подсветки красится ПО БУФЕРУ (см. KL_SETMODE_VEC), а update_screen его обнулил.
    cut(r'do_menu_selection:\r?\n.*?(?=menu_loop:)', 'do_menu_selection head',
        "do_menu_selection:\n"
        "\t\t\t\txor     a\n"
        "\t\t\t\tld      (suppress_border), a\n"
        "\t\t\t\tcall    clear_scrn_buffer\n"
        "\t\t\t\tcall    display_menu                            ; текст, рамка, выгрузка на экран\n"
        "\t\t\t\tcall    display_menu                            ; буфер снова с текстом,\n"
        "\t\t\t\tcall    print_border                            ; и с рамкой: update_screen его обнулил\n"
        "\t\t\t\tcall    kl_setmode                              ; режим + полоса подсветки\n\n")
    # цикл меню: клавиши 1/2/3 выбирают режим, «0» — старт. Полоса подсветки живёт в плоскости 2
    # и меж кадрами не трогается — при смене режима перерисовывать текст (плоскость 0) не нужно.
    cut(r"\t+ld      a, 0xF7 ; '[^\n]*; 1,2,3,4,5\r?\n.*?; End of function do_menu_selection\r?\n", 'menu loop tail',
        "\t\t\t\tld      a, 0xF7                                 ; ряд ZX 3: клавиши 1..5\n"
        "\t\t\t\tcall    read_port\n"
        "\t\t\t\tand     7                                      ; '1' / '2' / '3' — режим управления\n"
        "\t\t\t\tjp      z, kl_ctl_done\n"
        "\t\t\t\tld      b, 0xFF                                 ; B = номер младшего нажатого бита\n"
        "kl_ctl_idx:\t\tinc     b\n"
        "\t\t\t\trra\n"
        "\t\t\t\tjp      nc, kl_ctl_idx\n"
        "\t\t\t\tld      hl, old_input_method                    ; номер режима 0..2\n"
        "\t\t\t\tld      a, b\n"
        "\t\t\t\tcp      (hl)\n"
        "\t\t\t\tjp      z, kl_ctl_done                          ; тот же режим — ничего не трогаем\n"
        "\t\t\t\tld      (hl), a\n"
        "\t\t\t\tcall    kl_setmode\n"
        "kl_ctl_done:\tcall    kl_joy_menu                             ; джойстик: раскладка по фронту\n"
        "\t\t\t\tld      a, 0xEF                                 ; ряд ZX 4: клавиши 0,9,8,7,6\n"
        "\t\t\t\tcall    read_port\n"
        "\t\t\t\tand     1                                      ; '0' — начать игру\n"
        "\t\t\t\tjp      nz, kl_menu_exit\n"
        "\t\t\t\tld      hl, seed_1\n"
        "\t\t\t\tinc     (hl)                                    ; remember when BASIC games did this?\n"
        "\t\t\t\tjp      menu_loop\n"
        "; End of function do_menu_selection\n")
    cut(r'flash_menu:\r?\n.*?; End of function flash_menu\r?\n', 'flash_menu',
        KL_SETMODE_VEC if vec else "kl_setmode:\t\tret\t\t\t; полоса подсветки — только Вектор (плоскость 2)\n"
                                   "kl_menu_exit:\tret\n"
                                   "kl_joy_menu:\tret\t\t\t; джойстик — только Вектор (порт 0x0E)\n")
    cut(r'toggle_selected:\r?\n.*?; End of function toggle_selected\r?\n', 'toggle_selected')
    # ввод: только клавиатура; directional — по биту 3 без проверки джойстика
    cut(r'\t+ld      a, \(user_input_method\)\r?\n\t+rrca\r?\n\t+and     3[^\n]*\r?\n\t+jp      Z, keyboard\r?\n.*?(?=keyboard:)', 'input dispatch')
    cut(r'(chk_pickup_drop:\r?\n)\t+ld      hl, user_input_method\r?\n\t+ld      a, \(hl\)\r?\n\t+and     6[^\n]*\r?\n\t+ld      a, \(user_input\)[^\n]*\r?\n\t+jr      Z, loc_C00B[^\n]*\r?\n\t+bit     3, \(hl\)[^\n]*\r?\n\t+jr      Z, loc_C00B\r?\n', 'chk_pickup_drop',
        r'\g<1>\t\t\t\tld      a, (user_input_method)\n\t\t\t\tand     8                                      ; directional?\n\t\t\t\tld      a, (user_input)\n\t\t\t\tjp      z, loc_C00B\n')
    cut(r'(handle_left_right:\r?\n)\t+ld      hl, user_input_method\r?\n\t+ld      a, \(hl\)\r?\n\t+and     6[^\n]*\r?\n\t+jr      Z, left_right_rotational[^\n]*\r?\n\t+bit     3, \(hl\)[^\n]*\r?\n\t+jr      Z, left_right_rotational\r?\n', 'handle_left_right head',
        r'\g<1>\t\t\t\tld      a, (user_input_method)\n\t\t\t\tand     8                                      ; directional?\n\t\t\t\tjp      z, left_right_rotational\n')
    return src

def kl_glue(src, target):
    n0 = src.count('\n')
    # заголовок после DEVICE
    src = src.replace("\tDEVICE ZXSPECTRUM48\n", "\tDEVICE ZXSPECTRUM48\n" + KL_HEADER.replace("__KL_SCRATCH__", kl_native.SCRATCH + kl_native_render.SCRATCH + kl_native_logic.SCRATCH) + _LDIR_HELPERS + "\n", 1)
    src = kl_logic_fixes(src)
    src = kl_cut_controls(src)
    src = kl_seed_oracle(src, target)
    # read_port -> виртуальная клавиатура
    src = re.sub(r'read_port:\n.*?; End of function read_port', KL_READ_PORT, src, count=1, flags=re.S)
    if target == 'lockstep':
        # РАСКЛАДКА ЛОКСТЕПА (код после трансляции ~47.6К, в раскладку ZX не влезает):
        #   0x0000 boot/scratch/ldir | 0x0100 код+данные (~0xBC00) | стек 0xBC00-0xBFFF |
        #   0xC000 переменные (0x568) | 0xC600 vidbuf 6К | 0xDE00 zx_vram 6К (= shift_bits_table,
        #   таблица без читателей при заглушенном рендере) | 0xF600 zx_aram | 0xF900 reverse_bits_table
        src = src.replace("\t\t\t\tORG 0x4000\nzx_vram:", "\t\t\t\tORG 0xDE00\nzx_vram:", 1)
        src = src.replace("\t\t\t\tORG 0x5800\nzx_aram:", "\t\t\t\tORG 0xF600\nzx_aram:", 1)
        src = re.sub(r'^\t+ORG 0x5BA0\nclear_start:', '\t\t\t\tORG 0xC000\nclear_start:', src, count=1, flags=re.M)
        src = src.replace("\nclear_end:\n", "\nclear_end:\n\t\t\t\tORG 0x0100\t; код и данные игры\n", 1)
        # `font` в оригинале лежит сразу за таблицей объектов и служит ограничителем «конец
        # таблицы» (3 сайта); после ORG 0x0100 он ниже таблицы -> заменяем на clear_end.
        n_end = src.count("ld      bc, font")
        src = src.replace("ld      bc, font", "ld      bc, clear_end")
        src = src.replace("ld      hl, font\n\t\t\t\tand     a", "ld      hl, clear_end\n\t\t\t\tand     a", 1)
        assert n_end == 2, n_end
        src = re.sub(r'^\t+ORG\s+0xD900\nvidbuf:', '\t\t\t\tORG 0xC600\nvidbuf:', src, count=1, flags=re.M)
        src = re.sub(r'^\t+ORG\s+0xF100\nreverse_bits_table:\n\t+DS\t+0x100\nshift_bits_table:\n\t+DS\t+0xE00',
                     '\t\t\t\tORG 0xF900\nreverse_bits_table:\n\t\t\t\tDS\t\t0x100\nshift_bits_table:\tequ 0xDE00\t; alias zx_vram (без читателей)', src, count=1, flags=re.M)
        src = re.sub(r'^(START:\s+ld\s+sp,\s*)0x5B9[Ff]', r'\g<1>0xC000', src, count=1, flags=re.M)
        src = src.replace("ld      a, (0x5C78)", "ld      a, (kl_seed)", 1)
        n_org = src.count('ORG')
        # пиксельный цикл print_sprite: всё после сохранения SP мёртво
        src = re.sub(r'^\t+ld\s+\(tmp_SP\),\s*sp[^\n]*$', "\t\t\t\tret\t\t\t; STUB(lockstep): пиксельный цикл print_sprite отключён", src, count=1, flags=re.M)
        for lbl in ('blit_to_screen', 'vidbuf_fill_window', 'vidbuf_clear_window'):
            src = _stub_after_label(src, lbl, 'lockstep: самомод. djnz, только vidbuf/экран')
    # выход: SAVEBIN вместо SAVESNA; ASSERT по размеру кода снимаем (код растёт)
    src = re.sub(r'^\s*ASSERT\s+\$\s*<\s*0xD900[^\n]*$', '', src, flags=re.M)
    src = re.sub(r'^\s*SAVESNA[^\n]*$', '\t\t\t\tSAVEBIN "build/knightlore_8080.r0m", 0, 0x10000', src, flags=re.M)
    return src

SCRATCH_DEFS_VEC = """z80_ix:			DS		2
z80_iy:			DS		2
z80_af_alt:		DS		2
z80_bc_alt:		DS		2
z80_de_alt:		DS		2
z80_hl_alt:		DS		2
il_tmp:			DS		2
il_tmp2:		DS		2
il_a:			DS		2
il_f:			DS		2
il_outstub:		DS		3
kl_ball_cc:		DS		1
kl_blk_off:		DS		2
kl_lines:		DS		1			; print_sprite: счётчик строк (вместо AF')
"""

# ============================ Vector glue (общая для z80vec и vec) ============================
VEC_HEADER = """
; ---- Вектор-06Ц: загрузчик (.r0m с 0x0000, PC=0). Всё в подмножестве i8080. ----
				ORG		0x0000
				di
				ld		sp, clear_start
				jp		vec_boot
				ORG		0x0038
				jp		vec_isr			; RST 38h: кадровое прерывание (палитра в VBlank, скан клавиатуры)
				ORG		0x0040
vec_kbmat:		DS		8			; матрица клавиатуры Вектора, active-high (ряд 0..7)
vec_tmp:		DS		2
vec_palreq:		DS		1			; 1 = загрузить палитру в ближайшем VBlank
vec_zxrows:		DB		0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF	; 8 рядов ZX (active-low), строит ISR из vec_kbmat по vec_zxmap;
									; 0xFF — согласовано с vec_kbmat = 0 и vec_kbchg = 0 (ранний выход vec_zxbuild)
vec_kbchg:		DS		1			; != 0, если vec_scan увидел отличие матрицы от прошлого кадра
vec_pal:		DS		16			; палитра (индекс = p0<<3|p1<<2|p2<<1|p3), строит vec_set_pal
pace_el:		DS		1			; ограничитель темпа: кадров с начала итерации (ISR инкрементит, насыщение 255)
pace_acc:		DS		1			; ограничитель темпа: аккумулятор Брезенхэма (дробная часть периода, 1/256 кадра)
VEC_PSB_PAD		equ		1			; запас оценки print_sprite: столько*256 тиков (512 тактов) сверх модели цепочки
VEC_HUD_WHITE	equ		1			; 1 = цвета HUD через плоскость 2 (плоскость 2 без кода: верхний регион с 0xE000)
VEC_HUD_INK		equ		0xFF		; статичное перо HUD (белый): цифры жизней, фигурка, DAY, счётчик, знак солнца.
									; Цвет HUD ЗАВИСИТ ТОЛЬКО ОТ p2: p1 — буфер рендера, под пикселем HUD он
									; произволен (замер: под счётчиком дней буфер держит цифры после print_days),
									; и любое второе перо по p1 красит элемент наполовину — «тайлами»
VEC_HUD_BG		equ		0			; фон внутри зон маски. ТОЛЬКО 0: строки вне картинки (0..31 и 224..255)
									; заняты кодом в плоскостях 1 и 2, и любой ненулевой цвет при p0=0 показывает
									; этот код мусором в верхней и нижней кромке экрана (проверено снимком)
				ASSERT $ <= 0x00A0
				ORG		0x0060
vec_boot:		ld		a, 0x88				; 8255: A выход (скролл/ряд клавы), B выход (бордюр/режим)
				out		(0x00), a
				xor		a
				out		(0x02), a			; бордюр чёрный, режим 256
				ld		a, 0xFF
				out		(0x03), a			; скролл
				ld		a, 0x30				; ВИ53 счётчик 0: режим 0, оба байта, выход низкий (динамик молчит)
				out		(0x08), a			; секундомер кадра для vec_di_budget (в режиме ВИ53 счётчик 0 забирает музыка)
				call	msx_snd_init			; зеркало AY, слоты эффектов, заготовка OUT для ВИ53
				ld		a, 7
				call	vec_set_pal			; палитра: белый (меню), запрос загрузки
				ei						; палитру защёлкивает цвет под лучом -> ISR грузит её в VBlank; прерывания остаются ВКЛ
vb_wait:		ld		a, (vec_palreq)
				or		a
				jp		nz, vb_wait
				ld		hl, 0x8000			; очистить плоскость 0
				ld		bc, 0x2000
vb_clr:			ld		(hl), 0
				inc		hl
				dec		bc
				ld		a, b
				or		c
				jp		nz, vb_clr
				jp		START
"""
VEC_TAIL = """
				ORG		0x7B00			; блок Вектора: клавиатура, ВИ53, экран (после START на 0x7A00, до стека)

; ---- палитра: idx 8..15 (бит плоскости 0x8000) = белый, остальные чёрный. Только в VBlank. ----
vec_isr:		push	af				; RST 38h каждый кадр (VBlank): палитра по запросу, скан клавиатуры
				ld		a, (msx_vic0)		; 1 = счётчик 0 играет мелодию (режим ВИ53) — перезаряжать нельзя
				or		a
				jp		nz, vi_nostop
				ld		a, 0xFF				; секундомер кадра (ВИ53 счётчик 0) <- 0xFFFF, считает вниз 1.5 МГц
				out		(0x0B), a
				out		(0x0B), a
vi_nostop:		push	bc
				push	de
				push	hl
				ld		hl, pace_el			; ограничитель темпа: кадров с начала итерации
				inc		(hl)
				jp		nz, vi_pace
				dec		(hl)				; насыщение на 255 (длинная пауза не должна переполнять счётчик)
vi_pace:
				; палитра: записи 15..8 (точка экрана есть) — КАЖДЫЙ кадр, 4 OUT на запись (~900 тактов): запись
				; защёлкивается цветом под лучом, и одиночная загрузка «по запросу» может частично промахнуться мимо
				; обратного хода (поздний вход в ISR) — тогда часть индексов остаётся старой («картинка не полностью
				; закрашивается»). Записи 7..0 (без точки) — чёрные, грузятся по запросу vec_palreq.
				ld		hl, vec_pal + 15
				ld		e, 0x0F
vb_pal:			ld		a, e
				out		(0x02), a			; индекс = цвет бордюра (луч в обратном ходе)
				ld		a, (hl)
				dec		hl
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				dec		e
				ld		a, e
				cp		8
				jp		nc, vb_pal
				ld		a, (vec_palreq)
				or		a
				jp		z, vb_pal_done
vb_pal2:		ld		a, e
				out		(0x02), a
				ld		a, (hl)
				dec		hl
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				dec		e
				jp		p, vb_pal2
				xor		a
				ld		(vec_palreq), a
vb_pal_done:	xor		a
				out		(0x02), a			; бордюр чёрный
vi_kbd:			call	vec_scan
				call	vec_zxbuild
				call	vec_joy					; джойстик (порт 0x0E) — ОДНА строка в ISR, см. §25
				call	msx_mtick				; мелодия: один тик = один кадр
				call	msx_viflush				; зеркало регистров AY -> ВИ53 (в режиме AY сразу ret)
				pop		hl
				pop		de
				pop		bc
				pop		af
				ei
				ret

; ---- клавиатура: скан матрицы Вектора -> vec_kbmat, с зеркалом стрелок в буквы ----
; vec_set_pal: A = атрибут комнаты (биты 0..2 = чернила комнаты, таблица location_tbl как на ZX)
; -> таблица vec_pal, запрос загрузки в VBlank. Портит A, BC, DE, HL.
;
; Индекс цвета = p0<<3|p1<<2|p2<<1|p3. На цвет влияют только p0 (0x8000, картинка) и p2 (0xC000, маска HUD):
; p1 (буфер рендера) и p3 (код/таблицы) произвольны, поэтому записи идут четвёрками, а не парами.
; ЧЕТЫРЕ ЦВЕТА НА ЭКРАНЕ:
;   0,1,4,5     p0=0 p2=0  чёрный              — фон игрового поля (обязан быть чёрным)
;   8,9,12,13   p0=1 p2=0  ЦВЕТ КОМНАТЫ        — картинка комнаты и рамка HUD: колонны, шнур, свитки,
;                                                рамка солнца/луны (перекрашиваются вслед за комнатой)
;   10,11,14,15 p0=1 p2=1  VEC_HUD_INK        — рисунок внутри зон маски: цифры жизней, фигурка, DAY,
;                                                счётчик дней, знак солнца/луны. p1 НЕ участвует
;   2,3,6,7     p0=0 p2=1  VEC_HUD_BG = 0     — фон внутри зон; ненулевым его делать НЕЛЬЗЯ: см. VEC_HUD_BG
; Записи 2,3,6,7 раньше уходили в чёрный по одному биту p0 — теперь p2 проверяется до него (владелец просил
; проверить именно это), но замер показал, что цветной фон зоны засвечивает код в кромках экрана.
vec_set_pal:	and		7
				ld		c, a
				ld		b, 0
				ld		hl, vec_zxcol
				add		hl, bc
				ld		e, (hl)				; цвет комнаты %BBGGGRRR
				ld		c, VEC_HUD_INK
				ld		d, VEC_HUD_BG
				IF VEC_HUD_WHITE == 0
				ld		c, e				; плоскость 2 занята кодом — маски нет, HUD цветом комнаты
				ld		d, 0
				ENDIF
				ld		hl, vec_pal
				xor		a
				ld		(hl), a				; 0,1   фон поля
				inc		hl
				ld		(hl), a
				inc		hl
				ld		(hl), d				; 2,3   фон зоны
				inc		hl
				ld		(hl), d
				inc		hl
				xor		a
				ld		(hl), a				; 4,5   фон поля
				inc		hl
				ld		(hl), a
				inc		hl
				ld		(hl), d				; 6,7   фон зоны
				inc		hl
				ld		(hl), d
				inc		hl
				ld		(hl), e				; 8,9   цвет комнаты
				inc		hl
				ld		(hl), e
				inc		hl
				ld		(hl), c				; 10,11 перо HUD
				inc		hl
				ld		(hl), c
				inc		hl
				ld		(hl), e				; 12,13 цвет комнаты
				inc		hl
				ld		(hl), e
				inc		hl
				ld		(hl), c				; 14,15 то же перо HUD (p1 на цвет не влияет)
				inc		hl
				ld		(hl), c
				ld		a, 1
				ld		(vec_palreq), a
				ret
vec_zxcol:		DB		0xFF, 0xC0, 0x07, 0xC7, 0x38, 0xF8, 0x3F, 0xFF	; ZX 0..7: (чёрный->белый) синий красный пурпурный зелёный голубой жёлтый белый

; vec_hud_mask: строит маску HUD в плоскости 2 по таблице зон. Внутри ИГРОВОГО экрана маска
; СТАТИЧНА: её кладут один раз на комнату (блок перерисовки панели, render_status_info != 0) и
; больше не трогают — ни перерисовка цифр, ни ход знака солнца/луны её не меняют. Так цвет элемента не может отстать от картинки: любой пиксель, попавший
; внутрь прямоугольника зоны, красится пером HUD в тот же кадр, кто бы его ни нарисовал.
; Запись зоны — 5 байт: страница столбца в плоскости 2 (0xC0 + столбец), сколько столбцов, первая
; строка, сколько строк, ЗНАЧЕНИЕ (0xFF — заливка; 0 — силуэт: байт картинки из плоскости 0, оставлен
; на случай зоны, которую нельзя обвести прямоугольником). Конец таблицы — нулевая страница.
; Портит A, BC, DE, HL.
vec_hud_off:	; ПОГАСИТЬ зоны маски. Экраны без HUD (меню, game over, финал) рисуют в те же
				; прямоугольники рамку и текст, а статичная маска красила их пером HUD — на рамке
				; game over выходили белые куски (замер: 320 точек, все внутри vec_hudcols).
				IF VEC_HUD_WHITE == 0
				ret
				ENDIF
				xor		a
				jp		vhm_sel
vec_hud_mask:	IF VEC_HUD_WHITE == 0
				ret
				ENDIF
				ld		a, 0xFF
vhm_sel:		ld		(vhm_put+1), a		; патч: 0xFF — класть зону, 0x00 — гасить
				ld		hl, vec_hudcols
vhm_run:		ld		a, (hl)
				or		a
				ret		z
				ld		d, a				; страница = 0xC0 + столбец
				inc		hl
				ld		b, (hl)				; сколько столбцов
				inc		hl
				ld		a, (hl)
				ld		e, a				; первая строка
				inc		hl
				ld		a, (hl)
				ld		(vec_tmp), a		; сколько строк
				inc		hl
				ld		a, (hl)
				ld		(vec_tmp+1), a		; значение (0 = силуэт из плоскости 0)
				inc		hl
vhm_col:		push	hl
				push	de					; начало столбца: страница маски + строка
				ld		a, d
				sub		0x40				; плоскость 2 -> плоскость 0
				ld		h, a
				ld		l, e				; HL = источник силуэта
				ld		a, (vec_tmp)
				ld		c, a
vhm_r:			ld		a, (vec_tmp+1)
				or		a
				jp		nz, vhm_put
				ld		a, (hl)
vhm_put:		and		0xFF				; патч из vec_hud_mask / vec_hud_off
				ld		(de), a
				inc		l
				inc		e
				dec		c
				jp		nz, vhm_r
				pop		de
				pop		hl
				inc		d
				dec		b
				jp		nz, vhm_col
				jp		vhm_run

; Геометрия зон снята ЗАМЕРОМ с плоскости 0 (tools/hudshot_vec.js KLDUMP=..., связные компоненты картинки).
; Внутри прямоугольников нет ничего, кроме самого элемента, поэтому заливка ничего не режет и не красит лишнего:
;   фигурка жизней  x 20..28   строки 65..79   шнур идёт по столбцу 5 в строках 76..79 -> фигурка отдельной зоной
;   цифры жизней    x 32..46   строки 64..71   поэтому у цифр только 8 строк
;   DAY             x 116..138 строки 40..47   буква D начинается в СТОЛБЦЕ 14, а не 15
;   счётчик дней    x 120..134 строки 32..39
;   предметы в панели (display_objects) рисуются в столбцах 2..10 строк 32..55 — в зоны не попадают
vec_hudcols:		DB		0xC2, 2, 64, 16, 0xFF	; фигурка жизней: столбцы 2..3, строки 64..79
					DB		0xC4, 2, 64,  8, 0xFF	; цифры жизней: столбцы 4..5, строки 64..71
					DB		0xCE, 4, 32, 16, 0xFF	; DAY и счётчик дней: столбцы 14..17, строки 32..47
; Знак солнца/луны ходит внутри арки, и рисует его не только display_frame (объектный рендер тоже трогает
; это окно), поэтому зона — ЗАЛИВКА по всей области хода знака, а не силуэт: иначе маска отстаёт от картинки
; и знак выходит наполовину цветом комнаты. Прямоугольники обходят пиксели самой рамки (замер: в столбцах
; 24 и 27 рамка занимает строки 33..38 и 56..61, в столбцах 25..26 — строки 36..39 и 59..62), поэтому рамка
; остаётся цветом комнаты. Это максимальная область внутри арки, свободная от рамки.
					DB		0xD8, 1, 39, 17, 0xFF	; столбец 24, строки 39..55
					DB		0xD9, 2, 40, 19, 0xFF	; столбцы 25..26, строки 40..58
					DB		0xDB, 1, 39, 17, 0xFF	; столбец 27, строки 39..55
					DB		0

vec_scan:		; ряды матрицы Вектора -> vec_kbmat (active-high) + vec_kbchg = 0, если матрица та же.
				; Цикл развёрнут: в нём на ряд уходило 70 тактов (счётчик, поворот маски ряда),
				; развёртка с непосредственной маской — 64 такта И заодно считает отличие от
				; прошлого кадра. Ряд 1 не опрашивается: в vec_kbzx все восемь его записей 0xFF
				; (ни одна клавиша ряда 1 не отображена на ZX), а раньше его биты впустую гоняли
				; цепочку zb_bit и гасили биты 5..7 ряда ZX 7, которые read_port всё равно срезает.
				; ПРИМЕСЬ СТРЕЛОК УБРАНА ОТСЮДА в vec_zxbuild: раньше vec_scan дописывал в
				; vec_kbmat+4/+6/+7 зеркало стрелок, и сравнение с прошлым кадром ловило бы
				; собственную примесь как «изменение». Портит A, BC, HL, флаги.
				ld		a, 0x8A				; порт B на вход
				out		(0x00), a
				ld		hl, vec_kbmat
				ld		b, 0				; накопитель отличий
vs_l:			ld		a, 0xFE				; ряд 0
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				inc		hl					; ряд 1 Вектора не отображён ни на одну клавишу ZX
										; (vec_kbzx 8..15 = 0xFF) — не опрашиваем его вовсе
				ld		a, 0xFB				; ряд 2
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				ld		a, 0xF7				; ряд 3
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				ld		a, 0xEF				; ряд 4
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				ld		a, 0xDF				; ряд 5
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				ld		a, 0xBF				; ряд 6
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				ld		a, 0x7F				; ряд 7
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		c, a
				xor		(hl)				; отличие от прошлого кадра
				or		b
				ld		b, a
				ld		(hl), c
				inc		hl
				ld		a, 0x88
				out		(0x00), a
				ld		a, b
				ld		(vec_kbchg), a
				ld		a, 0xFF
				out		(0x03), a			; вернуть скролл
				xor		a
				out		(0x02), a			; вернуть бордюр/режим
				ret

; vec_zxbuild: vec_kbmat (active-high) -> vec_zxrows: 8 байт рядов ZX, бит i = 0 если клавиша i ряда нажата.
; Раз в кадр из ISR; read_port потом только AND'ит готовые байты. Если матрица не изменилась с прошлого
; кадра (vec_kbchg = 0) — ряды ZX уже верны, выходим сразу: полный обход стоит ~900 тактов, выход 22.
; Начальное состояние согласовано статически: vec_kbmat = 0 (DS), vec_zxrows = 0xFF (DB), vec_kbchg = 0.
vec_zxbuild:	ld		a, (vec_kbchg)
				or		a
				ret		z
				ld		hl, vec_zxrows
				ld		a, 0xFF
				ld		b, 8
zb_clr:			ld		(hl), a
				inc		hl
				dec		b
				jp		nz, zb_clr
				ld		hl, vec_kbmat
				ld		de, vec_kbzx
				ld		b, 8
zb_row:			ld		a, (hl)
				or		a
				jp		nz, zb_bits
				ld		a, e				; ряд пуст: пропустить 8 записей
				add		a, 8
				ld		e, a
				ld		a, d				; ПЕРЕНОС ОБЯЗАТЕЛЕН: vec_kbzx (64 Б) может лечь через границу страницы,
				adc		a, 0				; и счёт только по E уводил чтение таблицы в чужую страницу —
				ld		d, a				; клавиатура молча переставала видеть клавиши (поймано 2026-09-11)
				jp		zb_next
zb_bits:		ld		c, a
zb_bit:			ld		a, c
				rra
				ld		c, a
				jp		nc, zb_skip
				ld		a, (de)				; запись: биты 7-5 = ряд ZX, биты 4-0 = маска (0 = нажато)
				push	hl
				push	bc
				ld		c, a
				and		0x1F
				ld		b, a
				ld		a, c
				rlca
				rlca
				rlca
				and		7
				ld		hl, vec_zxrows
				add		a, l
				ld		l, a
				ld		a, b
				and		(hl)
				ld		(hl), a
				pop		bc
				pop		hl
zb_skip:		inc		de
				ld		a, c
				or		a
				jp		nz, zb_bit			; остались нажатые биты
				dec		de					; добрать до конца 8 записей ряда (таблица ALIGN 8).
				ld		a, e				; DE = база+k, k = 1..8 (k-1 = номер старшего нажатого бита).
				or		7					; ДЕКРЕМЕНТ ОБЯЗАТЕЛЕН: без него при k = 8 (нажат бит 7 ряда)
				ld		e, a				; «or 7 : inc de» проскакивало целую восьмёрку, и ВСЕ следующие
				inc		de					; ряды читались из чужих записей, а ряд 7 — вообще за таблицей.
									; Ловилось на стрелке ВНИЗ (ряд 0 бит 7) и на клавишах G, O, W, '7'.
									; 16-битный inc: перенос в D не теряется.
zb_next:		inc		hl
				dec		b
				jp		nz, zb_row
				; стрелки ряда 0 Вектора -> Z / X / A / Q прямо в рядах ZX (раньше их подмешивал
				; vec_scan в vec_kbmat; см. комментарий там). Таблица: маска стрелки в ряду 0,
				; младший байт адреса ряда ZX, маска гашения бита клавиши.
				ld		a, (vec_kbmat+0)
				ld		c, a
				ld		de, vec_arrzx
				ld		b, 4
zb_ar:			ld		a, (de)
				and		c
				inc		de
				ld		a, (de)				; младший байт адреса ряда ZX (LDAX/INX флаги не трогают)
				inc		de
				jp		z, zb_arn
				ld		l, a
				ld		h, high vec_zxrows
				ld		a, (de)
				and		(hl)
				ld		(hl), a
zb_arn:			inc		de
				dec		b
				jp		nz, zb_ar
				jp		vec_rpbuild			; ряды готовы — пересчитать готовые ответы read_port
vec_arrzx:		DB		0x10, low (vec_zxrows+0), 0xFD	; LT -> Z  (ряд ZX 0, бит 1)
				DB		0x40, low (vec_zxrows+0), 0xFB	; RT -> X  (ряд ZX 0, бит 2)
				DB		0x20, low (vec_zxrows+1), 0xFE	; UP -> A  (ряд ZX 1, бит 0)
				DB		0x80, low (vec_zxrows+2), 0xFE	; DN -> Q  (ряд ZX 2, бит 0)

; ---- ДЖОЙСТИК A (порт 0x0E), раз в кадр из ISR. Подробности и таблица сведения — docs §25. ----
; Порт active-low: бит 0 RIGHT, 1 LEFT, 2 UP, 3 DOWN, 6 FIRE1, 7 FIRE2; биты 4 и 5 схема держит
; в единице (кнопок там нет). PPI-инициализация не нужна, чтение ничего не защёлкивает.
;
; ДВЕ ДОРОГИ, потому что их две и у клавиатуры:
;   * ИГРА читает раскладку ПРЯМО ИЗ МАТРИЦЫ (kl_vec_input), и лезть туда джойстику нечем:
;     vec_joy просто кладёт состояние в vec_joyraw, а kl_vec_input подмешивает его к клавишам.
;     В vec_kbmat писать НЕЛЬЗЯ: vec_scan сравнивает матрицу с прошлым кадром, и наша примесь
;     каждый кадр читалась бы как «матрица изменилась» — полный пересбор рядов ZX на ровном месте.
;   * МЕНЮ, «любая клавиша» и пауза читают РЯДЫ ZX (read_port). Туда кнопка сводится к клавише '0'
;     (ряд ZX 4, бит 0): '0' — это и «0 START GAME» в меню, и «любая клавиша» в мелодиях.
; Ряды пересобирает только vec_zxbuild и только когда изменилась матрица, поэтому наш бит надо
; (а) накладывать заново в каждом кадре, где ряды пересобрали, и (б) при ОТПУСКАНИИ кнопки
; заставить пересбор — иначе бит остался бы прижатым навсегда.
; Цена в кадре покоя: 80 тактов тела + 24 на call = 104 (замеряет tools/inputcheck_vec.js, §25).
vec_joy:		in		a, (0x0E)			; 12
				cpl						; 4   -> 1 = нажато
				ld		hl, vec_joyraw		; 12
				ld		c, a				; 4
				xor		(hl)				; 8   изменилось ли состояние
				jp		nz, vj_chg			; 12
				ld		a, c				; 4   нет: кнопка сейчас нажата? (C можно верить: он сошёлся
				and		0xC0				; 8   с vec_joyraw, а туда кладётся уже проверенное значение)
				ret		z					; 12  нет — в рядах ZX нам делать нечего
				ld		a, (vec_kbchg)		; 20
				or		a					; 4
				ret		z					; 12  ряды ZX не пересобирались — наш бит в них цел
				jp		vj_fire
vj_chg:			ld		a, c
				and		0x30				; биты 4 и 5 схема держит в единице, после cpl тут нули. Иначе
				jp		z, vj_ok			; порта нет (эмулятор без джойстика) и «нажато всё» — считаем,
				ld		c, 0				; что отпущено всё: иначе игра была бы неуправляема
vj_ok:			ld		a, (hl)				; что было
				ld		(hl), c				; что стало (действующее состояние — его читает kl_vec_input)
				xor		c					; что изменилось
				and		0xC0				; кнопка?
				jp		z, vj_dir
				ld		a, 0xFF				; кнопку нажали или ОТПУСТИЛИ: ряды ZX собрать заново
				ld		(vec_kbchg), a		; (иначе отпускание не сотрёт наш бит '0')
				call	vec_zxbuild
vj_dir:			ld		a, (vec_joyraw)
				and		0xC0
				ret		z
vj_fire:		ld		hl, vec_zxrows+4	; FIRE -> клавиша '0' (ряд ZX 4, бит 0): старт игры в меню
				ld		a, (hl)				; и «любая клавиша» на экранах мелодий
				and		0xFE
				ld		(hl), a
				jp		vec_rpbuild			; готовые ответы read_port пересчитать
vec_joyraw:		DS		1			; действующее состояние джойстика (active-high): 0 RIGHT 1 LEFT 2 UP
									; 3 DOWN 6 FIRE1 7 FIRE2 — читают kl_vec_input и kl_joy_menu
vec_joylck:		DS		1			; кнопки, «дожатые» из меню: не в счёт, пока их не отпустят
vec_joyprv:		DS		1			; направления прошлого опроса МЕНЮ (антидребезг по фронту)

; vec_rpbuild: ряды ZX -> vec_rptab, готовые ответы read_port для масок, которыми игра реально
; пользуется. Значение = cpl(AND выбранных рядов) and 0x1F, то есть всегда <= 0x1F; 0xFF в таблице
; означает «маска не считана» и отправляет read_port на общий цикл — поэтому неучтённая маска
; остаётся ПРАВИЛЬНОЙ, просто медленной. Список снят замером (трасса A на входе read_port):
; в игре живут семь масок (0xFE 0x7F 0x7E 0xBD 0xDB 0xE7 0x99), ещё пять — в меню и паузе.
; Зовётся только из vec_zxbuild, то есть в кадр, когда матрица изменилась. Портит A, BC, DE, HL.
vec_rpbuild:	ld		a, (vec_zxrows+0)
				ld		b, a				; B = ряд 0
				cpl
				and		0x1F
				ld		(vec_rptab+0xFE), a
				ld		a, (vec_zxrows+7)
				ld		c, a				; C = ряд 7
				cpl
				and		0x1F
				ld		(vec_rptab+0x7F), a
				ld		a, b
				and		c
				ld		b, a				; B = ряд0 & ряд7
				cpl
				and		0x1F
				ld		(vec_rptab+0x7E), a
				ld		a, (vec_zxrows+1)
				ld		c, a
				cpl
				and		0x1F
				ld		(vec_rptab+0xFD), a
				ld		a, (vec_zxrows+6)
				and		c
				ld		c, a				; C = ряд1 & ряд6
				cpl
				and		0x1F
				ld		(vec_rptab+0xBD), a
				ld		a, (vec_zxrows+2)
				ld		d, a
				cpl
				and		0x1F
				ld		(vec_rptab+0xFB), a
				ld		a, (vec_zxrows+5)
				and		d
				ld		d, a				; D = ряд2 & ряд5
				cpl
				and		0x1F
				ld		(vec_rptab+0xDB), a
				ld		a, c
				and		d
				ld		e, a				; E = ряды 1,2,5,6
				cpl
				and		0x1F
				ld		(vec_rptab+0x99), a
				ld		a, (vec_zxrows+3)
				ld		c, a
				cpl
				and		0x1F
				ld		(vec_rptab+0xF7), a
				ld		a, (vec_zxrows+4)
				ld		d, a
				cpl
				and		0x1F
				ld		(vec_rptab+0xEF), a
				ld		a, c
				and		d
				ld		c, a				; C = ряд3 & ряд4
				cpl
				and		0x1F
				ld		(vec_rptab+0xE7), a
				ld		a, b
				and		c
				and		e					; все восемь рядов
				cpl
				and		0x1F
				ld		(vec_rptab+0x00), a
				ret
__VEC_KBZX__
; ---- таблица ZX-ряд -> клавиши Вектора: ИСХОДНЫЕ ДАННЫЕ СБОРЩИКА (VEC_ZXMAP ниже), в образ не идёт ----
; §17.0: в ПЗУ на неё нет ни одной ссылки — vec_zxbuild читает только vec_kbzx, которую строит
; сборщик из этого же текста. 80 байт нижнего региона освобождены 2026-09-12.
; ZX ряды: 0 SHIFT Z X C V | 1 A S D F G | 2 Q W E R T | 3 1 2 3 4 5 | 4 0 9 8 7 6 | 5 P O I U Y | 6 ENTER L K J H | 7 SPACE SYM M N B
; Вектор: ряд4 @ABCDEFG(бит0..7) ряд5 HIJKLMNO ряд6 PQRSTUVW ряд7 XYZ[\\]^SPC ряд2 0-7 ряд3 8=бит0 9=бит1 ряд0 ВК=бит2

"""


VEC_ZXMAP = """vec_zxmap:		DB	vec_kbmat+7,0x00, vec_kbmat+7,0x04, vec_kbmat+7,0x01, vec_kbmat+4,0x08, vec_kbmat+6,0x40	; SHIFT(нет) Z X C V
				DB	vec_kbmat+4,0x02, vec_kbmat+6,0x08, vec_kbmat+4,0x10, vec_kbmat+4,0x40, vec_kbmat+4,0x80	; A S D F G
				DB	vec_kbmat+6,0x02, vec_kbmat+6,0x80, vec_kbmat+4,0x20, vec_kbmat+6,0x04, vec_kbmat+6,0x10	; Q W E R T
				DB	vec_kbmat+2,0x02, vec_kbmat+2,0x04, vec_kbmat+2,0x08, vec_kbmat+2,0x10, vec_kbmat+2,0x20	; 1 2 3 4 5
				DB	vec_kbmat+2,0x01, vec_kbmat+3,0x02, vec_kbmat+3,0x01, vec_kbmat+2,0x80, vec_kbmat+2,0x40	; 0 9 8 7 6
				DB	vec_kbmat+6,0x01, vec_kbmat+5,0x80, vec_kbmat+5,0x02, vec_kbmat+6,0x20, vec_kbmat+7,0x02	; P O I U Y
				DB	vec_kbmat+0,0x04, vec_kbmat+5,0x10, vec_kbmat+5,0x08, vec_kbmat+5,0x04, vec_kbmat+5,0x01	; ENTER L K J H
				DB	vec_kbmat+7,0x80, vec_kbmat+7,0x10, vec_kbmat+5,0x20, vec_kbmat+5,0x40, vec_kbmat+4,0x04	; SPACE SYM(\\) M N B
"""


VEC_RPTAB = """\t\t\t\t; Готовые ответы read_port по маске рядов (см. vec_rpbuild). 0xFF = «не считано».
\t\t\t\t; Лежит в конце нижнего региона: выравнивание на страницу нужно для ld h, high vec_rptab,
\t\t\t\t; а добивку ALIGN съедает любой код, который добавят перед таблицей.
\t\t\t\tALIGN 256
vec_rptab:\t\tDS\t\t256, 0xFF
"""

VEC_READ_PORT = """read_port:		; A = маска рядов ZX (бит i=0 -> ряд i опрошен) -> A = cpl(AND рядов) and 0x1F, как в оригинале.
				; Ряды ZX (active-low) готовит ISR (vec_zxbuild) раз в кадр. Ответы для масок, которыми игра
				; пользуется, там же посчитаны вперёд (vec_rpbuild): чтение таблицы 70 тактов против ~500
				; у общего цикла. Ответ всегда <= 0x1F, поэтому 0xFF однозначно значит «не считано» —
				; такая маска уходит на общий цикл и остаётся правильной, просто медленной.
				push	hl
				ld		l, a
				ld		h, high vec_rptab
				ld		a, (hl)
				inc		a
				jp		z, rp_slow
				dec		a
				pop		hl
				ret
rp_slow:		ld		a, l				; маска обратно
				pop		hl
				push	hl
				push	bc
				push	de
				ld		c, a
				ld		hl, vec_zxrows
				ld		b, 8
				ld		d, 0xFF				; накопитель AND
rp_row:			ld		a, c
				rra
				ld		c, a
				jp		c, rp_next			; ряд не выбран
				ld		a, d
				and		(hl)
				ld		d, a
rp_next:		inc		hl
				dec		b
				jp		nz, rp_row
				ld		a, d
				cpl
				and		0x1F
				pop		de
				pop		bc
				pop		hl
				ret
; End of function read_port
"""

VEC_CLR_BUF = """clear_scrn_buffer:								; буфер = строки 32..223 всех 32 столбцов
\t\t\t\tld      hl, vidbuf + 32
\t\t\t\txor     a
\t\t\t\tld      b, 32\t\t\t\t\t; столбцов
csb_col:\t\tld      c, 192\t\t\t\t\t; строк в столбце
csb_r:\t\t\tld      (hl), a
\t\t\t\tinc     l
\t\t\t\tdec     c
\t\t\t\tjp      nz, csb_r
\t\t\t\tinc     h
\t\t\t\tld      l, 32
\t\t\t\tdec     b
\t\t\t\tjp      nz, csb_col
\t\t\t\tret
"""

VEC_SCREEN = """
; ---- БЮДЖЕТ di-БЛОКА -------------------------------------------------------------------------
; Кадровое прерывание Вектора НЕ защёлкивается: если обратный ход пришёлся на di, кадр теряется
; (так в RTL MiSTer и в Emu80; vector06js держит запрос до ei и дефекта не показывает — проверять
; частоту ISR надо на них). Секундомер — счётчик 0 ВИ53 (звук ушёл на AY, счётчик свободен):
; boot ставит режим 0, ISR перезаряжает 0xFFFF на границе кадра. Здесь читаем остаток и, если
; блок до конца кадра не помещается, ждём прерывания (ei/halt) — тогда блок начнётся сразу после
; обратного хода и заведомо уложится. Кадр = 29952 тика (1.5 МГц), порог 0x8D00 = запас 513 тиков.
; Вход: DE = оценка длительности блока в тиках (1 тик = 2 такта CPU). Портит A, HL, флаги; BC/DE целы.
; Вызывать ТОЛЬКО при разрешённых прерываниях (иначе halt навсегда).
vec_di_budget:	xor		a
				out		(0x08), a			; команда защёлки счётчика 0
				in		a, (0x0B)
				ld		l, a
				in		a, (0x0B)
				ld		h, a				; HL = остаток кадра в тиках
				ld		a, l
				sub		e
				ld		a, h
				sbc		a, d				; остаток минус стоимость блока
				jp		c, vdb_wait			; ушли в минус — точно не успеваем
				cp		0x8D				; 0xFFFF-0x8D00 = 29439: столько тиков кадра (из 29952) уже занято
				jp		nc, vdb_ok
vdb_wait:		ei
				halt						; ждём кадрового прерывания
vdb_ok:			ret

; ---- ОГРАНИЧИТЕЛЬ ТЕМПА (потолок скорости игры) --------------------------------------------
; Итерация главного цикла не может быть короче PACE_INT + PACE_FRC/256 кадров. Дробность даёт
; Брезенхэм: каждую итерацию pace_acc += PACE_FRC, перенос -> лишний кадр. Узор шагов регулярный
; (для 2+60/256 это 2 2 2 2 3 2 2 2 2 3 ...), пачек не бывает.
; Счётчик кадров pace_el ведёт ISR; здесь ждём, пока он не дорастёт до шага, и обнуляем.
; Ожидание — только ei/halt: прерывание Вектора не защёлкивается, под di кадр теряется.
; В тяжёлой комнате (итерация длиннее шага) ожидания нет вообще, темп не трогаем.
; Вызывается из loc_B000 (начало итерации), где свободны все регистры. Портит A, B, HL, флаги.
vec_pace:		ld		a, (pace_acc)
				add		a, PACE_FRC			; дробная часть периода (1/256 кадра)
				ld		(pace_acc), a
				ld		a, PACE_INT
				adc		a, 0				; A = шаг этой итерации в кадрах
				ld		b, a
				ld		hl, pace_el
@PACE_ROUND@vp_e:			ld		a, b
				sub		(hl)				; шаг - прошло кадров
				jp		z, vp_done
				jp		c, vp_done			; прошло больше шага (тяжёлая комната) — не ждём
vp_h:			ei							; halt только при разрешённых прерываниях
				halt
				jp		vp_e
vp_done:		ld		(hl), 0				; отсчёт с этого кадра (сразу после обратного хода)
				ret

; Оценка пиксельного цикла print_sprite в тиках ВИ53 (тик = 2 такта CPU).
; ТОЧНАЯ модель строки по слотам шины (замер psbreak_vec.js подтверждает такт в такт):
;   выровненный путь  48*w + 84 такта  = 24w + 42 тика на строку (группа 48 тактов с 2026-09-12);
;   сдвинутый путь   124*w - 36 тактов = 62w - 18 тиков на строку.
; Оценка обязана быть СВЕРХУ, но не втрое: чем она больше, тем чаще vec_di_budget уводит в
; ei/halt, а halt — чистый простой (в комнате 31 он доходил до 9 % всех тактов). Берём РОВНО
; модель строки: шаг 24h и старт 66h (выровненный), шаг 62h и старт 44h (сдвинутый) — то есть
; оценка цепочки совпадает с её длительностью такт в такт, а запас сверху даёт единственная
; добавка VEC_PSB_PAD (обвязка блока между di и ei: diprol 112 тактов + возврат SP/ei).
; Прежние множители 32h*(w+2) и 64h*w завышали оценку цепочки на h*(6w+22) и h*(2w+18) тиков.
; Путь берём из иммедиата ps_shpg+1 (страница таблицы сдвига, 0 = выровненный: выровненная
; цепочка H не читает, поэтому заголовок кладёт туда 0), ширину — из уже записанного патча
; отката строки (loc_D800+1 = w-1), высоту — из kl_lines.
; Занятыми на входе остаются BC (адрес в буфере) и DE (данные спрайта) — DE сохраняем.
vec_ps_budget:	push	de					; DE = данные спрайта
				push	bc					; BC = адрес в буфере
				ld		a, (kl_lines)
				ld		h, 0
				ld		l, a				; HL = h
				add		hl, hl				; 2h
				ld		b, h
				ld		c, l				; BC = 2h
				add		hl, hl				; 4h
				add		hl, hl				; 8h
				ld		d, h
				ld		e, l				; DE = 8h
				add		hl, hl				; 16h
				ld		a, (ps_shpg+1)		; страница таблицы сдвига; 0 = выровненный путь
				or		a
				jp		nz, vpb_sh
; ВЫРОВНЕННЫЙ: шаг 24h = 16h+8h, старт (w=1) 66h = 2*24h + 16h + 2h
				push	hl					; 16h
				add		hl, de				; 24h = шаг
				ld		d, h
				ld		e, l
				add		hl, hl				; 48h
				add		hl, bc				; 50h
				pop		bc					; 16h
				add		hl, bc				; 66h = старт
				jp		vpb_go
; СДВИНУТЫЙ: шаг 62h = 64h - 2h, старт (w=1) 44h = 32h+8h+2h+2h
vpb_sh:			push	hl					; 16h
				add		hl, hl				; 32h
				add		hl, de				; 40h
				add		hl, bc				; 42h
				add		hl, bc				; 44h = старт
				ex		de, hl				; DE = старт
				pop		hl					; 16h
				add		hl, hl				; 32h
				add		hl, hl				; 64h
				ld		a, l				; 62h = 64h - 2h
				sub		c
				ld		l, a
				ld		a, h
				sbc		a, b
				ld		h, a				; HL = шаг
				ex		de, hl				; HL = старт, DE = шаг
vpb_go:			ld		a, (loc_D800+1)		; w-1
vpb_m:			or		a
				jp		z, vpb_done
				dec		a
				add		hl, de
				jp		vpb_m
vpb_done:		ld		a, h				; DE = оценка + обвязка блока (VEC_PSB_PAD*256 тиков)
				add		a, VEC_PSB_PAD
				ld		d, a
				ld		e, l
; ---- остаток кадра: секундомер (счётчик 0 ВИ53) считает ВНИЗ от 0xFFFF, перезаряд в ISR ----
vpb_est:		xor		a					; метка для distat_vec.js: здесь DE = оценка ВСЕГО остатка спрайта
				out		(0x08), a			; команда защёлки счётчика 0
				in		a, (0x0B)
				ld		l, a
				in		a, (0x0B)
				sub		0x8D				; 0xFFFF-0x8D00 = 29439 тиков кадра из 29952 (запас 513)
				ld		h, a				; HL = тиков до границы кадра
				jp		c, vpb_wait			; граница уже рядом
				ld		a, l
				sub		e
				ld		a, h
				sbc		a, d
				jp		nc, vpb_full		; весь остаток спрайта влезает в кадр
; ---- НЕ ВЛЕЗАЕТ: не ждать кадр, а нарисовать столько строк, сколько до границы помещается ----
				ld		a, (msx_vic0)
				or		a
				jp		nz, vpb_wait		; счётчик 0 ВИ53 отдан мелодии — ISR его не перезаряжает,
											;   секундомеру верить нельзя, дробить по нему нельзя
				ld		a, (ps_jrow+1)
				cp		low (ps_fix)
				jp		z, vpb_wait			; печать обрезана по столбцу 31: хвост строки досылает ps_fix,
											;   на последней строке сегмента он не отрабатывает — не дробим
				push	hl					; тиков до границы
				ld		hl, 66				; тиков на строку: выровненный 24w+42
				ld		de, 24
				ld		a, (ps_shpg+1)
				or		a
				jp		z, vpb_pr
				ld		hl, 44				; сдвинутый 62w-18
				ld		de, 62
vpb_pr:			ld		a, (loc_D800+1)		; w-1
vpb_pl:			or		a
				jp		z, vpb_pd
				dec		a
				add		hl, de
				jp		vpb_pl
vpb_pd:			ld		a, l				; DE = -(тиков на строку + 32)
				add		a, 32				; ВИТОК САМОГО СЧЁТА: строка цикла vpb_kl стоит 52 такта = 26 тиков.
				cpl							;   Он идёт ПОСЛЕ снятия остатка кадра, то есть тратит тот самый
				ld		e, a				;   остаток. Не учесть его — блок переезжает границу кадра
				ld		a, h				;   (замер: 162 потерянных обратных хода из 2276).
				adc		a, 0
				cpl
				ld		d, a
				inc		de
				pop		hl					; тиков до границы
				ld		a, h
				cp		3
				jp		c, vpb_wait			; меньше обвязки — ждём кадра
				sub		3
				ld		h, a				; минус обвязка сегмента и самого бюджета (768 тиков)
				pop		bc					; BC = адрес в буфере, C = текущая строка
				ld		a, (ps_fin+1)
				ld		(vpb_ce+1), a		; строка за последней строкой спрайта
				ld		a, c
vpb_kl:			add		hl, de				; минус строка; CY = строка влезла
				jp		nc, vpb_ke
				inc		a
vpb_ce:			cp		0
				jp		nz, vpb_kl
				jp		vpb_set				; влез весь остаток (граничный случай)
vpb_ke:			cp		c
				jp		nz, vpb_set
				push	bc					; не влезла ни одна строка — ждём кадра
				jp		vpb_wait
vpb_set:		ld		(ps_end+1), a		; конец сегмента: цепочка выйдет на этой строке
				ld		(psa_end+1), a		; у выровненной цепочки свой иммедиат конца строки
				pop		de
				ret
vpb_wait:		ei
				halt						; после обратного хода кадр целый — остаток влезает заведомо
vpb_full:		pop		bc
				ld		a, (ps_fin+1)
				ld		(ps_end+1), a		; сегмент = весь остаток спрайта
				ld		(psa_end+1), a		; у выровненной цепочки свой иммедиат конца строки
				pop		de
				ret

; ---- ДРОБЛЕНИЕ di-БЛОКА print_sprite ПО ГРАНИЦЕ КАДРА (2026-09-12) ----
; Оценка блока часто не влезала в остаток кадра, и бюджет уводил в ei/halt — целый обратный ход
; простоя (комната 39: 1186 тактов на вызов print_sprite, 3.1 % итерации). Теперь vec_ps_budget
; урезает сегмент до числа строк, помещающихся до границы: цепочка выходит на этой строке, кадр
; отдаётся (ждать уже нечего), печать продолжается с той же строки. Состояние сегмента цепочка
; оставляет сама: BC = адрес в буфере (столбец откачен, строка поднята), SP = место в данных
; спрайта, страница таблицы сдвига — в иммедиате ps_shpg+1.
ps_more:		ld		a, (ps_shpg+1)
				or		a
				jp		nz, psm_bc			; сдвинутый: адрес в буфере уже в BC
				ld		b, h
				ld		c, l				; выровненный: адрес в буфере лежит в HL
psm_bc:			ld		hl, 0
				add		hl, sp
				ld		(ps_sp), hl			; где цепочка встала в данных спрайта
				ld		sp, (tmp_SP)		; стек игры
				ei							; отдать кадр: обратный ход не потерян
				ld		a, (ps_fin+1)
				sub		c
				ld		(kl_lines), a		; строк осталось
				call	vec_ps_budget		; следующий сегмент (подождёт кадра, если надо)
				di
				ld		hl, (ps_sp)
				ld		sp, hl
				ld		a, (ps_shpg+1)
				or		a
				jp		z, ps_algo			; выровненный: HL = адрес в буфере, BC = дельта строки
				ld		h, a				; сдвинутый: страница таблицы сдвига
				jp		loc_D7AA
ps_sp:			DS		2					; указатель в данных спрайта на границе сегмента

; ---- НАТИВНЫЙ РЕНДЕР: буфер = плоскость 1 (0xA000-0xBFFF, невидима) в раскладке экрана Вектора:
; ---- адрес = база + (X/8)*256 + (32+Y), Y растёт вверх (0 = низ поля). Экран = буфер - 0x2000. ----
screen_calc_addr_de:	; BC = Y,X (пиксели) -> DE = адрес в плоскости 0. Портит только A/флаги.
				ld		a, c
				rrca
				rrca
				rrca
				and		0x1F
				add		a, 0x80
				ld		d, a
				ld		a, 32
				add		a, b
				ld		e, a
				ret

vidbuf_calc_addr_in_bc:	; BC = Y,X -> BC = адрес в буфере
				ld		a, c
				rrca
				rrca
				rrca
				and		0x1F
				add		a, high vidbuf
				ld		c, a
				ld		a, b
				add		a, 32
				ld		b, c
				ld		c, a
				ret

vidbuf_calc_addr_in_hl:	; BC = Y,X -> HL = адрес в буфере (BC сохраняется)
				ld		a, c
				rrca
				rrca
				rrca
				and		0x1F
				add		a, high vidbuf
				ld		h, a
				ld		a, b
				add		a, 32
				ld		l, a
				ret

; DE = экран, HL = буфер, B = высота (строк), C = ширина (байт). Столбцы сплошные. Нулевой размер — выход
; (иначе 256 столбцов затирают всю память: объекты у края комнаты дают ширину 0).
; СТЕКОВЫЙ БЛИТ: SP ставится на начало окна в столбце буфера, pop забирает 2 байта за 10 тактов,
; приёмник адресуется через HL — 17 тактов на байт против 39 у байтового цикла. Число pop'ов на
; столбец известно на входе, поэтому цепочка из 16 блоков развёрнута целиком и вход считается один
; раз на вызов. SP уходит в картинку => блок под di, стек игры в bts_sp.
; Окна выше 32 строк — прежним байтовым циклом, без di.
blit_to_screen:
				inc		b
				dec		b
				ret		z
				inc		c
				dec		c
				ret		z
				call	vec_clip_cols
				ret		z
				; Окно объекта, залезающее в полосу HUD (строки 0..31 = байты 32..63): после update_screen строки HUD в
				; буфере нулевые, а панель живёт только на экране — блит прямоугольника призрака/героя у нижнего края
				; стирал текст DAY. Окно, ПЕРЕСЕКАЮЩЕЕ границу HUD, обрезаем до строки 32; окна целиком внутри HUD
				; (рамка солнца, цифры, предметы) — это сами процедуры панели, их не трогаем.
				ld		a, l
				cp		64
				jp		nc, bts_yok
				add		a, b
				cp		65
				jp		c, bts_yok
				sub		64
				ld		b, a
				ld		l, 64
				ld		e, 64
bts_yok:
				ld		a, b
				cp		33
				jp		nc, bts_slow
				ld		a, l
				ld		(bts_off+1), a				; смещение окна в столбце (одно на буфер и экран)
				ld		a, h
				ld		(bts_pg+1), a				; страница буфера
				sub		d
				ld		(bts_dif+1), a				; страница экрана = страница буфера минус это
				ld		a, b
				or		a
				rra									; A = число pop, CY = нечётная высота
				ld		b, a
				ld		hl, bts_next
				jp		nc, bts_ev
				ld		hl, bts_lo
bts_ev:			ld		(bts_odd+1), hl				; хвост столбца: последний байт или пропуск
				ld		a, b						; блок цепочки — 5 байт, считаем 5*pop
				add		a, a
				add		a, a
				add		a, b
				ld		b, a
				ld		hl, bts_pend
				ld		a, l
				sub		b
				ld		l, a
				ld		a, h
				sbc		a, 0
				ld		h, a
				ld		(bts_go+1), hl				; вход в цепочку = конец минус число pop
				ld		hl, 0
				add		hl, sp
				ld		(bts_sp), hl				; стек игры
				ld		h, c						; бюджет кадра: 512 тиков ВИ53 на столбец.
				ld		l, 0						; Факт по слотам шины: столбец = 22h+128 тактов = 11h+64 тика,
				add		hl, hl						; при h <= 32 (условие быстрого пути) это <= 416 < 512.
				ld		d, h						; Прежняя плоская 1800 снята в голых T-состояниях instruction()
				ld		e, l						; и занижала реальные 2446 тиков — обратный ход терялся.
				call	vec_di_budget
				di
bts_pg:			ld		b, 0						; патч: страница буфера
bts_col:		ld		h, b
bts_off:		ld		l, 0						; патч: смещение окна
				ld		sp, hl						; SP = источник, pop идёт вверх по столбцу
				ld		a, b
bts_dif:		sub		0x20						; патч: буфер -> экран
				ld		h, a						; HL = приёмник, L то же смещение
bts_go:			jp		bts_pend					; патч: вход в цепочку
__BTS_CHAIN__
bts_pend:
bts_odd:		jp		bts_next					; патч: bts_next или bts_lo
bts_lo:			pop		de							; нечётная высота: последний байт (D не нужен)
				ld		(hl), e
bts_next:		inc		b
				ld		a, b
				cp		high vidbuf + 32			; за столбцом 31 — стоп (иначе пишем в буфер)
				jp		z, bts_done
				dec		c
				jp		nz, bts_col
bts_done:		ld		hl, (bts_sp)
				ld		sp, hl
				ei
				ret
bts_sp:			DS		2							; стек игры на время захвата SP

; окно выше 32 строк: прежний байтовый цикл, SP не трогаем
bts_slow:
bts_scol:		push	bc
				push	hl
				push	de
bts_srow:		ld		a, (hl)
				ld		(de), a
				inc		l
				inc		e
				dec		b
				jp		nz, bts_srow
				pop		de
				pop		hl
				pop		bc
				inc		h
				inc		d
				ld		a, d
				cp		0xA0				; за столбцом 31 — стоп (иначе пишем в буфер)
				ret		z
				dec		c
				jp		nz, bts_scol
				ret

; HL = буфер, B = высота, C = ширина (байт), A = значение. Нулевой размер — выход (A сохраняется).
; СТЕКОВАЯ ЗАЛИВКА: столбец буфера сплошной, поэтому SP ставится на конец окна и push кладёт
; 2 байта за 11 тактов (5.5 такта/байт против 27 у байтового цикла). Число push'ей на столбец
; известно на входе — цепочка из 16 push разворачивает цикл целиком, вход считается один раз.
; SP уходит в картинку => весь блок под di (иначе ISR положит свои регистры в буфер).
; Окна выше 32 строк (меню, крупные объекты) — прежним байтовым циклом, без di.
; vec_clip_cols: HL = адрес в буфере, C = столбцов -> C обрезан по краю буфера (страница 0xC0); Z = окно целиком
; вне буфера (например, адреса атрибутов ZX 0xCFxx: colour_panel/colour_sun_moon/атрибуты объектов зовут заливку
; с ними — в ч/б это должно быть пустой операцией, а не записью столбцами в 0xCF00.. и дальше по данным спрайтов).
; A и DE сохраняются.
vec_clip_cols:	push	af
				ld		a, h
				cp		high vidbuf
				jp		c, vcc_none
				cp		high vidbuf + 32
				jp		nc, vcc_none
				ld		a, high vidbuf + 32
				sub		h							; столбцов до края (1..32)
				cp		c
				jp		nc, vcc_ok
				ld		c, a
vcc_ok:			pop		af
				inc		c
				dec		c							; NZ: есть что делать
				ret
vcc_none:		pop		af
				ld		c, 0
				inc		c
				dec		c							; Z
				ret

vidbuf_fill_window:
				inc		b
				dec		b
				ret		z
				inc		c
				dec		c
				ret		z
				call	vec_clip_cols				; окна вне буфера (атрибуты ZX) и вылет за столбец 31
				ret		z
				ld		d, a
				ld		e, a						; значение в обоих байтах пары
				ld		a, b
				cp		33
				jp		nc, vfw_slow
				ld		a, l
				ld		(vfw_lo+1), a				; нижняя строка окна (для нечётной высоты)
				add		a, b
				ld		(vfw_end+1), a				; конец окна = SP на входе в столбец
				ld		a, h
				ld		(vfw_pg+1), a				; страница первого столбца
				ld		a, b
				or		a
				rra									; A = число push, CY = нечётная высота
				ld		b, a
				ld		hl, vfw_next
				jp		nc, vfw_ev
				ld		hl, vfw_lo
vfw_ev:			ld		(vfw_odd+1), hl				; хвост столбца: нижний байт или пропуск
				ld		hl, vfw_pend
				ld		a, l
				sub		b
				ld		l, a
				ld		a, h
				sbc		a, 0
				ld		h, a
				ld		(vfw_ent+1), hl				; вход в цепочку = конец минус число push
				ld		hl, 0
				add		hl, sp
				ld		(vfw_sp), hl				; стек игры
				push	de							; бюджет кадра: 256 тиков ВИ53 на столбец.
				ld		d, c						; Факт: столбец = 8h+112 тактов = 4h+56 тиков, при h <= 32
				ld		e, 0						; это <= 184 < 256. Плоская 900 занижала реальные 1150.
				call	vec_di_budget
				pop		de
				di
vfw_pg:			ld		b, 0						; патч: страница столбца
vfw_col:		ld		h, b
vfw_end:		ld		l, 0						; патч: конец окна
				ld		sp, hl						; push пойдёт вниз по столбцу
vfw_ent:		ld		hl, vfw_pend				; патч: вход в цепочку
				jp		(hl)
__VFW_CHAIN__
vfw_pend:
vfw_odd:		jp		vfw_next					; патч: vfw_next или vfw_lo
vfw_lo:			ld		l, 0						; патч: нижняя строка окна
				ld		h, b
				ld		(hl), d
vfw_next:		inc		b
				ld		a, b
				cp		high vidbuf + 32			; за столбцом 31 — стоп
				jp		z, vfw_done
				dec		c
				jp		nz, vfw_col
vfw_done:		ld		hl, (vfw_sp)
				ld		sp, hl
				ei
				ret

vfw_sp:			DS		2							; стек игры на время захвата SP
; окно выше 32 строк (меню, крупные объекты): прежний байтовый цикл, SP не трогаем
vfw_slow:
vfw_scol:		push	hl
				push	bc
vfw_srow:		ld		(hl), d
				inc		l
				dec		b
				jp		nz, vfw_srow
				pop		bc
				pop		hl
				inc		h
				ld		a, h
				cp		high vidbuf + 32
				ret		z
				dec		c
				jp		nz, vfw_scol
				ret

; HL = буфер, B = высота, E = ширина (байт) -> нули
vidbuf_clear_window:
				ld		c, e
				xor		a
				jp		vidbuf_fill_window

; vec_sun_save/restore: столбцы 22, 29, 30 буфера, строки 0..30 (HUD) <-> vec_suntmp (93 байта в защитной зоне)
vec_sun_save:	ld		hl, vidbuf+22*256+32
				ld		de, vec_suntmp
				ld		b, 1
				call	vsv_cols
				ld		hl, vidbuf+29*256+32
				ld		b, 2
vsv_cols:		push	bc
				ld		b, 31
vsv_r:			ld		a, (hl)
				ld		(de), a
				inc		l
				inc		de
				dec		b
				jp		nz, vsv_r
				pop		bc
				ld		l, 32
				inc		h
				dec		b
				jp		nz, vsv_cols
				ret
vec_sun_restore:
				ld		hl, vidbuf+22*256+32
				ld		de, vec_suntmp
				ld		b, 1
				call	vsr_cols
				ld		hl, vidbuf+29*256+32
				ld		b, 2
vsr_cols:		push	bc
				ld		b, 31
vsr_r:			ld		a, (de)
				ld		(hl), a
				inc		l
				inc		de
				dec		b
				jp		nz, vsr_r
				pop		bc
				ld		l, 32
				inc		h
				dec		b
				jp		nz, vsr_cols
				ret
vec_suntmp:		DS		93				; 3 столбца по 31 строке; в нижнем регионе: плоскость 2 — селектор цвета HUD

update_screen:	; весь буфер (строки 32..223 всех 32 столбцов) -> плоскость 0, буфер обнуляется (как оригинал 0xD57D)
				; СТЕКОВАЯ ВЕРСИЯ, столбец за столбцом:
				;   1) SP = начало окна в столбце буфера, pop забирает 2 байта за 10 тактов,
				;      приёмник — через HL: 24 такта на 2 байта против 98 у байтового цикла;
				;   2) после прохода SP стоит на конце окна, и 96 push HL (=0) обнуляют ровно
				;      прочитанное окно, идя вниз: 11 тактов на 2 байта.
				; 49 -> 20.6 такта на байт: 301К -> 127К на вызов (смена комнаты).
				; SP уходит в картинку => столбец идёт под di (~3.9К тактов, 1/15 кадра),
				; стек игры — в us_sp, бюджет кадра — как у blit_to_screen.
				ld		hl, 0
				add		hl, sp
				ld		(us_sp), hl					; стек игры на время захвата SP
				ld		c, high vidbuf				; C = страница столбца буфера
us_col:			ld		a, c
				ld		(us_src+1), a
				sub		high vidbuf - 0x80
				ld		(us_dst+1), a				; страница плоскости 0
				ld		de, 3300					; бюджет столбца в тиках ВИ53 (замер distat_vec: ровно 3132,
															; столбец всегда полный; прежние 2100 занижали на 49 %)
				call	vec_di_budget
				di
us_src:			ld		h, 0						; патч: страница буфера
				ld		l, 32
				ld		sp, hl						; SP = источник, pop идёт вверх по окну
us_dst:			ld		h, 0						; патч: страница экрана
				ld		l, 32						; HL = приёмник
				ld		b, 12						; 12 * 8 * 2 = 192 строки
us_row:
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				pop		de
				ld		(hl), e
				inc		l
				ld		(hl), d
				inc		l
				dec		b
				jp		nz, us_row
				ld		hl, 0						; обнуление окна: push идёт вниз от конца
				ld		b, 8						; 8 * 12 * 2 = 192
us_zero:
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				push	hl
				dec		b
				jp		nz, us_zero
				ld		hl, (us_sp)
				ld		sp, hl
				ei
				inc		c
				ld		a, c
				cp		high vidbuf + 32			; за столбцом 31 — стоп
				jp		nz, us_col
				ret
us_sp:			DS		2							; стек игры на время захвата SP
"""

KL_VEC_INPUT = """kl_vec_input:	; ТРИ РАСКЛАДКИ, читаются прямо из матрицы Вектора (vec_kbmat, active-high).
				; Таблица: младший байт адреса ряда, маска клавиш, биты user_input; 0 = конец списка.
				; Биты user_input: 0 влево, 1 вправо, 2 вперёд, 3 прыжок, 4 взять (поворотный режим,
				; в directional это «вниз»), 5 взять (directional).
				; Ряды Вектора: 0 ВК/стрелки, 2 цифры 0..7, 4 @ABCDEFG, 5 HIJKLMNO, 6 PQRSTUVW, 7 XYZ[\\\\]^ПРБ.
				ld      de, kl_in_t3
				ld      a, (user_input_method)
				and     8                                      ; 3 DIRECTIONAL CONTROL
				jp      nz, kl_in_scan
				ld      de, kl_in_t2
				ld      a, (user_input_method)
				and     4                                      ; 2 CURSOR
				jp      nz, kl_in_scan
				ld      de, kl_in_t1                            ; 1 KEYBOARD (user_input_method = 0)
kl_in_scan:		ld      c, 0
kl_in_1:		ld      a, (de)                                 ; младший байт ряда (0 = конец)
				or      a
				jp      z, kl_in_joy
				ld      l, a
				ld      h, high vec_kbmat
				inc     de
				ld      a, (de)                                 ; маска клавиш ряда
				and     (hl)
				inc     de
				ld      a, (de)                                 ; биты user_input; LDAX/INX флагов не трогают,
				inc     de                                      ; поэтому Z ещё от `and (hl)`
				jp      z, kl_in_1
				or      c
				ld      c, a
				jp      kl_in_1
kl_in_joy:		; ДЖОЙСТИК РАБОТАЕТ ВСЕГДА, ПАРАЛЛЕЛЬНО С КЛАВИАТУРОЙ (просто or к набранным битам)
				; и сводится ровно к тем же битам user_input, что клавиши ВЫБРАННОЙ раскладки.
				; Таблица: маска джойстика (active-high), биты user_input; 0 = конец.
				ld      hl, kl_joy_r                            ; режимы 1 и 2 — поворотные
				ld      a, (user_input_method)
				and     8                                      ; 3 DIRECTIONAL CONTROL
				jp      z, kl_joy_go
				ld      hl, kl_joy_d
kl_joy_go:		ld      a, (vec_joylck)                         ; замок: кнопка, которой вышли из меню,
				ld      b, a                                    ; не считается нажатой, пока её не отпустят
				ld      a, (vec_joyraw)                         ; (иначе игра начиналась бы с прыжка)
				and     b
				ld      (vec_joylck), a
				cpl
				ld      b, a
				ld      a, (vec_joyraw)
				and     b
				ld      b, a                                    ; B = действующее состояние джойстика
				jp      z, kl_in_store
kl_joy_1:		ld      a, (hl)                                 ; маска джойстика (0 = конец таблицы)
				inc     hl
				or      a
				jp      z, kl_in_store
				and     b
				ld      a, (hl)                                 ; биты user_input; MOV/INX флагов не трогают,
				inc     hl                                      ; поэтому Z ещё от `and b`
				jp      z, kl_joy_1
				or      c
				ld      c, a
				jp      kl_joy_1
kl_joy_r:		; 1 KEYBOARD и 2 CURSOR (поворотные): «вниз» не заведено
				DB      0x02, 0x01                              ; LEFT  -> поворот влево  (как O / стрелка влево)
				DB      0x01, 0x02                              ; RIGHT -> поворот вправо (как P / стрелка вправо)
				DB      0x04, 0x04                              ; UP    -> шаг вперёд     (как Q / стрелка вверх)
				DB      0x40, 0x08                              ; FIRE1 -> прыжок         (как A / пробел)
				DB      0x80, 0x10                              ; FIRE2 -> взять/бросить  (как цифры / стрелка вниз)
				DB      0
kl_joy_d:		; 3 DIRECTIONAL CONTROL: стрелки = стороны света, «взять» на бите 5
				DB      0x02, 0x01                              ; LEFT  -> влево          (как стрелка влево)
				DB      0x01, 0x02                              ; RIGHT -> вправо         (как стрелка вправо)
				DB      0x04, 0x04                              ; UP    -> вверх          (как стрелка вверх)
				DB      0x08, 0x10                              ; DOWN  -> вниз           (как стрелка вниз)
				DB      0x40, 0x08                              ; FIRE1 -> прыжок         (как пробел)
				DB      0x80, 0x20                              ; FIRE2 -> взять/бросить  (как цифры)
				DB      0
kl_in_store:	ld      a, c
				ld      (user_input), a
				ret
; End of function check_user_input
kl_joy_menu:	; МЕНЮ: направление переключает раскладку ПО ФРОНТУ. По уровню одно отклонение стика
				; пролистало бы все три за кадр-другой. LEFT/UP — назад, RIGHT/DOWN — вперёд.
				; Кнопка (старт игры) сюда не заходит: она сведена к клавише '0' прямо в рядах ZX
				; (vec_joy), и её разбирает та же строка меню, что и настоящую '0'.
				ld      hl, vec_joyprv
				ld      a, (vec_joyraw)
				and     0x0F                                    ; только направления
				ld      b, a
				xor     (hl)
				ld      (hl), b
				and     b                                      ; 1 = нажато сейчас и НЕ было в прошлый раз
				ret     z
				and     0x09                                    ; RIGHT | DOWN -> следующая раскладка
				ld      hl, old_input_method                    ; номер раскладки 0..2
				ld      a, (hl)                                 ; MOV A,M флагов не трогает — Z ещё от `and`
				jp      z, kl_jm_prev
				inc     a
				cp      3
				jp      c, kl_jm_set
				xor     a
				jp      kl_jm_set
kl_jm_prev:		or      a
				jp      nz, kl_jm_dec
				ld      a, 3
kl_jm_dec:		dec     a
kl_jm_set:		ld      (hl), a
				jp      kl_setmode                              ; раскладка + полоса подсветки
kl_in_t1:		; 1 KEYBOARD: O / P поворот, Q вперёд, A прыжок, цифры 0..7 взять
				DB      low (vec_kbmat+5), 0x80, 0x01           ; O
				DB      low (vec_kbmat+6), 0x01, 0x02           ; P
				DB      low (vec_kbmat+6), 0x02, 0x04           ; Q
				DB      low (vec_kbmat+4), 0x02, 0x08           ; A
				DB      low (vec_kbmat+2), 0xFF, 0x10           ; 0..7
				DB      0
kl_in_t2:		; 2 CURSOR: стрелки влево/вправо поворот, вверх вперёд, пробел прыжок, вниз взять
				DB      low (vec_kbmat+0), 0x10, 0x01           ; стрелка влево
				DB      low (vec_kbmat+0), 0x40, 0x02           ; стрелка вправо
				DB      low (vec_kbmat+0), 0x20, 0x04           ; стрелка вверх
				DB      low (vec_kbmat+7), 0x80, 0x08           ; пробел
				DB      low (vec_kbmat+0), 0x80, 0x10           ; стрелка вниз
				DB      0
kl_in_t3:		; 3 DIRECTIONAL CONTROL: стрелки = стороны света, пробел прыжок, цифры 0..7 взять
				DB      low (vec_kbmat+0), 0x10, 0x01           ; стрелка влево
				DB      low (vec_kbmat+0), 0x40, 0x02           ; стрелка вправо
				DB      low (vec_kbmat+0), 0x20, 0x04           ; стрелка вверх
				DB      low (vec_kbmat+0), 0x80, 0x10           ; стрелка вниз
				DB      low (vec_kbmat+7), 0x80, 0x08           ; пробел
				DB      low (vec_kbmat+2), 0xFF, 0x20           ; 0..7 (directional: бит 5)
				DB      0
				ASSERT (low vec_kbmat) != 0		; 0 — признак конца таблицы раскладки
"""


def _replace_func(src, label, end_marker, new, note=""):
    """Заменить тело от `label:` до строки end_marker (включительно) на new."""
    i = src.index(label + ":")
    j = src.index(end_marker, i) + len(end_marker)
    return src[:i] + new + src[j:]

def kl_cut_zx_attributes(src):
    """Убрать атрибутную память ZX (vec/z80vec): на Векторе её нет, а адреса zx_aram
    (0xE400-0xE7FF, считаются в attrib_calc_addr_de от high(zx_vram+0x1700)) попадают
    В ВЕРХНИЙ РЕГИОН КОДА — clr_attribute_memory затирал спрайты spr_091..spr_095
    (768 записей 0x46 на каждую очистку экрана). Вырезаем всех писателей и саму область."""
    def cut(pat, name, repl='', count=1, flags=re.M | re.S):
        nonlocal src
        src, n = re.subn(pat, repl, src, count=count, flags=flags)
        assert n == count, "kl_cut_zx_attributes: " + name + " (%d)" % n
    RET = "\t\t\t\tret\t\t\t\t\t\t; Вектор: атрибутов ZX нет\n"
    # 1. print_text: адрес атрибута и запись атрибута на символ
    cut(r'\t+push    de\r?\n\t+call    attrib_calc_addr_de\r?\n\t+ld      l, e\r?\n'
        r'\t+ld      h, d[^\n]*\r?\n\t+pop     de\r?\n', 'print_text addr')
    cut(r'\t+ld      \(hl\), a\s+; store attribute\r?\n\t+inc     l[^\n]*\r?\n', 'print_text store')
    cut(r'\t+ld      \(hl\), a\s+; store attribute\r?\n', 'print_text store last')
    # 2. display_object: заливка атрибутов под объектом панели
    cut(r'\t+ld      l, \(ix\+26\)\s+; pixel X\r?\n\t+ld      a, \(ix\+27\)[^\n]*\r?\n'
        r'\t+add     a, 23\r?\n\t+ld      h, a\r?\n\t+call    attrib_calc_addr_de\r?\n'
        r'\t+ex      de, hl\r?\n\t+ld      a, c[^\n]*\r?\n\t+ld      bc,0x0303[^\n]*\r?\n'
        r'\t+call    vidbuf_fill_window\r?\n', 'display_object fill')
    # 3. процедуры, целиком работающие с атрибутами
    cut(r'(print_days:\r?\n(?:[^\n]*\r?\n){4})\t+ld      hl, zx_aram\+0x2EF[^\n]*\r?\n'
        r'(?:\t+ld      \(hl\), 0x47[^\n]*\r?\n\t+inc     l\r?\n\t+ld      \(hl\), 0x47[^\n]*\r?\n)', 'print_days', r'\g<1>')
    cut(r'\t+ld      a, 0x47[^\n]*\r?\n\t+ld      de, zx_aram\+0x242\r?\n\t+ld      b, 2\r?\n'
        r'\t+call    fill_DE\r?\n\t+ld      de, zx_aram\+0x262\r?\n\t+ld      b, 4\r?\n\t+jp      fill_DE\r?\n',
        'print_lives_gfx', RET)
    cut(r'colour_panel:\r?\n.*?; End of function colour_panel\r?\n', 'colour_panel', 'colour_panel:\n' + RET)
    cut(r'colour_sun_moon:\r?\n.*?; End of function colour_sun_moon\r?\n', 'colour_sun_moon', 'colour_sun_moon:\n' + RET)
    cut(r'clr_attribute_memory:\r?\n.*?; End of function clr_attribute_memory\r?\n', 'clr_attribute_memory',
        'clr_attribute_memory:\n' + RET)
    cut(r'fill_attr:\r?\n.*?; End of function fill_attr\r?\n', 'fill_attr', 'fill_attr:\n' + RET)
    # 4. cycle_colours_with_sound: пробег по атрибутам — долой, звук и задержка остаются
    cut(r'(loc_C2A7:[^\n]*\r?\n)\t+ld      hl, 0x5800\r?\n\t+ld      bc, 0x300[^\n]*\r?\n'
        r'\r?\ncycle_attribute_mem:\r?\n(?:[^\n]*\r?\n)*?\t+jr      NZ, cycle_attribute_mem\r?\n',
        'cycle_attribute_mem', r'\g<1>')
    # 5. сам расчёт адреса и область
    cut(r'attrib_calc_addr_de:\r?\n.*?; End of function calc_attrib_addr\r?\n', 'attrib_calc_addr_de')
    cut(r'\t+ORG 0x5800\r?\nzx_aram:\s+DS 0x300\r?\n', 'zx_aram segment')
    assert 'zx_aram' not in src, "остались ссылки на zx_aram: " + str(re.findall(r'[^\n]*zx_aram[^\n]*', src))
    return src


def kl_vec_glue(src, translate_later):
    """Адаптация под Вектор-06Ц (общая для Z80- и i8080-целей): загрузчик, экран, клавиатура, ВИ53."""
    # заголовок после DEVICE
    # РАСКЛАДКА: код 0x0100.., стек <0x8000, ПЛОСКОСТЬ 0x8000-0x9FFF, переменные 0xA000, vidbuf 0xA600,
    # таблицы 0xBE00/0xBF00, "экран ZX" 0xCD00 (только атрибуты как мусорное ОЗУ)
    src = kl_cut_zx_attributes(src)
    # 0xC000-0xC7FF — ЗАЩИТНАЯ ЗОНА: спрайты/стирания у правого края вылезают за столбец 31 (в линейном
    # буфере ZX это заворачивало строку), плюс временный стек `ld sp,seed_1` (у ZX — буфер принтера).
    # Переменные с 0xC800.
    # 0xC000-0xC7FF — ЗАЩИТНАЯ ЗОНА: спрайты/стирания у правого края вылезают за столбец 31 (в линейном
    # буфере ZX это заворачивало строку), плюс временный стек `ld sp,seed_1` (у ZX — буфер принтера).
    # ПЕРЕМЕННЫЕ — в нижний регион (0xC000-0xDFFF = плоскость 2 остаётся под полосу HUD).
    # 0x7A80 кратно 32 (ASSERT'ы на младшие 5 бит graphic_objs_tbl не меняются), под ними
    # 256 байт стека: и главный стек (SP=clear_start), и временный `ld sp,seed_1` растут вниз
    # от clear_start. Замерено: главный стек 38 байт, временный 72 байта.
    src = re.sub(r'^\t+ORG 0x5BA0\nclear_start:', '\t\t\t\tORG %s\nclear_start:' % VEC_VARS, src, count=1, flags=re.M)
    src = src.replace("\nclear_end:\n", "\nclear_end:\n\t\t\t\tORG 0x0100\t; код и данные игры\n", 1)
    # vidbuf — отдельный сегмент; START и build_lookup_tbls (в оригинале лежат ВНУТРИ буфера и
    # затираются им) выносим на 0x7A00 (код кончается ~0x78B1, стек растёт вниз от 0x8000)
    src = re.sub(r'^\t+ORG\s+0xD900\nvidbuf:', '\t\t\t\tORG 0xA000\nvidbuf:\t\t\tDS 0x2000\n\t\t\t\tORG 0x7A00\t; START/build_lookup_tbls вне буфера', src, count=1, flags=re.M)
    # таблицы сдвига ОСТАЮТСЯ на 0xF100/0xF200: print_sprite адресует страницу таблицы константой (or 0xF0)
    src = re.sub(r'^(START:\s+ld\s+sp,\s*)0x5B9[Ff]', r'\g<1>clear_start', src, count=1, flags=re.M)
    # font как ограничитель таблицы объектов -> clear_end
    assert src.count("ld      bc, font") == 2
    src = src.replace("ld      bc, font", "ld      bc, clear_end")
    src = src.replace("ld      hl, font\n\t\t\t\tand     a", "ld      hl, clear_end\n\t\t\t\tand     a", 1)
    # ---- НАТИВНЫЙ БУФЕР (столбцовый, как экран) ----
    # print_sprite: шаг вправо = +256 (inc b вместо inc bc), переход на строку выше = B -= (w-1), C += 1
    i = src.index("print_sprite:"); j = src.index("vidbuf_calc_addr_in_bc:", i)
    ps = src[i:j]
    ps = ps.replace("inc     bc", "inc     b")
    old_adv = "loc_D7FF:\n\t\t\t\tld      a, c\n\nloc_D800:                                                       ; patched\n\t\t\t\tadd     a, 0x1E\n\t\t\t\tld      c, a\n\t\t\t\tld      a, b\n\t\t\t\tadc     a, 0\n\t\t\t\tld      b, a\n"
    assert old_adv in ps, "print_sprite row advance"
    ps = ps.replace(old_adv, "loc_D7FF:\n\t\t\t\tld      a, b\n\nloc_D800:                                                       ; patched: w-1\n\t\t\t\tsub     0x1E\n\t\t\t\tld      b, a\n\t\t\t\tinc     c\n")
    old_pat = "\t\t\t\tld      a, b\n\t\t\t\tcpl\n\t\t\t\tadd     a, 0x22 ; '\"'\n\t\t\t\tld      (loc_D800+1), a\n"
    assert old_pat in ps, "print_sprite patch calc"
    ps = ps.replace(old_pat, "\t\t\t\tld      a, b\n\t\t\t\tdec     a\t\t\t\t; столбцовый буфер: откат B на w-1\n\t\t\t\tld      (loc_D800+1), a\n")
    # прерывания включены всегда: трюк ld sp,hl в пиксельном цикле защитить di/ei
    ps = ps.replace("\t\t\t\tld      (tmp_SP), sp\n", "\t\t\t\tdi\t\t\t\t\t\t; SP уходит в данные спрайта — прерывание испортило бы их\n\t\t\t\tld      (tmp_SP), sp\n", 1)
    ps = ps.replace("\t\t\t\tld      sp, (tmp_SP)\n\t\t\t\tret\n", "\t\t\t\tld      sp, (tmp_SP)\n\t\t\t\tei\n\t\t\t\tret\n", 1)
    src = src[:i] + ps + src[j:]
    # старые vidbuf_calc_addr_in_bc/in_hl (линейные) — долой, новые в VEC_SCREEN
    src = re.sub(r'vidbuf_calc_addr_in_bc:\n.*?\n\t+ret\r?\n', '', src, count=1, flags=re.S)
    src = re.sub(r'vidbuf_calc_addr_in_hl:\n.*?\n\t+ret\r?\n', '', src, count=1, flags=re.S)
    # vidbuf_fill_window / vidbuf_clear_window (самомод. djnz jonsole) — долой
    src, n1 = re.subn(r'vidbuf_fill_window:\r?\n.*?\.fill_loop:[^\n]*\r?\n\t+ret\r?\n', '', src, count=1, flags=re.S)
    src, n2 = re.subn(r'vidbuf_clear_window:\r?\n.*?\.clear_loop:[^\n]*\r?\n\t+ret\r?\n', '', src, count=1, flags=re.S)
    assert n1 == 1 and n2 == 1, ("fill/clear", n1, n2)
    # print_8x8: строка ниже = dec l; следующий символ = +0x108
    old8 = "\t\t\t\tpush    bc\n\t\t\t\tld      bc, 0xFFE0                             ; next video/buffer line\n\t\t\t\tadd     hl, bc\n\t\t\t\tpop     bc\n\t\t\t\tdjnz    loc_BE91                                ; loop until done character\n\t\t\t\tpop     de\n\t\t\t\tld      bc, 0x101\n"
    assert old8 in src, "print_8x8"
    src = src.replace(old8, "\t\t\t\tdec     l\t\t\t\t\t\t; столбцовый буфер: строка ниже\n\t\t\t\tdjnz    loc_BE91                                ; loop until done character\n\t\t\t\tpop     de\n\t\t\t\tld      bc, 0x108\t\t\t\t; следующий символ: +столбец, назад на 8 строк\n", 1)
    # константы vidbuf+N (линейные row*32+col) -> col*256 + 32 + row
    def conv(m):
        n = int(m.group(1), 16); row, col = divmod(n, 32)
        return "vidbuf+0x%X" % (col * 256 + 32 + row)
    src, nconv = re.subn(r'vidbuf\+0x([0-9A-Fa-f]+)', conv, src)
    assert nconv == 7, nconv   # 6 разных + vidbuf+0x17 дважды
    # очистка буфера — постолбцово, ТОЛЬКО строки 32..223: строки 0..31 и 224..255 каждого столбца
    # лежат вне картинки и отданы под код (vec_pack_holes), сплошной clr_mem их бы стирал.
    # Заодно дешевле: 6144 записи вместо 8192.
    src, n_csb = re.subn(r'clear_scrn_buffer:\n\t+ld      bc, 0x1800[^\n]*\n\t+ld      hl, vidbuf[^\n]*\n\t+jr      clr_mem\n',
                         VEC_CLR_BUF, src, count=1)
    assert n_csb == 1, "clear_scrn_buffer"
    # клавиатура
    src = re.sub(r'read_port:\n.*?; End of function read_port', VEC_READ_PORT, src, count=1, flags=re.S)
    # экран: старые процедуры целиком долой (наши определены в заголовке)
    src = re.sub(r'screen_calc_addr_de:\n.*?\n\t+ret\r?\n', '', src, count=1, flags=re.S)
    src = re.sub(r'\nblit_to_screen:\n.*?\n\.djnz\s+djnz\s+\$\r?\n\t+ret\r?\n', '\n', src, count=1, flags=re.S)
    src = re.sub(r'update_screen:\n.*?; End of function update_screen', '; (update_screen -> Вектор, см. заголовок)', src, count=1, flags=re.S)
    src, n_cb = re.subn(r'(clr_bitmap_memory:[^\n]*\n\t+)ld      hl, zx_vram\n(\t+)ld      bc, 0x1800',
                        r'\g<1>ld      hl, 0x8000\t\t\t; Вектор: плоскость 0 целиком\n\g<2>ld      bc, 0x2000', src, count=1)
    assert n_cb == 1, "clr_bitmap"
    assert "ld      de, zx_vram+0x17F7" in src
    src = src.replace("ld      de, zx_vram+0x17F7", "ld      de, 0x9720\t\t\t\t; Вектор: (184,0) = столбец 23, lo 32", 1)
    # солнце/луна рисуется с X=176..225: байты вне окна рамки (столбцы 22, 29, 30) остаются в буфере и всплывают
    # при update_screen (на ZX их прятал атрибут чернила=фон). Сохраняем эти столбцы до печати и возвращаем после.
    old_sun = "\t\t\t\tcall    print_sprite                            ; display sun/moon\n"
    assert src.count(old_sun) == 1, "sun print"
    src = src.replace(old_sun, "\t\t\t\tcall    vec_sun_save\n" + old_sun + "\t\t\t\tcall    vec_sun_restore\n", 1)
    assert "ld      hl, 0x5800\n" not in src, "0x5800 (атрибуты ZX) должен был уйти в kl_cut_zx_attributes"
    # бордюр ZX (ULA) — убрать
    src = src.replace("\t\t\t\txor     a                                       ; border colour BLACK, activate MIC\n\t\t\t\tout     (0xFE), a                               ; ULA\n",
                      "\t\t\t\txor     a\n", 1)
    # zx_vram: битмап ZX на Векторе не пишется; читатели заменены выше (clr_bitmap_memory,
    # update_screen, солнце/луна), attrib_calc_addr_de вырезан вместе с атрибутами — сегмент долой
    src, _n = re.subn(r'\t+ORG 0x4000\r?\nzx_vram:\s+DS 0x1800\r?\n', '', src, count=1)
    assert _n == 1 and 'zx_vram' not in src, "zx_vram: " + str(re.findall(r'[^\n]*zx_vram[^\n]*', src))
    src, snd_cut = kl_sound_ay.apply(src)
    src = kl_cut_controls(src, vec=True)
    if kl_sound_ay.JUKEBOX_ON:
        src = kl_sound_ay.apply_jukebox(src)     # стартовый экран -> стенд приёмки на слух
    # ВВОД: три раскладки читаются ПРЯМО ИЗ МАТРИЦЫ ВЕКТОРА, а не через ряды ZX. Дорога через ZX
    # для них не годится: у Городецкого в режиме 1 клавиши O и P — повороты, а на ZX обе лежат в
    # ряду 5 и обе значат «прыжок». Ряды ZX (read_port) остаются для меню, «любой клавиши» в
    # мелодиях и паузы. Ценой этого уходят keyboard:/finished_input/kl_direct_in — 251 байт на 103.
    src = _replace_func(src, 'keyboard', '; End of function check_user_input', KL_VEC_INPUT, 'vec input')
    src = src.replace("\t\t\t\tjp      NZ, finished_input\n", "\t\t\t\tjp      NZ, kl_in_store                         ; котёл: ввод не читаем\n", 1)
    assert 'finished_input' not in src, "finished_input: остались ссылки"
    # ПАУЗА — на ВК (в оригинале пробел, но пробел стал прыжком в режимах 2 и 3). Читаем матрицу
    # Вектора напрямую: read_port для маски ряда 6 пришлось бы гонять общим циклом.
    src = _replace_func(src, 'handle_pause', '; End of function handle_pause', """handle_pause:	; ВК (ряд 0 Вектора, бит 2) — пауза; нажатие снимает её.
				; ЗАЩИТА ОРИГИНАЛА, которую порт сперва потерял: ZX ставит паузу, только если нажат
				; ПРОБЕЛ и НИ ОДНОЙ другой клавиши ТОГО ЖЕ РЯДА (`and 0x1E : ret nz`). На Векторе в ряду 0
				; вместе с ВК лежат СТРЕЛКИ (0x10/0x20/0x40/0x80) — клавиши движения раскладок 2 и 3, да и
				; «призрак» реальной матрицы при нескольких зажатых клавишах живёт там же. Ложная пауза
				; выглядит ровно как зависание: картинка замерла, на клавиши ответа нет, снимается только
				; повторным ВК. Поэтому: ровно ВК и ни одной стрелки. Ожидание отпускания и повторного
				; нажатия смотрит только на ВК — как `wait_for_space` в оригинале.
				ld      a, (vec_kbmat+0)
				and     0xF4
				cp      4
				ret     nz
hp_rel:			ld      a, (vec_kbmat+0)                        ; дождаться отпускания
				and     4
				jp      nz, hp_rel
				call    toggle_audio_hw_x24
hp_wait:		ld      a, (vec_kbmat+0)                        ; дождаться нажатия
				and     4
				jp      z, hp_wait
hp_rel2:		ld      a, (vec_kbmat+0)
				and     4
				jp      nz, hp_rel2
				jp      toggle_audio_hw_x24
; End of function handle_pause
""", 'pause')
    # цвет комнаты как на ZX: после flag_room_visited (конец build_screen_objects) — палитра по атрибуту; меню — белое
    # ПАЛИТРА КОМНАТЫ — вместе с картинкой, а не раньше неё. Прежде её ставили в хвосте
    # build_screen_objects (после flag_room_visited): к тому моменту на экране ещё СТАРАЯ комната
    # (а при старте — меню), и новый цвет успевал мигнуть на чужой картинке. Теперь — сразу после
    # update_screen в блоке перерисовки панели: этот блок идёт ровно один раз на комнату
    # (render_status_info != 0), и его update_screen и есть первый показ новой комнаты.
    # Там же кладём маску HUD: на экранах без панели её гасит print_border (см. vec_hud_off).
    src, n_hm = re.subn(r'(\t+xor     a\s+; reset flag\r?\n\t+ld      \(render_status_info\), a\r?\n)',
        r'\g<1>\t\t\t\tcall    vec_hud_mask                            ; зоны белого HUD: панель снова на экране\n', src, count=1)
    assert n_hm == 1, "hud mask hook"
    src, n_rp = re.subn(r'\t+call    update_screen\s+; copy to display\r?\n',
        '\t\t\t\tcall    update_screen                           ; copy to display\n'
        '\t\t\t\tld      a, (curr_room_attrib)                   ; палитра комнаты — вместе с картинкой\n'
        '\t\t\t\tcall    vec_set_pal\n', src, count=1)
    assert n_rp == 1, "room palette hook"
    # print_border рисуют только полноэкранные текстовые экраны (меню, game over, финал) —
    # на них панели нет, и зоны маски надо погасить, иначе рамка и текст красятся пером HUD
    src, n_ho = re.subn(r'(print_border:\r?\n)', r'\g<1>\t\t\t\tcall    vec_hud_off\n', src, count=1)
    assert n_ho == 1, "hud off hook"
    src, n_mp = re.subn(r'(do_menu_selection:\r?\n)',
        '\\g<1>\t\t\t\tld      a, 7\n\t\t\t\tcall    vec_set_pal\n'
        '\t\t\t\tld      a, 0xC0                                 ; ФОН ПОЛОСЫ ПОДСВЕТКИ — синий.\n'
        '\t\t\t\tld      (vec_pal+8), a                          ; Индексы 8..11 = p0=1, p1=0: точка есть\n'
        '\t\t\t\tld      (vec_pal+9), a                          ; на экране, но нет в буфере — так набита\n'
        '\t\t\t\tld      (vec_pal+10), a                         ; заливка полосы. 12..15 (p0=1, p1=1) —\n'
        '\t\t\t\tld      (vec_pal+11), a                         ; белые: буквы и рамка есть и там, и там.\n'
        '\t\t\t\tld      a, 1                                    ; Перезапросить палитру: vec_set_pal свой\n'
        '\t\t\t\tld      (vec_palreq), a                         ; запрос мог уже унести ISR\n', src, count=1)
    assert n_mp == 1, "menu palette hook"
    assert "out     (0xFE)" not in src, "остались OUT (0xFE)"
    src = re.sub(r'^\s*ASSERT\s+\$\s*<\s*0xD900[^\n]*$', '', src, flags=re.M)
    hdr = VEC_HEADER
    # цепочки развёрнутых стековых операций: вход в них считается по высоте окна (см. VEC_SCREEN)
    screen = VEC_SCREEN.replace("__VFW_CHAIN__\n",
        "vfw_ch:\n" + "\t\t\t\tpush\tde\n" * 16, 1)
    screen = screen.replace("__BTS_CHAIN__\n",
        "bts_ch:\n" + "\t\t\t\tpop\t\tde\n\t\t\t\tld\t\t(hl), e\n\t\t\t\tinc\t\tl\n"
        "\t\t\t\tld\t\t(hl), d\n\t\t\t\tinc\t\tl\n" * 16, 1)
    tail = VEC_TAIL + screen + kl_sound_ay.DRIVER \
        + (kl_sound_ay.jukebox_block() if kl_sound_ay.JUKEBOX_ON else '') + kl_sound_ay.data_block()
    # vec_kbzx: 64 байта (ряд Вектора*8 + бит) -> (ряд ZX<<5 | маска 5 бит), 0xFF = клавиша не отображена
    kbzx = [0xFF]*64
    zxrows = re.findall(r'DB\t((?:vec_kbmat\+\d,0x[0-9A-Fa-f]{2},?[ \t]*){5})', VEC_ZXMAP)
    assert len(zxrows) == 8, "vec_zxmap: 8 рядов"
    for zr, line in enumerate(zxrows):
        for zb, (vr, mask) in enumerate(re.findall(r'vec_kbmat\+(\d),0x([0-9A-Fa-f]{2})', line)):
            m = int(mask, 16)
            if m == 0: continue
            vb = m.bit_length() - 1; assert m == 1 << vb
            kbzx[int(vr)*8 + vb] = (zr << 5) | (0x1F & ~(1 << zb))
    tail = tail.replace("__VEC_KBZX__\n", "\t\t\t\tALIGN 8\nvec_kbzx:\t\tDB\t" + ", ".join("0x%02X" % b for b in kbzx) + "\t; (ряд Вектора*8+бит) -> ряд ZX<<5 | маска\n", 1)
    if not translate_later:   # z80vec: ячейка kl_lines нужна и Z80-сборке (заголовок print_sprite её использует)
        hdr = hdr.replace("vec_palreq:\t\tDS\t\t1\t\t\t; 1 = загрузить палитру в ближайшем VBlank\n",
            "vec_palreq:\t\tDS\t\t1\t\t\t; 1 = загрузить палитру в ближайшем VBlank\nkl_lines:\t\tDS\t\t1\t\t\t; print_sprite: счётчик строк\n")
    if translate_later:
        # ---- i8080: скретч эмуляции Z80 в нулевой странице, загрузчик на 0x0090 ----
        hdr = hdr.replace("vec_palreq:\t\tDS\t\t1\t\t\t; 1 = загрузить палитру в ближайшем VBlank\n",
            "vec_palreq:\t\tDS\t\t1\t\t\t; 1 = загрузить палитру в ближайшем VBlank\n" + SCRATCH_DEFS_VEC + kl_native.SCRATCH + kl_native_render.SCRATCH + kl_native_logic.SCRATCH)
        hdr = hdr.replace("\t\t\t\tORG\t\t0x0060\nvec_boot:", "\t\t\t\tORG\t\t0x00A0\nvec_boot:")
        src = kl_logic_fixes(src)
        # ---- print_sprite под i8080: счётчик строк в ячейке (нет AF'), точка входа = младший байт jp ----
        src, n_h = re.subn(r'(\t+ld      a, \(ix\+25\)[^\n]*\n)(?=(\t+di[^\n]*\n)?\t+ld      \(tmp_SP\), sp)', lambda m: m.group(1) + "\t\t\t\tld      (kl_lines), a\n", src, count=1)
        assert n_h == 1, "ps height"
        old_l = "loc_D7AA:\n\t\t\t\tex      af, af'\n"
        assert old_l in src, "ps loop head"
        src = src.replace(old_l, "loc_D7AA:\n", 1)
        old_e = "\t\t\t\tex      af, af'\n\t\t\t\tdec     a\n\t\t\t\tjp      NZ, loc_D7AA\n"
        assert old_e in src, "ps loop tail"
        # Конец строки: счётчик строк в ЯЧЕЙКЕ стоил 40 тактов (ld a,(nn) 16 + dec 8 + ld (nn),a 16).
        # C — младший байт адреса в буфере, то есть номер строки (32+Y), и он и так растёт на строку.
        # Сравнение с патч-иммедиатом «строка за последней» даёт то же за 16: 84 -> 60 тактов на строку.
        src = src.replace(old_e, "\t\t\t\tld      a, c\n"
                          "ps_end:\t\t\tcp      0\t\t\t\t\t; патч заголовка: строка за последней (32+Y+h)\n"
                          "ps_jrow:\t\tjp      NZ, loc_D7AA\t\t; младший байт патчит ps_cw: ps_fix, если печать обрезана по столбцу 31\n"
                          "ps_fin:\t\t\tcp      0\t\t\t\t\t; патч заголовка: НАСТОЯЩАЯ строка за последней (A ещё = C)\n"
                          "\t\t\t\tjp      NZ, ps_more\t\t\t; сегмент кончился, спрайт нет: дробление di-блока по границе кадра\n", 1)
        old_s = "\t\t\t\tneg\n\t\t\t\tadd     a, 0x50 ; 'P'\n"
        assert old_s in src, "ps shifted base"
        src = src.replace(old_s, "\t\t\t\tneg\n\t\t\t\tadd     a, 0x50 ; 'P'\n\t\t\t\tadd     a, low (loc_D7AC+3)\t\t; i8080: jp — младший байт адреса входа\n", 1)
        # ---- выровненная группа: cpl/or e/cpl/or d -> or e/xor e/or d ----
        # (A|e)^e = A & ~e — «И с дополнением» за две ALU-операции вместо трёх с cpl.
        # Результат ПОБИТНО тот же, включая 8 пар данных, где data не вложена в маску
        # (spr_012/021/028/030/032/070): последняя операция осталась `or d`, а не `xor d`.
        # 52 -> 48 тактов на байт окна; группа 8 -> 7 байт, вход в цепочку считается по 7
        # (заголовок kl_native_render: add a,a x3 + sub b = 7w).
        old_g = ("\t\t\t\tld      a, (bc)\n\t\t\t\tcpl\n\t\t\t\tor      e\n"
                 "\t\t\t\tcpl\n\t\t\t\tor      d\n")
        new_g = ("\t\t\t\tld      a, (bc)\n\t\t\t\tor      e\n\t\t\t\txor     e\n"
                 "\t\t\t\tor      d\n")
        assert src.count(old_g) == 5, ("выровненная группа", src.count(old_g))
        src = src.replace(old_g, new_g)
        old_a = "\t\t\t\tneg\n\t\t\t\tsub     6\n\t\t\t\tjr      loc_D73C\n"
        assert old_a in src, "ps aligned base"
        src = src.replace(old_a, "\t\t\t\tneg\n\t\t\t\tsub     6\n\t\t\t\tadd     a, low (loc_D7AC+3)\t\t; i8080: jp — младший байт входа (+1: перед loc_D7AC убран ex af,af')\n\t\t\t\tjr      loc_D73C\n", 1)
        i = src.index("\t\t\t\tjr      loc_D73C\n", src.index("print_sprite:")) + len("\t\t\t\tjr      loc_D73C\n")
        j = src.index("\t\t\t\tpop     de\n", i)
        # развёрнутые цепочки пиксельного цикла: все точки входа обязаны лежать в одной странице
        # (патчится только младший байт `jp` в loc_D7AC). ALIGN 256 на месте съедал 244 байта
        # выравнивания — вместо него цепочки уезжают в НАЧАЛО верхнего региона (0xE000/0xD400 —
        # граница страницы по построению). Вход в блок только через `jp loc_D7AA`, провала нет.
        k = src.index("\t\t\t\tld      sp, (tmp_SP)\n", j)
        k = src.index("\t\t\t\tret\n", k) + len("\t\t\t\tret\n")
        # ---- ВЫРОВНЕННАЯ ЦЕПОЧКА НА HL: столбец = inc h, конец строки = ОДИН add hl,bc ----
        # Конец строки стоил 80 тактов на обе цепочки: откат столбца (ld a,b/sub w-1/ld b,a = 24),
        # строка и тест (inc c/ld a,c/cp/jp nz = 36), холостой ld a,(bc) у loc_D7AA (8, нужен только
        # сдвинутому) и jp в цепочку (12). У выровненного пути HL СВОБОДЕН (таблицы сдвига он не
        # читает), поэтому адрес в буфере кладём в HL, а в BC — дельту строки (B = 1-w', C = 1):
        # `add hl, bc` делает откат столбца И переход на строку разом за 12 тактов, строка теперь
        # в L, а переход идёт прямо на вход цепочки. 80 -> 40 тактов на строку.
        # Сдвинутому пути так нельзя: у него HL — указатель в таблицу сдвига, а `and (hl)` —
        # единственная форма «ALU с памятью» у i8080.
        i_al = src.index("\t\t\t\tpop     de\n", j)
        i_ae = src.index("\t\t\t\tjp      loc_D7FF\n", i_al)
        al = src[i_al:i_ae]
        assert al.count("ld      a, (bc)") == 5 and al.count("ld      (bc), a") == 5, "выровненная цепочка"
        al = al.replace("ld      a, (bc)", "ld      a, (hl)").replace("ld      (bc), a", "ld      (hl), a")
        assert al.count("inc     b\n") == 4, "inc b выровненной цепочки"
        al = al.replace("inc     b\n", "inc     h\n")
        al += ("psa_row:\t\tadd     hl, bc\t\t\t\t; B = 1-w', C = 1: столбец назад и строка вверх за один dad\n"
               "\t\t\t\tld      a, l\t\t\t\t; строка = младший байт адреса в буфере\n"
               "psa_end:\t\tcp      0\t\t\t\t\t; патч vec_ps_budget: строка за последней в СЕГМЕНТЕ\n"
               "psa_jrow:\t\tjp      NZ, psa_row\t\t; ПАТЧ МЛАДШЕГО БАЙТА: вход в цепочку (ps_cw: psa_fix, если печать обрезана).\n"\
               "\t\t\t\t\t\t\t\t\t\t; Адрес-заглушка обязан лежать в СТРАНИЦЕ ЦЕПОЧКИ: патчится только младший байт\n"
               "\t\t\t\tjp      ps_fin\t\t\t\t; сегмент кончился — общий хвост (A ещё = строка)\n"
               "; печать обрезана по столбцу 31 (выровненный путь): HL занят адресом в буфере, поэтому через DE\n"
               "psa_fix:\t\tex      de, hl\n"
               "psa_fsp:\t\tld      hl, 0\t\t\t\t; патч ps_cw: 2*(w - w')\n"
               "\t\t\t\tadd     hl, sp\n"
               "\t\t\t\tld      sp, hl\n"
               "\t\t\t\tex      de, hl\n"
               "psa_fixj:\t\tjp      psa_row\t\t\t; патч младшего байта: вход в цепочку (сюда же первый вход из заголовка)\n")
        src = src[:i_al] + al + src[i_ae + len("\t\t\t\tjp      loc_D7FF\n"):]
        k = src.index("\t\t\t\tld      sp, (tmp_SP)\n", j)
        k = src.index("\t\t\t\tret\n", k) + len("\t\t\t\tret\n")
        ps_chain = ("; ---- развёрнутые цепочки print_sprite: начало верхнего региона = граница страницы ----\n"
                    + src[j:k]
                    + "; печать обрезана по столбцу 31: цепочка не дочитала хвост строки спрайта — двигаем SP за него\n"
                      "ps_fix:\t\t\tld      hl, 0\t\t\t\t; патч ps_cw: 2*(w - w')\n"
                      "\t\t\t\tadd     hl, sp\n\t\t\t\tld      sp, hl\n\t\t\t\tjp      loc_D7AA\n"
                      "\t\t\t\tASSERT high (ps_fix) == high (loc_D7AA)\t; ps_cw патчит только младший байт перехода\n")
        src = src[:j] + src[k:]
        # ---- разрез памяти: цепочки print_sprite + хвост спрайтов (spr_064..) + хелперы + START в верхний регион ----
        assert "\n%s:" % VEC_SPLIT in src
        src = src.replace("\n%s:" % VEC_SPLIT, "\n\t\t\t\tORG %#06x\t\t\t; верхний регион: цепочки print_sprite, хвост спрайтов, START\n" % VEC_UPPER
                          + ps_chain + "%s:" % VEC_SPLIT, 1)
        # блок Вектора (без ORG 0x7B00) — в нижний регион перед разрезом; START — за vidbuf, но в верхнем регионе
        tail = tail.replace("\t\t\t\tORG\t\t0x7B00\t\t\t; блок Вектора: клавиатура, ВИ53, экран (после START на 0x7A00, до стека)\n", "")
        tail = tail + _LDIR_HELPERS
        src = src.replace("\n\t\t\t\tORG %#06x\t\t\t; верхний регион" % VEC_UPPER, "\n" + tail + "\n\t\t\t\tORG %#06x\t\t\t; верхний регион" % VEC_UPPER, 1)
        tail = ""
        src = src.replace("\t\t\t\tORG 0xA000\nvidbuf:\t\t\tDS 0x2000\n\t\t\t\tORG 0x7A00\t; START/build_lookup_tbls вне буфера", "vidbuf:\t\t\tequ 0xA000\t\t; плоскость 1; START/build_lookup_tbls — продолжение верхнего региона", 1)
        assert "vidbuf:\t\t\tequ 0xA000" in src
        # атрибуты ZX: только 1К реального ОЗУ на 0xCE00 (zx_vram=0xB700 — битмап не пишется), очередь звука 0xD200
        src = src.replace("\t\t\t\tORG 0xCE00\nzx_vram:", "\t\t\t\tORG 0xB700\nzx_vram:", 1)
        src = src.replace("\t\t\t\tORG 0xE600\nzx_aram:", "\t\t\t\tORG 0xCF00\nzx_aram:", 1)
    # заголовок (и наши экранные процедуры) — ПОСЛЕ удаления старых, иначе регулярки цепляют новые
    src = src.replace("\tDEVICE ZXSPECTRUM48\n", "\tDEVICE ZXSPECTRUM48\n" + hdr + "\n", 1)
    src = re.sub(r'^(\s*SAVESNA)', tail.replace("\\", "\\\\") + r"\n\g<1>", src, count=1, flags=re.M)
    return src


# ---------------------------------------------------------------------------
# ДЫРЫ ВНЕ КАРТИНКИ (цель vec).
# Картинка занимает строки 32..223 каждого из 32 столбцов плоскости (адрес = база + столбец*256
# + 32 + Y). Строки 224..255 столбца c и 0..31 столбца c+1 лежат в памяти подряд — это 64 байта,
# в которые не пишет ни рендер, ни очистка буфера (замер tools/holescan_vec.js: за 150 итераций
# в плоскостях 0 и 1 тронуты ровно строки 32..223). Плоскость 1 (буфер) и плоскость 2 (селектор
# белого HUD) дают по 2048 байт таких дыр. Туда уезжают самостоятельные процедуры нижнего региона:
# место внизу освобождается под хвост спрайтов, а темп не меняется — у i8080 нет ни кэша, ни
# штрафа за выравнивание, адрес процедуры на её скорость не влияет.
# ---------------------------------------------------------------------------
_R8 = {'a', 'b', 'c', 'd', 'e', 'h', 'l', '(hl)'}
_RR16 = {'bc', 'de', 'hl', 'sp', 'af'}


def _i8080_len(t):
    """Длина инструкции i8080 в байтах; None — не инструкция.
    Сверено с листингом сборки: 7685 инструкций, 0 расхождений."""
    p = t.split(None, 1)
    mn = p[0].lower()
    ops = [o.strip().lower() for o in p[1].split(',')] if len(p) > 1 else []
    if mn in ('nop', 'halt', 'di', 'ei', 'cpl', 'daa', 'scf', 'ccf', 'rlca', 'rrca', 'rla', 'rra',
              'ex', 'ret', 'rst', 'push', 'pop', 'inc', 'dec'):
        return 1
    if mn == 'call':
        return 3
    if mn == 'jp':
        return 1 if ops and ops[-1] == '(hl)' else 3
    if mn in ('in', 'out'):
        return 2
    if mn == 'add' and len(ops) == 2 and ops[0] == 'hl':
        return 1
    if mn in ('add', 'adc', 'sub', 'sbc', 'and', 'or', 'xor', 'cp'):
        return 1 if ops[-1] in _R8 else 2
    if mn == 'ld':
        if len(ops) != 2:
            return None
        d, s = ops
        if d == 'sp' and s == 'hl':
            return 1
        if d in _RR16 or (s in _RR16 and d.startswith('(')):
            return 3
        if (d.startswith('(') and d not in ('(hl)', '(bc)', '(de)')) or \
           (s.startswith('(') and s not in ('(hl)', '(bc)', '(de)')):
            return 3
        if d in ('(bc)', '(de)') or s in ('(bc)', '(de)'):
            return 1
        return 1 if s in _R8 else 2
    return None


_H_LBL = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')
_H_HARD = re.compile(r'(?i)^(ORG|DEVICE|SAVEBIN|SAVESNA|ASSERT|ALIGN|IF|ELSE|ENDIF|INCBIN|DS|DB|DW|EQU)\b')
# halt НЕ прерывает поток: после прерывания исполнение идёт дальше (vec_di_budget: ei/halt/ret)
_H_TERM = re.compile(r'(?i)^(ret$|jp\s+(?!nz\b|z\b|nc\b|c\b|po\b|pe\b|p\b|m\b))')


def vec_holes(base, pages=32):
    """Дыры одной плоскости: (адрес, размер). Строки 224..255 столбца c + 0..31 столбца c+1."""
    h = [(base, 32)]
    h += [(base + c * 256 + 224, 64) for c in range(pages - 1)]
    h.append((base + (pages - 1) * 256 + 224, 32))
    return h


def vec_pack_holes(src, holes, need=-1, exclude=()):
    """Увезти мелкие самостоятельные процедуры нижнего региона в дыры вне картинки.
    Движим блок «метка: ... ret/jp», если: весь блок — инструкции; кончается безусловной передачей
    управления (в него нельзя провалиться из него же); предыдущий блок тоже кончается безусловно и
    непуст (иначе метка-синоним или провал сверху); перед блоком нет ORG (иначе следующий блок
    уедет на его адрес). Ссылки на метки символические, поэтому переезд ничего не ломает."""
    if not holes or need == 0:
        return src, 0, 0
    lines = src.split('\n')
    end = next(i for i, l in enumerate(lines) if 'ASSERT $ <=' in l and 'нижний регион' in l)
    blocks, cur, org, prev_org = [], None, None, False
    for i in range(end):
        raw = lines[i]
        m = _H_LBL.match(raw)
        body = raw[m.end():] if m else raw
        code = ' '.join(body.split(';')[0].split())   # пробелы схлопнуть: иначе `jp\s+` в _H_TERM отступает назад и «jp nz, x» проходит за безусловный
        hard = bool(code) and bool(_H_HARD.match(code))
        if m:
            cur = {'name': m.group(1), 'i0': i, 'iend': i + 1, 'size': 0, 'ok': True,
                   'org': org, 'after_org': prev_org, 'last': ''}
            blocks.append(cur)
        if code:
            prev_org = code.upper().startswith('ORG')
            if prev_org:
                try:
                    org = int(code.split()[1], 0)
                except ValueError:
                    org = None
            if cur is not None:
                if hard:
                    cur['ok'] = False
                else:
                    n = _i8080_len(code)
                    if n is None:
                        cur['ok'] = False
                    else:
                        cur['size'] += n
                        cur['last'] = code
                        cur['iend'] = i + 1
    maxh = max(sz for _, sz in holes)
    cand = []
    for k, b in enumerate(blocks):
        pv = blocks[k - 1] if k else None
        if not (b['ok'] and 0 < b['size'] <= maxh) or b['after_org'] or b['name'] in exclude:
            continue
        if b['org'] is None or not (0x0100 <= b['org'] < 0x8000):
            continue
        if not _H_TERM.match(b['last']):
            continue
        # в блок проваливаются сверху: двигать можно, оставив на его месте jp <метка> (3 Б).
        # Чистый выигрыш = size - 3, поэтому мелочь не трогаем.
        b['stub'] = pv is None or pv['size'] == 0 or not (pv['ok'] and _H_TERM.match(pv['last']))
        if b['stub'] and b['size'] < 16:
            continue
        cand.append(b)
    cand.sort(key=lambda b: (-b['size'], b['name']))
    free = [[a, sz] for a, sz in holes]
    placed, moved = [[] for _ in holes], 0
    for b in cand:
        if need >= 0 and moved >= need:
            break
        for j, (a, sz) in enumerate(free):
            if sz >= b['size']:
                placed[j].append(b)
                free[j][0] += b['size']
                free[j][1] -= b['size']
                moved += b['size'] - (3 if b.get('stub') else 0)
                break
    if not moved:
        return src, 0, 0
    drop, stubs = set(), {}
    for lst in placed:
        for b in lst:
            drop.update(range(b['i0'], b['iend']))
            if b.get('stub'):                      # оставить переход на уехавший блок
                stubs[b['i0']] = '\t\t\t\tjp\t\t%s\t\t\t; блок уехал в дыру' % b['name']
    out = ['\n; ==== дыры вне картинки (строки 224..255/0..31 столбцов): мелкие процедуры нижнего региона ====']
    for j, (a, sz) in enumerate(holes):
        if not placed[j]:
            continue
        out.append('\t\t\t\tORG %#06x' % a)
        for b in placed[j]:
            out += [lines[i] for i in range(b['i0'], b['iend'])]
        out.append('\t\t\t\tASSERT $ <= %#06x\t\t; дыра %#06x (%d Б)' % (a + sz, a, sz))
    kept = [stubs[i] if i in stubs else l for i, l in enumerate(lines) if i not in drop or i in stubs]
    ei = next(i for i, l in enumerate(kept) if 'ASSERT $ <=' in l and 'нижний регион' in l)
    return '\n'.join(kept[:ei + 1] + out + kept[ei + 1:]), moved, sum(len(x) for x in placed)



VEC_HOLES = [h for b in VEC_HOLE_PLANES for h in vec_holes(b)]


def main():
    argv = sys.argv[1:]
    target = 'lockstep'
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--target':
            target = argv[i + 1]; i += 2; continue
        if a.startswith('--target='):
            target = a.split('=', 1)[1]; i += 1; continue
        if a == '--jukebox':
            kl_sound_ay.JUKEBOX_ON = True; i += 1; continue
        pos.append(a); i += 1
    inp, outp = pos[0], pos[1]
    src = open(inp, encoding='latin-1').read()
    if target == 'vec':
        src = kl_vec_glue(src, translate_later=True)
        src = kl_no_delay(src)
        src, pace_desc = kl_pace(src)
        src = kl_native.apply(src)
        src = kl_native_render.apply(src)
        src = kl_native_logic.apply(src, attrs_cut=True)   # атрибуты ZX вырезаны -> второй набор регистров не нужен
        src, exx_pairs = _classify_exx(src)
        src = translate(src)
        helpers = ix_helpers_text()
        # хелперы — в нижний регион (после блока Вектора, до стека 0x8000): в верхнем они упирались в таблицы 0xF100,
        # которые build_lookup_tbls перезаписывает (IY-хелперы стирались -> старт игры уходил в мусор)
        src, n = re.subn(r'(^\t+ORG %#06x\t+; верхний регион)' % VEC_UPPER, lambda m: helpers + VEC_RPTAB + "\t\t\t\tASSERT $ <= %#06x\t\t; нижний регион: до стека (128 Б) и переменных на %#06x\n" % (VEC_CODE_TOP, VEC_VARS) + m.group(1), src, count=1, flags=re.M)
        assert n == 1, "место для IX-хелперов (vec)"
        src, n = re.subn(r'(^\t+ORG\s+0xF100\nreverse_bits_table:)', lambda m: "\t\t\t\tASSERT $ <= 0xF100\t\t; верхний регион: до таблиц сдвига\n" + m.group(1), src, count=1, flags=re.M)
        assert n == 1, "ASSERT верхнего региона"
        # Второй набор регистров в цели vec больше не используется ни разу (последние exx ушли
        # вместе с атрибутами ZX в печати строк) — ячейки обмена мертвы, а scratch до 0x00A0 тесен.
        for cell in ('z80_bc_alt', 'z80_de_alt', 'z80_hl_alt', 'z80_af_alt'):
            if len(re.findall(r'\b' + cell + r'\b', src)) == 1:      # только объявление
                src, n = re.subn(r'(?m)^' + cell + r':[ \t]*DS[ \t]+2[^\n]*\n', '', src, count=1)
                assert n == 1, 'снятие мёртвой ячейки ' + cell
        src, moved, nblk = vec_pack_holes(src, VEC_HOLES, VEC_HOLE_BYTES, VEC_HOLE_SKIP)
        rom = 'build/knightlore_jukebox.r0m' if kl_sound_ay.JUKEBOX_ON else 'build/knightlore_vec.r0m'
        src = re.sub(r'^\s*SAVESNA[^\n]*$', '\t\t\t\tSAVEBIN "%s", 0, 0x10000' % rom, src, flags=re.M)
        open(outp, 'w', encoding='utf-8').write(src)
        print(f"vector glue + translate (i8080) {inp} -> {outp}; exx-B pairs={exx_pairs}; IX-хелперов {len(_ix_used)}; "
              f"в дыры вне картинки увезено {moved} Б ({nblk} процедур)"
              f"; ограничитель темпа: {pace_desc}")
        return
    if target == 'z80vec':
        src = kl_vec_glue(src, translate_later=False)
        src = kl_no_delay(src)
        src = re.sub(r'^\s*SAVESNA[^\n]*$', '\t\t\t\tSAVEBIN "build/knightlore_z80.r0m", 0, 0x10000', src, flags=re.M)
        open(outp, 'w', encoding='utf-8').write(src)
        print(f"vector glue (Z80, без трансляции) {inp} -> {outp}")
        return
    src = kl_glue(src, target)
    src = kl_no_delay(src)
    src = kl_native.apply(src)
    src = kl_native_render.apply(src)
    src = kl_native_logic.apply(src)
    src, exx_pairs = _classify_exx(src)
    src = translate(src)
    helpers = ix_helpers_text()
    if helpers:
        src, n = re.subn(r'(^\t+ORG 0x[0-9A-Fa-f]+\nvidbuf:)', lambda m: helpers + m.group(1), src, count=1, flags=re.M)
        assert n == 1, "место для IX-хелперов"
    print(f"IX-хелперов: {len(_ix_used)}")
    open(outp, 'w', encoding='utf-8').write(src)
    print(f"translated {inp} -> {outp} (target={target}); exx-B push/pop pairs={exx_pairs}")

if __name__ == '__main__':
    main()
