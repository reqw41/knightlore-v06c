#!/usr/bin/env python3
"""kl_z80_to_8080.py — source-level Z80 -> i8080 translation for Knight Lore (sjasmplus syntax).
Core (split_line/expand/_ixiy/_exx/_classify_exx/_bit/_resset/_shift/_ldir/_adcsbc_hl/translate)
is lifted verbatim from kvalley-v06c/tools/kv_z80_to_8080.py (tag asm-freeze); glue is Knight Lore specific.
Usage: python3 tools/kl_z80_to_8080.py <in.asm> <out.asm> [--target lockstep|vector]
"""
import sys, re
sys.path.insert(0, __import__('os').path.dirname(__file__))
import kl_native


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
_ix_used = set()
# горячие процедуры (по профилю): встроенное разворачивание ради темпа, остальные — компактно
_IX_INLINE_FUNCS = {'wipe_next_object', 'is_near_to', 'is_object_not_ignored', 'adj_2_4_hflip', 'chk_plyr_spec_near_arch', 'upd_2_4', 'add_dXYZ', 'render_dynamic_objects'}
_cur_func = ['']

def _ixiy_compact(mn, ops, idxinfo):
    cell, _ = idxinfo
    if _cur_func[0] in _IX_INLINE_FUNCS:
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

def kl_glue(src, target):
    n0 = src.count('\n')
    # заголовок после DEVICE
    src = src.replace("\tDEVICE ZXSPECTRUM48\n", "\tDEVICE ZXSPECTRUM48\n" + KL_HEADER.replace("__KL_SCRATCH__", kl_native.SCRATCH) + _LDIR_HELPERS + "\n", 1)
    src = kl_logic_fixes(src)
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

# ============================ звук: очередь ВИ53, разбираемая прерыванием ============================
SND_PLAY_NOTE = """play_note:		; A = байт ноты (биты 0-5 нота, 6-7 множитель длительности), DE -> данные. На выходе DE+1.
				; Ничего не ждём: кладём (делитель, длительность в 1/256 кадра) в очередь, играет ISR.
				and     0x3F
				jp      z, snd_delay
				ld      l, a
				ld      h, 0
				add     hl, hl
				call    add_HL_A                                ; HL = 3*note
				ld      bc, freq_tbl
				add     hl, bc
				ld      b, (hl)                                 ; N low  (счётчик полупериода)
				inc     hl
				ld      c, (hl)                                 ; N high (+1)
				inc     hl
				ld      l, (hl)                                 ; длительность (периодов), младший байт
				ld      h, 0
				ld      a, (de)
				rlca
				rlca
				and     3
				inc     a                                       ; множитель 1..4
				push    de
				ld      e, l
				ld      d, h
pn_mul:			dec     a
				jp      z, pn_done
				add     hl, de
				jp      pn_mul
pn_done:		ld      d, c
				dec     d
				ld      e, b                                    ; DE = N = (C-1)*256 + B
				push    hl                                      ; периодов
				push    de
				call    vec_snd_tick                            ; A = тиков на период = (N*3)>>5
				pop     de
				pop     hl
				call    vec_snd_mul                             ; HL = A * HL (тиков всего)
				ex      de, hl                                  ; DE = тики
				call    vec_snd_div                             ; HL = делитель ВИ53 из DE?  (нет: из N) — см. ниже
				pop     de
				inc     de
				ret
"""
# ВНИМАНИЕ: порядок регистров в play_note собирается ниже кодом, а не этой строкой (см. SND_PLAY_NOTE2)
SND_PLAY_NOTE2 = """play_note:		; A = байт ноты (биты 0-5 нота, 6-7 множитель длительности), DE -> данные. На выходе DE+1.
				; Ничего не ждём: кладём (делитель ВИ53, длительность в 1/256 кадра) в очередь, играет ISR.
				and     0x3F
				jp      z, snd_delay
				ld      l, a
				ld      h, 0
				add     hl, hl
				call    add_HL_A                                ; HL = 3*note
				ld      bc, freq_tbl
				add     hl, bc
				ld      b, (hl)                                 ; N low  (счётчик полупериода)
				inc     hl
				ld      c, (hl)                                 ; N high (+1)
				inc     hl
				ld      l, (hl)                                 ; длительность (периодов), младший байт
				ld      h, 0
				ld      a, (de)
				rlca
				rlca
				and     3
				inc     a                                       ; множитель 1..4
				push    de
				ld      e, l
				ld      d, h
pn_mul:			dec     a
				jp      z, pn_done
				add     hl, de
				jp      pn_mul
pn_done:		ld      d, c
				dec     d
				ld      e, b                                    ; DE = N
				ld      a, 48
				ld      (vec_snd_k), a                          ; накладные петли play_note
				call    vec_snd_entry                           ; HL = периодов, DE = N -> в очередь
				pop     de
				inc     de
				ret

snd_delay:		; пауза: (de) биты 6-7 -> 1..4 единиц по ~0x430B*24 тактов = 1506 тиков каждая
				ld      a, (de)
				inc     de
				rlca
				rlca
				and     3
				inc     a
				push    de
				ld      hl, 0
				ld      de, 1506
sd_mul:			add     hl, de
				dec     a
				jp      nz, sd_mul
				ex      de, hl                                  ; DE = тики
				ld      hl, 0                                   ; делитель 0 = тишина
				call    vec_snd_push
				pop     de
				ret
; End of function play_note

toggle_audio_hw:	; один период с полупериодом B (сохраняет B): в очередь, без ожидания
				push    hl
				push    de
				push    bc
				ld      hl, 1
				ld      d, 0
				ld      e, b
				ld      a, 35
				ld      (vec_snd_k), a                          ; накладные toggle_audio_hw (26B+82 T)
				call    vec_snd_entry
				pop     bc
				pop     de
				pop     hl
				ret

toggle_audio_hw_xC:	; C периодов с полупериодом B: одна запись длительностью C периодов
				push    hl
				push    de
				push    bc
				ld      h, 0
				ld      l, c
				ld      d, 0
				ld      e, b
				ld      a, 35
				ld      (vec_snd_k), a
				call    vec_snd_entry
				pop     bc
				pop     de
				pop     hl
				ld      c, 0
				ret
"""

SND_UNTIL_KEY = """play_audio_until_keypress:	; как ZX: между нотами опрос клавиш; нота — в очередь, когда есть место;
				; конец данных — ждать, пока очередь доиграет (или клавишу); клавиша — сброс очереди
pauk_loop:		xor     a
				call    read_port
				jp      nz, vec_snd_flush                       ; клавиша: прервать мелодию
				ld      a, (de)
				cp      0xFF
				jp      z, pauk_end
				call    vec_snd_room
				jp      z, pauk_loop                            ; очередь полна: подождать
				ld      a, (de)                                 ; (vec_snd_room портит A)
				call    play_note
				jp      pauk_loop
pauk_end:		xor     a
				call    read_port
				jp      nz, vec_snd_flush
				call    vec_snd_idle
				jp      nz, pauk_end
				ret
"""

SND_TAIL = """
; ---- ЗВУК: очередь записей (делитель ВИ53 lo,hi; длительность lo,hi в 1/256 кадра), 64 записи ----
				ORG		0xE900
vec_sq:			DS		256
vec_sq_rd:		DS		1
vec_sq_wr:		DS		1
vec_sq_rem:		DS		2			; остаток тиков текущей записи
vec_sq_on:		DS		1			; 1 = канал звучит
"""

SND_CODE = """
; vec_snd_div: DE = N (итераций djnz полупериода) -> HL = делитель ВИ53 = N*11 + N/8 + N/16 + vec_snd_k. Портит A.
vec_snd_div:	ld		h, d
				ld		l, e
				add		hl, hl
				add		hl, hl
				add		hl, hl				; N*8
				add		hl, de
				add		hl, de
				add		hl, de				; N*11
				push	de
				ld		a, d
				or		a
				rra
				ld		d, a
				ld		a, e
				rra
				ld		e, a
				ld		a, d
				or		a
				rra
				ld		d, a
				ld		a, e
				rra
				ld		e, a
				ld		a, d
				or		a
				rra
				ld		d, a
				ld		a, e
				rra
				ld		e, a				; N/8
				add		hl, de
				ld		a, d
				or		a
				rra
				ld		d, a
				ld		a, e
				rra
				ld		e, a				; N/16
				add		hl, de				; 11.1875N (ZX: 26.09N тактов * 3/7)
				ld		a, (vec_snd_k)
				ld		e, a
				ld		d, 0
				add		hl, de				; + накладные такты петли (МНК по журналу ZX: +111 T = 48 делителя; toggle: 82 T = 35)
				pop		de
				ret

; vec_snd_tick: DE = N -> A = тиков (1/256 кадра) на период = 26*N/273 ~ (N*3)>>5, не больше 255. Портит HL.
vec_snd_tick:	ld		h, d
				ld		l, e
				add		hl, hl
				add		hl, de				; N*3
				ld		a, h
				rra
				ld		h, a
				ld		a, l
				rra
				ld		l, a
				ld		a, h
				or		a
				rra
				ld		h, a
				ld		a, l
				rra
				ld		l, a
				ld		a, h
				or		a
				rra
				ld		h, a
				ld		a, l
				rra
				ld		l, a
				ld		a, h
				or		a
				rra
				ld		h, a
				ld		a, l
				rra
				ld		l, a
				ld		a, h
				or		a
				rra
				ld		h, a
				ld		a, l
				rra
				ld		l, a				; >>5
				ld		a, h
				or		a
				ld		a, l
				ret		z
				ld		a, 0xFF
				ret

; vec_snd_entry: HL = периодов, DE = N -> запись (делитель(N), тики = tick(N)*HL) в очередь. Портит всё.
vec_snd_entry:	push	hl
				push	de
				call	vec_snd_tick
				pop		de
				pop		hl
				push	de
				ld		b, a				; B = тиков на период (8 бит), HL = периодов (16 бит)
				ld		de, 0				; DE = произведение
				ld		c, 8
se_mul:			ld		a, b
				rrca
				ld		b, a				; младший бит множителя -> CY (HL удваивается на каждом шаге; rlca давало зеркальный множитель)
				jp		nc, se_nb
				ex		de, hl
				add		hl, de
				ex		de, hl				; DE += HL
				jp		nc, se_nb
				ld		de, 0xFFFF			; переполнение — насыщение
				jp		se_done
se_nb:			dec		c
				jp		z, se_done
				add		hl, hl				; HL <<= 1
				jp		se_mul
se_done:		pop		hl					; HL = N, DE = тики
				ex		de, hl				; DE = N, HL = тики
				push	hl					; тики
				call	vec_snd_div			; DE=N -> HL = делитель
				pop		de					; DE = тики
				; падаем в vec_snd_push
; vec_snd_push: HL = делитель (0 = тишина), DE = тики -> в очередь (если полна — запись теряется)
vec_snd_push:	push	hl
				ld		a, (vec_sq_wr)
				add		a, 4
				ld		c, a				; следующий wr
				ld		a, (vec_sq_rd)
				cp		c
				jp		z, sp_full
				ld		a, (vec_sq_wr)
				pop		hl
				push	hl
				ld		h, high vec_sq
				ld		l, a				; HL -> запись
				pop		bc					; BC = делитель
				ld		(hl), c
				inc		l
				ld		(hl), b
				inc		l
				ld		(hl), e
				inc		l
				ld		(hl), d
				ld		a, c
				ld		a, (vec_sq_wr)
				add		a, 4
				ld		(vec_sq_wr), a
				ret
sp_full:		pop		hl
				ret

; vec_snd_room: Z = очередь полна (нет места для записи). Портит A, флаги.
vec_snd_room:	push	hl
				ld		a, (vec_sq_wr)
				add		a, 4
				ld		l, a
				ld		a, (vec_sq_rd)
				cp		l
				pop		hl
				ret

; vec_snd_idle: Z = очередь пуста и канал молчит. Портит A, флаги.
vec_snd_idle:	push	hl
				ld		a, (vec_sq_rd)
				ld		l, a
				ld		a, (vec_sq_wr)
				sub		l
				ld		l, a
				ld		a, (vec_sq_on)
				or		l
				pop		hl
				ret

; vec_snd_flush: очистить очередь и заглушить канал
vec_snd_flush:	xor		a
				ld		(vec_sq_rd), a
				ld		(vec_sq_wr), a
				ld		(vec_sq_rem), a
				ld		(vec_sq_rem+1), a
				ld		(vec_sq_on), a
				ld		a, 0x36
				out		(0x08), a
				ret

; vi_snd: вызывается из ISR раз в кадр: списать 256 тиков, при исчерпании взять следующие записи
vi_snd:			ld		hl, (vec_sq_rem)
				ld		de, 0xFF00
				add		hl, de				; rem -= 256; CY=1 если rem был >= 256
				jp		nc, vs_next
				ld		(vec_sq_rem), hl
				ret
vs_next:		; HL = rem - 256 (отрицательное) ; дефицит = -HL
				ex		de, hl
				ld		hl, 0
				or		a
				sbc		hl, de				; HL = дефицит (Z80: sbc hl; для i8080 см. транслятор)
vs_pop:			ld		a, (vec_sq_rd)
				ld		e, a
				ld		a, (vec_sq_wr)
				cp		e
				jp		z, vs_empty
				push	hl
				ld		h, high vec_sq
				ld		l, e
				ld		c, (hl)
				inc		l
				ld		b, (hl)				; BC = делитель
				inc		l
				ld		e, (hl)
				inc		l
				ld		d, (hl)				; DE = тики записи
				ld		a, l
				inc		a
				ld		(vec_sq_rd), a
				ld		a, b
				or		c
				jp		z, vs_off
				ld		a, 0x36
				out		(0x08), a
				ld		a, c
				out		(0x0B), a
				ld		a, b
				out		(0x0B), a
				ld		a, 1
				ld		(vec_sq_on), a
				jp		vs_dur
vs_off:			ld		a, (vec_sq_on)
				or		a
				jp		z, vs_dur
				xor		a
				ld		(vec_sq_on), a
				ld		a, 0x36
				out		(0x08), a
vs_dur:			pop		hl					; HL = дефицит
				ex		de, hl				; DE = дефицит, HL = тики записи
				or		a
				sbc		hl, de				; rem = тики - дефицит
				jp		nc, vs_store		; хватило: остаток положительный
				ex		de, hl
				ld		hl, 0
				or		a
				sbc		hl, de				; дефицит = -(rem)
				jp		vs_pop
vs_store:		ld		(vec_sq_rem), hl
				ret
vs_empty:		ld		hl, 0
				ld		(vec_sq_rem), hl
				ld		a, (vec_sq_on)
				or		a
				ret		z
				xor		a
				ld		(vec_sq_on), a
				ld		a, 0x36
				out		(0x08), a
				ret
"""


def kl_sound_async(src):
    """Звук в очередь: play_note/snd_delay/toggle_audio_hw/_xC не ждут; ISR разбирает записи по кадрам."""
    src, n = re.subn(r'play_note:\n.*?; End of function play_note\r?\n', SND_PLAY_NOTE2, src, count=1, flags=re.S)
    assert n == 1, "play_note"
    src, n = re.subn(r'toggle_audio_hw:\n.*?; End of function toggle_audio_hw\r?\n', '', src, count=1, flags=re.S)
    assert n == 1, "toggle_audio_hw"
    old_pa = "play_audio:\n\t\t\t\tld      a, (de)                                 ; audio data byte\n\t\t\t\tcp      0xFF                                   ; done?\n\t\t\t\tjr      Z, end_audio                            ; yes, return\n"
    assert old_pa in src, "play_audio"
    src = src.replace(old_pa, old_pa + "\t\t\t\tcall    vec_snd_room\n\t\t\t\tjr      Z, play_audio                           ; очередь полна: ждать место (ISR разгружает)\n\t\t\t\tld      a, (de)                                 ; (vec_snd_room портит A)\n", 1)
    old = "toggle_audio_hw_xC:\n\t\t\t\tcall    toggle_audio_hw\n\t\t\t\tdec     c\n\t\t\t\tjr      NZ, toggle_audio_hw_xC\n\t\t\t\tret\n"
    assert old in src, "toggle_audio_hw_xC"
    src = src.replace(old, "", 1)
    src = src.replace("jr      toggle_audio_hw_xC", "jp      toggle_audio_hw_xC")   # xC теперь далеко (в хвосте)
    src, n = re.subn(r'play_audio_until_keypress:\n.*?; End of function play_audio_until_keypress\r?\n', SND_UNTIL_KEY, src, count=1, flags=re.S)
    assert n == 1, "until_keypress"
    # экран ZX сдвигаем на страницу: 0xCD00 занимает очередь звука
    src = src.replace("\t\t\t\tORG 0xCD00\nzx_vram:", "\t\t\t\tORG 0xCE00\nzx_vram:", 1)
    src = src.replace("\t\t\t\tORG 0xE500\nzx_aram:", "\t\t\t\tORG 0xE600\nzx_aram:", 1)
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
				ld		sp, 0x8000
				jp		vec_boot
				ORG		0x0038
				jp		vec_isr			; RST 38h: кадровое прерывание (палитра в VBlank, скан клавиатуры)
				ORG		0x0040
vec_kbmat:		DS		8			; матрица клавиатуры Вектора, active-high (ряд 0..7)
vec_tmp:		DS		2
vec_palreq:		DS		1			; 1 = загрузить палитру в ближайшем VBlank
vec_snd_k:		DS		1			; звук: константа делителя (накладные такты петли ZX): play_note 48, toggle 35
				ORG		0x0060
vec_boot:		ld		a, 0x88				; 8255: A выход (скролл/ряд клавы), B выход (бордюр/режим)
				out		(0x00), a
				xor		a
				out		(0x02), a			; бордюр чёрный, режим 256
				ld		a, 0xFF
				out		(0x03), a			; скролл
				call	vec_snd_flush			; очередь звука пуста, канал молчит
				ld		a, 1
				ld		(vec_palreq), a
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
				push	bc
				push	de
				push	hl
				ld		a, (vec_palreq)
				or		a
				jp		z, vi_kbd
				ld		e, 0x0F
vb_pal:			ld		a, e
				out		(0x02), a			; индекс = цвет бордюра (луч в обратном ходе)
				ld		a, e
				and		8
				jp		z, vb_black
				ld		a, 0xFF
				jp		vb_w
vb_black:		xor		a
vb_w:			out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				out		(0x0C), a
				dec		e
				jp		p, vb_pal
				xor		a
				out		(0x02), a			; бордюр чёрный
				ld		(vec_palreq), a
vi_kbd:			call	vec_scan
				call	vi_snd
				pop		hl
				pop		de
				pop		bc
				pop		af
				ei
				ret

; ---- клавиатура: скан матрицы Вектора -> vec_kbmat, с зеркалом стрелок в буквы ----
vec_scan:		ld		a, 0x8A				; порт B на вход
				out		(0x00), a
				ld		hl, vec_kbmat
				ld		c, 0xFE				; бит i = 0 -> ряд i
				ld		b, 8
vs_l:			ld		a, c
				out		(0x03), a
				in		a, (0x02)
				cpl
				ld		(hl), a
				inc		hl
				ld		a, c
				rlca
				ld		c, a
				dec		b
				jp		nz, vs_l
				ld		a, 0x88
				out		(0x00), a
				ld		a, 0xFF
				out		(0x03), a			; вернуть скролл
				xor		a
				out		(0x02), a			; вернуть бордюр/режим
				; стрелки (ряд 0: DN=7 RT=6 UP=5 LT=4) -> Z / X / A / Q
				ld		a, (vec_kbmat+0)
				ld		b, a
				and		0x10				; LT -> Z (ряд 7 бит 2)
				jp		z, vs_1
				ld		a, (vec_kbmat+7)
				or		0x04
				ld		(vec_kbmat+7), a
vs_1:			ld		a, b
				and		0x40				; RT -> X (ряд 7 бит 0)
				jp		z, vs_2
				ld		a, (vec_kbmat+7)
				or		0x01
				ld		(vec_kbmat+7), a
vs_2:			ld		a, b
				and		0x20				; UP -> A (ряд 4 бит 1)
				jp		z, vs_3
				ld		a, (vec_kbmat+4)
				or		0x02
				ld		(vec_kbmat+4), a
vs_3:			ld		a, b
				and		0x80				; DN -> Q (ряд 6 бит 1)
				ret		z
				ld		a, (vec_kbmat+6)
				or		0x02
				ld		(vec_kbmat+6), a
				ret

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


VEC_READ_PORT = """read_port:		; A = маска рядов ZX (бит i=0 -> ряд i опрошен) -> A = cpl(AND рядов) and 0x1F, как в оригинале
				push	hl
				push	bc
				push	de
				ld		c, a				; матрица vec_kbmat обновляется в прерывании (VBlank)
				ld		a, 0xFF
				ld		(vec_tmp), a
				ld		hl, vec_zxmap
				ld		b, 8
rp_row:			ld		a, c
				rra
				ld		c, a
				jp		nc, rp_sel
				ld		a, l				; ряд не выбран: пропустить 5 записей
				add		a, 10
				ld		l, a
				ld		a, h
				adc		a, 0
				ld		h, a
				jp		rp_next
rp_sel:			ld		d, 0xFF				; байт ZX-ряда, active-low
				ld		e, 0x01
rp_key:			ld		a, (hl)
				inc		hl
				push	hl
				ld		l, a
				ld		h, 0
				ld		a, (hl)				; биты ряда Вектора (active-high)
				pop		hl
				and		(hl)
				inc		hl
				jp		z, rp_nk
				ld		a, e
				cpl
				and		d
				ld		d, a
rp_nk:			ld		a, e
				add		a, a
				ld		e, a
				cp		0x20
				jp		nz, rp_key
				ld		a, (vec_tmp)
				and		d
				ld		(vec_tmp), a
rp_next:		dec		b
				jp		nz, rp_row
				ld		a, (vec_tmp)
				cpl
				and		0x1F
				pop		de
				pop		bc
				pop		hl
				ret
; End of function read_port"""

VEC_SCREEN = """
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
blit_to_screen:
				inc		b
				dec		b
				ret		z
				inc		c
				dec		c
				ret		z
bts_col:		push	bc
				push	hl
				push	de
bts_row:		ld		a, (hl)
				ld		(de), a
				inc		l
				inc		e
				dec		b
				jp		nz, bts_row
				pop		de
				pop		hl
				pop		bc
				inc		h
				inc		d
				ld		a, d
				cp		0xA0				; за столбцом 31 — стоп (иначе пишем в буфер)
				ret		z
				dec		c
				jp		nz, bts_col
				ret

; HL = буфер, B = высота, C = ширина (байт), A = значение. Нулевой размер — выход (A сохраняется).
vidbuf_fill_window:
				inc		b
				dec		b
				ret		z
				inc		c
				dec		c
				ret		z
				ld		d, a
vfw_col:		push	hl
				push	bc
vfw_row:		ld		(hl), d
				inc		l
				dec		b
				jp		nz, vfw_row
				pop		bc
				pop		hl
				inc		h
				ld		a, h
				cp		high vidbuf + 32			; за столбцом 31 — стоп
				ret		z
				dec		c
				jp		nz, vfw_col
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
vec_suntmp:		equ		0xC700			; защитная зона за буфером: 93 байта временно

update_screen:	; весь буфер (строки 32..223 всех 32 столбцов) -> плоскость 0, буфер обнуляется (как оригинал 0xD57D)
				ld		hl, vidbuf+0x20
				ld		de, 0x8020
				ld		c, 32
us_col:			push	hl
				push	de
				ld		b, 192
us_row:			ld		a, (hl)
				ld		(de), a
				ld		(hl), 0
				inc		l
				inc		e
				dec		b
				jp		nz, us_row
				pop		de
				pop		hl
				inc		h
				inc		d
				dec		c
				jp		nz, us_col
				ret
"""

def _replace_func(src, label, end_marker, new, note=""):
    """Заменить тело от `label:` до строки end_marker (включительно) на new."""
    i = src.index(label + ":")
    j = src.index(end_marker, i) + len(end_marker)
    return src[:i] + new + src[j:]

def kl_vec_glue(src, translate_later):
    """Адаптация под Вектор-06Ц (общая для Z80- и i8080-целей): загрузчик, экран, клавиатура, ВИ53."""
    # заголовок после DEVICE
    # РАСКЛАДКА: код 0x0100.., стек <0x8000, ПЛОСКОСТЬ 0x8000-0x9FFF, переменные 0xA000, vidbuf 0xA600,
    # таблицы 0xBE00/0xBF00, "экран ZX" 0xCD00 (только атрибуты как мусорное ОЗУ)
    src = src.replace("\t\t\t\tORG 0x4000\nzx_vram:", "\t\t\t\tORG 0xCD00\nzx_vram:", 1)
    src = src.replace("\t\t\t\tORG 0x5800\nzx_aram:", "\t\t\t\tORG 0xE500\nzx_aram:", 1)
    # 0xC000-0xC7FF — ЗАЩИТНАЯ ЗОНА: спрайты/стирания у правого края вылезают за столбец 31 (в линейном
    # буфере ZX это заворачивало строку), плюс временный стек `ld sp,seed_1` (у ZX — буфер принтера).
    # Переменные с 0xC800.
    # 0xC000-0xC7FF — ЗАЩИТНАЯ ЗОНА: спрайты/стирания у правого края вылезают за столбец 31 (в линейном
    # буфере ZX это заворачивало строку), плюс временный стек `ld sp,seed_1` (у ZX — буфер принтера).
    src = re.sub(r'^\t+ORG 0x5BA0\nclear_start:', '\t\t\t\tORG 0xC800\nclear_start:', src, count=1, flags=re.M)
    src = src.replace("\nclear_end:\n", "\nclear_end:\n\t\t\t\tORG 0x0100\t; код и данные игры\n", 1)
    # vidbuf — отдельный сегмент; START и build_lookup_tbls (в оригинале лежат ВНУТРИ буфера и
    # затираются им) выносим на 0x7A00 (код кончается ~0x78B1, стек растёт вниз от 0x8000)
    src = re.sub(r'^\t+ORG\s+0xD900\nvidbuf:', '\t\t\t\tORG 0xA000\nvidbuf:\t\t\tDS 0x2000\n\t\t\t\tORG 0x7A00\t; START/build_lookup_tbls вне буфера', src, count=1, flags=re.M)
    # таблицы сдвига ОСТАЮТСЯ на 0xF100/0xF200: print_sprite адресует страницу таблицы константой (or 0xF0)
    src = re.sub(r'^(START:\s+ld\s+sp,\s*)0x5B9[Ff]', r'\g<1>0x8000', src, count=1, flags=re.M)
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
    src = src.replace("clear_scrn_buffer:\n\t\t\t\tld      bc, 0x1800", "clear_scrn_buffer:\n\t\t\t\tld      bc, 0x2000", 1)
    assert "clear_scrn_buffer:\n\t\t\t\tld      bc, 0x2000" in src
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
    assert "ld      hl, 0x5800\n" in src
    src = src.replace("ld      hl, 0x5800\n", "ld      hl, zx_aram\n", 1)
    # бордюр ZX (ULA) — убрать
    src = src.replace("\t\t\t\txor     a                                       ; border colour BLACK, activate MIC\n\t\t\t\tout     (0xFE), a                               ; ULA\n",
                      "\t\t\t\txor     a\n", 1)
    src = kl_sound_async(src)
    assert "out     (0xFE)" not in src, "остались OUT (0xFE)"
    src = re.sub(r'^\s*ASSERT\s+\$\s*<\s*0xD900[^\n]*$', '', src, flags=re.M)
    hdr = VEC_HEADER
    tail = VEC_TAIL + VEC_SCREEN + SND_CODE
    snd_tail = SND_TAIL
    if translate_later:
        # ---- i8080: скретч эмуляции Z80 в нулевой странице, загрузчик на 0x0090 ----
        hdr = hdr.replace("vec_palreq:\t\tDS\t\t1\t\t\t; 1 = загрузить палитру в ближайшем VBlank\n",
            "vec_palreq:\t\tDS\t\t1\t\t\t; 1 = загрузить палитру в ближайшем VBlank\n" + SCRATCH_DEFS_VEC + kl_native.SCRATCH)
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
        src = src.replace(old_e, "\t\t\t\tld      a, (kl_lines)\n\t\t\t\tdec     a\n\t\t\t\tld      (kl_lines), a\n\t\t\t\tjp      NZ, loc_D7AA\n", 1)
        old_s = "\t\t\t\tneg\n\t\t\t\tadd     a, 0x50 ; 'P'\n"
        assert old_s in src, "ps shifted base"
        src = src.replace(old_s, "\t\t\t\tneg\n\t\t\t\tadd     a, 0x50 ; 'P'\n\t\t\t\tadd     a, low (loc_D7AC+3)\t\t; i8080: jp — младший байт адреса входа\n", 1)
        old_a = "\t\t\t\tneg\n\t\t\t\tsub     6\n\t\t\t\tjr      loc_D73C\n"
        assert old_a in src, "ps aligned base"
        src = src.replace(old_a, "\t\t\t\tneg\n\t\t\t\tsub     6\n\t\t\t\tadd     a, low (loc_D7AC+3)\t\t; i8080: jp — младший байт входа (+1: перед loc_D7AC убран ex af,af')\n\t\t\t\tjr      loc_D73C\n", 1)
        i = src.index("\t\t\t\tjr      loc_D73C\n", src.index("print_sprite:")) + len("\t\t\t\tjr      loc_D73C\n")
        j = src.index("\t\t\t\tpop     de\n", i)
        src = src[:j] + "\t\t\t\tALIGN 256\t\t\t\t\t; все точки входа развёрнутого цикла в одной странице\n" + src[j:]
        # ---- разрез памяти: хвост спрайтов (spr_064..) + хелперы + START в верхний регион 0xD400 ----
        assert "\nspr_064:" in src
        src = src.replace("\nspr_064:", "\n\t\t\t\tORG 0xD400\t\t\t; верхний регион: хвост спрайтов, IX-хелперы, START\nspr_064:", 1)
        # блок Вектора (без ORG 0x7B00) — в нижний регион перед разрезом; START — за vidbuf, но в верхнем регионе
        tail = tail.replace("\t\t\t\tORG\t\t0x7B00\t\t\t; блок Вектора: клавиатура, ВИ53, экран (после START на 0x7A00, до стека)\n", "")
        tail = tail + _LDIR_HELPERS
        src = src.replace("\n\t\t\t\tORG 0xD400\t\t\t; верхний регион", "\n" + tail + "\n\t\t\t\tORG 0xD400\t\t\t; верхний регион", 1)
        tail = ""
        src = src.replace("\t\t\t\tORG 0xA000\nvidbuf:\t\t\tDS 0x2000\n\t\t\t\tORG 0x7A00\t; START/build_lookup_tbls вне буфера", "vidbuf:\t\t\tequ 0xA000\t\t; плоскость 1; START/build_lookup_tbls — продолжение верхнего региона", 1)
        assert "vidbuf:\t\t\tequ 0xA000" in src
        # атрибуты ZX: только 1К реального ОЗУ на 0xCE00 (zx_vram=0xB700 — битмап не пишется), очередь звука 0xD200
        src = src.replace("\t\t\t\tORG 0xCE00\nzx_vram:", "\t\t\t\tORG 0xB700\nzx_vram:", 1)
        src = src.replace("\t\t\t\tORG 0xE600\nzx_aram:", "\t\t\t\tORG 0xCF00\nzx_aram:", 1)
        snd_tail = snd_tail.replace("\t\t\t\tORG\t\t0xE900\nvec_sq:", "\t\t\t\tORG\t\t0xD200\nvec_sq:")
    # заголовок (и наши экранные процедуры) — ПОСЛЕ удаления старых, иначе регулярки цепляют новые
    src = src.replace("\tDEVICE ZXSPECTRUM48\n", "\tDEVICE ZXSPECTRUM48\n" + hdr + "\n", 1)
    src = re.sub(r'^(\s*SAVESNA)', (tail + snd_tail).replace("\\", "\\\\") + r"\n\g<1>", src, count=1, flags=re.M)
    return src


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
        src = kl_native.apply(src)
        src, exx_pairs = _classify_exx(src)
        src = translate(src)
        helpers = ix_helpers_text()
        # хелперы — в нижний регион (после блока Вектора, до стека 0x8000): в верхнем они упирались в таблицы 0xF100,
        # которые build_lookup_tbls перезаписывает (IY-хелперы стирались -> старт игры уходил в мусор)
        src, n = re.subn(r'(^\t+ORG 0xD400\t+; верхний регион)', lambda m: helpers + "\t\t\t\tASSERT $ <= 0x7E00\t\t; нижний регион: до стека (SP=0x8000)\n" + m.group(1), src, count=1, flags=re.M)
        assert n == 1, "место для IX-хелперов (vec)"
        src, n = re.subn(r'(^\t+ORG\s+0xF100\nreverse_bits_table:)', lambda m: "\t\t\t\tASSERT $ <= 0xF100\t\t; верхний регион: до таблиц сдвига\n" + m.group(1), src, count=1, flags=re.M)
        assert n == 1, "ASSERT верхнего региона"
        src, n = re.subn(r'(^\s*SAVESNA)', r"\t\t\t\tASSERT $ <= 0xD400\t\t; очередь звука до верхнего региона\n\g<1>", src, count=1, flags=re.M)
        assert n == 1, "ASSERT очереди звука"
        src = re.sub(r'^\s*SAVESNA[^\n]*$', '\t\t\t\tSAVEBIN "build/knightlore_vec.r0m", 0, 0x10000', src, flags=re.M)
        open(outp, 'w', encoding='utf-8').write(src)
        print(f"vector glue + translate (i8080) {inp} -> {outp}; exx-B pairs={exx_pairs}; IX-хелперов {len(_ix_used)}")
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
