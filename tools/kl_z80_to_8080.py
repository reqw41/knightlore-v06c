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
_IX_INLINE_FUNCS = {'print_sprite', 'wipe_next_object', 'is_near_to', 'is_object_not_ignored', 'adj_2_4_hflip', 'chk_plyr_spec_near_arch', 'upd_2_4', 'add_dXYZ', 'render_dynamic_objects'}
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


def kl_cut_controls(src):
    """Борис 2026-09-10: варианты ввода ZX (Kempston/курсор/Interface II) вырезаны — на Векторе только клавиатура.
    Оставлены ДВА режима управления (Борис: «одно классическое, другое прямое»): бит 3 user_input_method =
    directional; в оригинале он работал только с джойстиком (проверка `and 6`), здесь — с клавиатурой.
    Меню: «1 CLASSIC» / «1 DIRECT» (клавиша 1 переключает, строка показывает текущий режим), «0 START GAME»."""
    def cut(pat, name, repl='', flags=re.S, count=1):
        nonlocal src
        src, n = re.subn(pat, repl, src, count=count, flags=flags)
        assert n == count, "kl_cut_controls: " + name
    # меню: 4 строки (заголовок, режим управления, 0 START GAME, (c) 1984)
    cut(r'menu_colours:   DB 0x43, 0xC4, 0x44, 0x44, 0x44, 0x45, 0x47, 0x47', 'menu_colours', 'menu_colours:   DB 0x43, 0x44, 0x47, 0x47')
    cut(r'menu_xy:        DB 88, 159, 48, 143, 48, 127, 48, 111, 48, 95, 48, 79\r?\n\t+DB 48, 63, 80, 39', 'menu_xy', 'menu_xy:        DB 88, 159, 72, 103, 48, 63, 80, 39')
    # строка режима: слот 9 байт (заполняется из kl_ctl_classic / kl_ctl_direct), токены: цифра = значение, A=0xA.., пробел 0x26, бит 7 = конец
    cut(r'; "1 KEYBOARD"\r?\n.*?(?=; "0 START GAME")', 'menu_text 1..5',
        '; режим управления (слот, копируется из kl_ctl_*)\nkl_ctl_slot:\t\tDB 1, 0x26, 0xC, 0x15, 0xA, 0x1C, 0x1C, 0x12, 0x8C\n')
    # исходные строки — ВНЕ списка меню (display_text_list печатает список подряд)
    cut(r'(complete_colours:)', 'ctl strings', '; "1 CLASSIC" / "1 DIRECT "\nkl_ctl_classic:\t\tDB 1, 0x26, 0xC, 0x15, 0xA, 0x1C, 0x1C, 0x12, 0x8C\nkl_ctl_direct:\t\tDB 1, 0x26, 0xD, 0x12, 0x1B, 0xE, 0xC, 0x1D, 0xA6\n\\g<1>')
    cut(r'(display_menu:\r?\n(?:[^\n]*\n){4}\t+ld      b, )8(\s+; 8 lines to display)', 'display_menu b', r'\g<1>4\g<2>')
    cut(r'(\t+ld      hl, menu_colours\r?\n\t+ld      b, )8(\s+; # menu entries)', 'do_menu_selection b', r'\g<1>4\g<2>')
    # цикл меню: вместо выбора 1..5 — переключатель режима клавишей 1 (с антидребезгом), затем проверка «0»
    cut(r"\t+ld      a, 0xF7 ; '[^\n]*; 1,2,3,4,5\r?\n.*?(?=\t+ld      a, 0xEF ; '[^\n]*; 0,9,8,7,6\r?\n\t+call    read_port\r?\n\t+bit     0, a\s+; '0' \(Start Game\)\?)", 'menu 1..5',
        "\t\t\t\tld      a, 0xF7                                 ; ряд 1..5\n"
        "\t\t\t\tcall    read_port\n"
        "\t\t\t\tand     1                                      ; '1' — переключить режим управления\n"
        "\t\t\t\tld      hl, debounce_5\n"
        "\t\t\t\tjp      z, kl_ctl_rel\n"
        "\t\t\t\tld      a, (hl)\n"
        "\t\t\t\tor      a\n"
        "\t\t\t\tjp      nz, kl_ctl_done                         ; ещё не отпущена\n"
        "\t\t\t\tld      (hl), 1\n"
        "\t\t\t\tld      a, (user_input_method)\n"
        "\t\t\t\txor     8\n"
        "\t\t\t\tld      (user_input_method), a\n"
        "\t\t\t\tcall    kl_ctl_show\n"
        "\t\t\t\tcall    display_menu                            ; перерисовать текст в буфер\n"
        "\t\t\t\tld      hl, vidbuf + 9*256 + 32 + 96            ; окно строки режима: столбцы 9..17, строки 96..111\n"
        "\t\t\t\tld      de, 0x8000 + 9*256 + 32 + 96\n"
        "\t\t\t\tld      bc, 0x1009                              ; 16 строк, 9 байт\n"
        "\t\t\t\tcall    blit_to_screen\n"
        "\t\t\t\tjp      kl_ctl_done\n"
        "kl_ctl_rel:\t\tld      (hl), 0\n"
        "kl_ctl_done:\n")
    cut(r'\t+call    flash_menu\r?\n', 'call flash_menu', count=2)
    cut(r'\t+jp      menu_loop\r?\n; -+\r?\n(?:\r?\n)*clr_debounce:\r?\n\t+res     0, \(hl\)\r?\n\t+jr      check_for_start_game[^\n]*\r?\n', 'clr_debounce', '\t\t\t\tjp      menu_loop\n')
    cut(r'flash_menu:\r?\n.*?; End of function flash_menu\r?\n', 'flash_menu',
        "kl_ctl_show:\t\t; строка меню по режиму: (user_input_method & 8) ? DIRECT : CLASSIC -> kl_ctl_slot\n"
        "\t\t\t\tld      hl, kl_ctl_classic\n"
        "\t\t\t\tld      a, (user_input_method)\n"
        "\t\t\t\tand     8\n"
        "\t\t\t\tjp      z, kl_cs_1\n"
        "\t\t\t\tld      hl, kl_ctl_direct\n"
        "kl_cs_1:\t\tld      de, kl_ctl_slot\n"
        "\t\t\t\tld      b, 9\n"
        "kl_cs_2:\t\tld      a, (hl)\n"
        "\t\t\t\tld      (de), a\n"
        "\t\t\t\tinc     hl\n"
        "\t\t\t\tinc     de\n"
        "\t\t\t\tdec     b\n"
        "\t\t\t\tjp      nz, kl_cs_2\n"
        "\t\t\t\tret\n")
    cut(r'toggle_selected:\r?\n.*?; End of function toggle_selected\r?\n', 'toggle_selected')
    # при входе в меню — показать текущий режим
    cut(r'(do_menu_selection:\r?\n)', 'do_menu_selection entry', r'\g<1>\t\t\t\tcall    kl_ctl_show\n')
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
				call	msx_snd_probe			; есть AY -> спросим игрока в меню; нет -> молча ВИ53
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
				call	vec_hud_mask			; маска HUD: зоны обнуляются силуэтом пустого экрана
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

; vec_hud_mask: строит маску HUD в плоскости 2 по таблице зон. Маска СТАТИЧНА: её кладут один раз
; в vec_boot (после очистки плоскости 0) и больше не трогают — ни перерисовка цифр, ни ход знака
; солнца/луны её не меняют. Так цвет элемента не может отстать от картинки: любой пиксель, попавший
; внутрь прямоугольника зоны, красится пером HUD в тот же кадр, кто бы его ни нарисовал.
; Запись зоны — 5 байт: страница столбца в плоскости 2 (0xC0 + столбец), сколько столбцов, первая
; строка, сколько строк, ЗНАЧЕНИЕ (0xFF — заливка; 0 — силуэт: байт картинки из плоскости 0, оставлен
; на случай зоны, которую нельзя обвести прямоугольником). Конец таблицы — нулевая страница.
; Портит A, BC, DE, HL.
vec_hud_mask:	IF VEC_HUD_WHITE == 0
				ret
				ENDIF
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
vhm_put:		ld		(de), a
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
				ret
vec_arrzx:		DB		0x10, low (vec_zxrows+0), 0xFD	; LT -> Z  (ряд ZX 0, бит 1)
				DB		0x40, low (vec_zxrows+0), 0xFB	; RT -> X  (ряд ZX 0, бит 2)
				DB		0x20, low (vec_zxrows+1), 0xFE	; UP -> A  (ряд ZX 1, бит 0)
				DB		0x80, low (vec_zxrows+2), 0xFE	; DN -> Q  (ряд ZX 2, бит 0)
__VEC_KBZX__
; ---- таблица ZX-ряд -> клавиши Вектора: 8 рядов x 5 записей (адрес ячейки vec_kbmat, маска) ----
; ZX ряды: 0 SHIFT Z X C V | 1 A S D F G | 2 Q W E R T | 3 1 2 3 4 5 | 4 0 9 8 7 6 | 5 P O I U Y | 6 ENTER L K J H | 7 SPACE SYM M N B
; Вектор: ряд4 @ABCDEFG(бит0..7) ряд5 HIJKLMNO ряд6 PQRSTUVW ряд7 XYZ[\\]^SPC ряд2 0-7 ряд3 8=бит0 9=бит1 ряд0 ВК=бит2
vec_zxmap:		DB	vec_kbmat+7,0x00, vec_kbmat+7,0x04, vec_kbmat+7,0x01, vec_kbmat+4,0x08, vec_kbmat+6,0x40	; SHIFT(нет) Z X C V
				DB	vec_kbmat+4,0x02, vec_kbmat+6,0x08, vec_kbmat+4,0x10, vec_kbmat+4,0x40, vec_kbmat+4,0x80	; A S D F G
				DB	vec_kbmat+6,0x02, vec_kbmat+6,0x80, vec_kbmat+4,0x20, vec_kbmat+6,0x04, vec_kbmat+6,0x10	; Q W E R T
				DB	vec_kbmat+2,0x02, vec_kbmat+2,0x04, vec_kbmat+2,0x08, vec_kbmat+2,0x10, vec_kbmat+2,0x20	; 1 2 3 4 5
				DB	vec_kbmat+2,0x01, vec_kbmat+3,0x02, vec_kbmat+3,0x01, vec_kbmat+2,0x80, vec_kbmat+2,0x40	; 0 9 8 7 6
				DB	vec_kbmat+6,0x01, vec_kbmat+5,0x80, vec_kbmat+5,0x02, vec_kbmat+6,0x20, vec_kbmat+7,0x02	; P O I U Y
				DB	vec_kbmat+0,0x04, vec_kbmat+5,0x10, vec_kbmat+5,0x08, vec_kbmat+5,0x04, vec_kbmat+5,0x01	; ENTER L K J H
				DB	vec_kbmat+7,0x80, vec_kbmat+7,0x10, vec_kbmat+5,0x20, vec_kbmat+5,0x40, vec_kbmat+4,0x04	; SPACE SYM(\\) M N B

"""


VEC_READ_PORT = """read_port:		; A = маска рядов ZX (бит i=0 -> ряд i опрошен) -> A = cpl(AND рядов) and 0x1F, как в оригинале.
				; Ряды ZX (active-low) готовит ISR (vec_zxbuild) раз в кадр; здесь только AND выбранных. ~300 тактов.
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
;   выровненный путь  52*w + 84 такта  = 26w + 42 тика на строку;
;   сдвинутый путь   124*w - 36 тактов = 62w - 18 тиков на строку.
; Оценка обязана быть СВЕРХУ, но не втрое: чем она больше, тем чаще vec_di_budget уводит в
; ei/halt, а halt — чистый простой (в комнате 31 он доходил до 9 % всех тактов). Берём
;   выровненный  32h*(w+2) = 32w + 64 тика/строку (>= 26w+42 при любом w >= 1);
;   сдвинутый    64h*w     = 64w      тиков/строку (>= 62w-18 при любом w >= 1).
; Один общий множитель 64 (как было) для выровненного пути завышал оценку на 60-140 %.
; Путь берём из иммедиата ps_shpg+1 (страница таблицы сдвига, 0 = выровненный: выровненная
; цепочка H не читает, поэтому заголовок кладёт туда 0), ширину — из уже записанного патча
; отката строки (loc_D800+1 = w-1), высоту — из kl_lines.
; Занятыми на входе остаются BC (адрес в буфере) и DE (данные спрайта) — DE сохраняем.
vec_ps_budget:	push	de
				ld		a, (kl_lines)
				ld		h, 0
				ld		l, a
				add		hl, hl
				add		hl, hl
				add		hl, hl
				add		hl, hl				; 16h
				add		hl, hl				; 32h
				ld		a, (ps_shpg+1)		; страница таблицы сдвига; 0 = выровненный путь
				or		a
				jp		z, vpb_al
				add		hl, hl				; сдвинутый: шаг 64h, множитель w-1 -> 64*w*h
				ld		d, h
				ld		e, l
				jp		vpb_go
vpb_al:			ld		d, h				; выровненный: шаг 32h,
				ld		e, l
				add		hl, de				; старт 3*32h, множитель w-1 -> 32h*(w+2)
				add		hl, de
vpb_go:			ld		a, (loc_D800+1)		; w-1
vpb_m:			or		a
				jp		z, vpb_done
				dec		a
				add		hl, de
				jp		vpb_m
vpb_done:		ld		a, h				; DE = оценка + 512 (обвязка входа/выхода)
				add		a, 2
				ld		d, a
				ld		e, l
				call	vec_di_budget
				pop		de
				ret

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
    src = kl_cut_controls(src)
    # прямое управление (Борис): стрелки = направления, пробел = прыжок, ВК = взять/положить. Читается из матрицы
    # Вектора поверх ZX-раскладки, только когда включён бит directional (в классике стрелки сведены к Z/X/A/Q в vec_scan).
    src, n_di = re.subn(r'(loc_D125:[^\n]*\r?\n)(\t+ld      a, c\r?\n\t+ld      \(user_input\), a)', r'\g<1>\t\t\t\tcall    kl_direct_in\n\g<2>', src, count=1)
    assert n_di == 1, "direct input hook"
    src = src.replace("; End of function check_user_input", """; End of function check_user_input
kl_direct_in:	; directional: C = стрелки (0 влево, 1 вправо, 2 вверх, 4 вниз), пробел -> 3 (прыжок), ВК -> 5 (взять)
				ld      a, (user_input_method)
				and     8
				ret     z
				ld      c, 0
				ld      a, (vec_kbmat+0)                        ; ряд 0: LT 0x10, RT 0x40, UP 0x20, DN 0x80, ВК 0x04
				ld      b, a
				and     0x10
				jp      z, kdi_1
				ld      c, 1
kdi_1:			ld      a, b
				and     0x40
				jp      z, kdi_2
				ld      a, c
				or      2
				ld      c, a
kdi_2:			ld      a, b
				and     0x20
				jp      z, kdi_3
				ld      a, c
				or      4
				ld      c, a
kdi_3:			ld      a, b
				and     0x80
				jp      z, kdi_4
				ld      a, c
				or      0x10
				ld      c, a
kdi_4:			ld      a, b
				and     0x04
				jp      z, kdi_5
				ld      a, c
				or      0x20
				ld      c, a
kdi_5:			ld      a, (vec_kbmat+7)                        ; пробел 0x80
				and     0x80
				ret     z
				ld      a, c
				or      8
				ld      c, a
				ret
""", 1)
    # цвет комнаты как на ZX: после flag_room_visited (конец build_screen_objects) — палитра по атрибуту; меню — белое
    src, n_rp = re.subn(r'(\t+call    flag_room_visited\r?\n\t+ret\r?\n)', r'\t\t\t\tcall    flag_room_visited\n\t\t\t\tld      a, (curr_room_attrib)\n\t\t\t\tjp      vec_set_pal\n', src, count=1)
    assert n_rp == 1, "room palette hook"
    src, n_mp = re.subn(r'(do_menu_selection:\r?\n)', r'\g<1>\t\t\t\tld      a, 7\n\t\t\t\tcall    vec_set_pal\n', src, count=1)
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
    tail = VEC_TAIL + screen + kl_sound_ay.DRIVER + kl_sound_ay.data_block()
    # vec_kbzx: 64 байта (ряд Вектора*8 + бит) -> (ряд ZX<<5 | маска 5 бит), 0xFF = клавиша не отображена
    kbzx = [0xFF]*64
    zxrows = re.findall(r'DB\t((?:vec_kbmat\+\d,0x[0-9A-Fa-f]{2},?[ \t]*){5})', tail)
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
                          "ps_jrow:\t\tjp      NZ, loc_D7AA\t\t; младший байт патчит ps_cw: ps_fix, если печать обрезана по столбцу 31\n", 1)
        old_s = "\t\t\t\tneg\n\t\t\t\tadd     a, 0x50 ; 'P'\n"
        assert old_s in src, "ps shifted base"
        src = src.replace(old_s, "\t\t\t\tneg\n\t\t\t\tadd     a, 0x50 ; 'P'\n\t\t\t\tadd     a, low (loc_D7AC+3)\t\t; i8080: jp — младший байт адреса входа\n", 1)
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
        src, n = re.subn(r'(^\t+ORG %#06x\t+; верхний регион)' % VEC_UPPER, lambda m: helpers + "\t\t\t\tASSERT $ <= %#06x\t\t; нижний регион: до стека (128 Б) и переменных на %#06x\n" % (VEC_CODE_TOP, VEC_VARS) + m.group(1), src, count=1, flags=re.M)
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
        src = re.sub(r'^\s*SAVESNA[^\n]*$', '\t\t\t\tSAVEBIN "build/knightlore_vec.r0m", 0, 0x10000', src, flags=re.M)
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
