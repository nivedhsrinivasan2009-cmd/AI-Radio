"""RDS — the Radio Data System (IEC 62106): the station name and text that car radios display.

How it travels (from the bottom up):
  * Data is sent in GROUPS of 4 BLOCKS. Each block = 16 data bits + 10 check bits (26 bits).
  * The check bits are a CRC (a cyclic error-detecting code) plus an "offset word" that also tells the
    receiver WHICH block it is (A, B, C, C' or D) — that is how a receiver finds where groups start.
  * Bits are DIFFERENTIALLY encoded (a 1 means "flip", a 0 means "stay"), so the receiver doesn't care
    whether its copy of the carrier is upside down.
  * Each bit is sent as a BIPHASE symbol (a +/- pair, like Manchester code) at 1187.5 bits per second,
    on a 57 kHz subcarrier (3 × the 19 kHz stereo pilot) using BPSK.

Group types used here:
  0A — PS, the 8-character Programme Service name ("TAMIL FM"), 2 characters per group
  2A — RadioText, up to 64 characters of free text, 4 characters per group
"""
from dataclasses import dataclass, field

import numpy as np

BITRATE = 1187.5
POLY = 0b10110111001          # g(x) = x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1
OFFSETS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "C'": 0x350, "D": 0x1B4}


# ----------------------------------------------------------------- the error-detecting code
def crc10(data16: int) -> int:
    """Remainder of data(x)·x^10 divided by g(x): the 10 check bits before the offset is added."""
    reg = data16 << 10
    for bit in range(25, 9, -1):
        if reg & (1 << bit):
            reg ^= POLY << (bit - 10)
    return reg & 0x3FF


def encode_block(data16: int, offset: str) -> int:
    return (data16 << 10) | (crc10(data16) ^ OFFSETS[offset])


def check_block(block26: int, offset: str) -> int | None:
    """Return the 16 data bits if this 26-bit block is a valid block of the given kind."""
    data = block26 >> 10
    if (block26 & 0x3FF) == (crc10(data) ^ OFFSETS[offset]):
        return data
    return None


# ----------------------------------------------------------------- building groups (transmitter side)
def _bits(value: int, n: int) -> list[int]:
    return [(value >> i) & 1 for i in range(n - 1, -1, -1)]


def group_bits(blocks: tuple[int, int, int, int], third: str = "C") -> list[int]:
    a, b, c, d = blocks
    out = []
    for data, off in ((a, "A"), (b, "B"), (c, third), (d, "D")):
        out += _bits(encode_block(data, off), 26)
    return out


def ps_groups(pi: int, ps: str, pty: int = 10) -> list[tuple]:
    ps = ps.ljust(8)[:8]
    groups = []
    for seg in range(4):
        b = (0 << 12) | (0 << 11) | (0 << 10) | (pty << 5) | (1 << 3) | seg   # type 0A, music, segment
        d = (ord(ps[2 * seg]) << 8) | ord(ps[2 * seg + 1])
        groups.append((pi, b, 0xE0E0, d))                                   # 0xE0E0: "no alternative frequencies"
    return groups


def rt_groups(pi: int, text: str, pty: int = 10, ab_flag: int = 0) -> list[tuple]:
    text = text[:64]
    if len(text) < 64:
        text += "\r"                                                         # end-of-text marker
    text = text.ljust(-(-len(text) // 4) * 4)
    groups = []
    for seg in range(len(text) // 4):
        s = text[4 * seg:4 * seg + 4]
        b = (2 << 12) | (0 << 11) | (pty << 5) | (ab_flag << 4) | seg
        groups.append((pi, b, (ord(s[0]) << 8) | ord(s[1]), (ord(s[2]) << 8) | ord(s[3])))
    return groups


class RDSBitSource:
    """Endless stream of RDS data bits: PS groups interleaved with RadioText groups."""

    def __init__(self, pi: int, ps: str, rt: str):
        ps_g, rt_g = ps_groups(pi, ps), rt_groups(pi, rt)
        seq = []
        for i in range(max(4, len(rt_g))):  # PS, RT, PS, RT... every PS segment and every RT segment
            seq.append(ps_g[i % 4])
            seq.append(rt_g[i % len(rt_g)])
        self.bits = [b for g in seq for b in group_bits(g)]
        self.pos = 0

    def take(self, n: int) -> list[int]:
        out = []
        while len(out) < n:
            k = min(n - len(out), len(self.bits) - self.pos)
            out += self.bits[self.pos:self.pos + k]
            self.pos = (self.pos + k) % len(self.bits)
        return out


# ----------------------------------------------------------------- decoding (receiver side)
@dataclass
class RDSState:
    pi: int | None = None
    ps: str = ""
    rt: str = ""
    groups_ok: int = 0
    blocks_bad: int = 0
    _ps_chars: list = field(default_factory=lambda: [" "] * 8)
    _ps_seen: set = field(default_factory=set)
    _rt_chars: list = field(default_factory=lambda: [" "] * 64)
    _rt_seen: set = field(default_factory=set)
    _rt_ab: int | None = None


class RDSDecoder:
    """Finds groups in a stream of decoded bits by testing every position for a valid block A,
    then checks B, C (or C') and D at the expected distances. Bad CRCs are simply skipped."""

    def __init__(self):
        self.buf: list[int] = []
        self.state = RDSState()

    @staticmethod
    def _word(bits: list[int], i: int) -> int:
        v = 0
        for b in bits[i:i + 26]:
            v = (v << 1) | b
        return v

    def feed(self, bits) -> None:
        self.buf.extend(int(b) for b in bits)
        i, buf = 0, self.buf
        while len(buf) - i >= 104:
            a = check_block(self._word(buf, i), "A")
            if a is None:
                i += 1
                continue
            b = check_block(self._word(buf, i + 26), "B")
            c = check_block(self._word(buf, i + 52), "C")
            if c is None:
                c = check_block(self._word(buf, i + 52), "C'")
            d = check_block(self._word(buf, i + 78), "D")
            if b is None or d is None:
                self.state.blocks_bad += 1
                i += 1
                continue
            self._group(a, b, c, d)
            i += 104
        self.buf = buf[i:]

    def _group(self, a: int, b: int, c: int | None, d: int) -> None:
        st = self.state
        st.pi = a
        st.groups_ok += 1
        gtype, version = b >> 12, (b >> 11) & 1
        if gtype == 0:
            seg = b & 0x3
            st._ps_chars[2 * seg:2 * seg + 2] = [chr(d >> 8), chr(d & 0xFF)]
            st._ps_seen.add(seg)
            if len(st._ps_seen) == 4:
                st.ps = "".join(st._ps_chars).strip()
        elif gtype == 2:
            ab, seg = (b >> 4) & 1, b & 0xF
            if st._rt_ab is not None and ab != st._rt_ab:   # station started a new text
                st._rt_chars, st._rt_seen = [" "] * 64, set()
            st._rt_ab = ab
            if version == 0 and c is not None:
                chars = [chr(c >> 8), chr(c & 0xFF), chr(d >> 8), chr(d & 0xFF)]
                st._rt_chars[4 * seg:4 * seg + 4] = chars
            elif version == 1:
                st._rt_chars[2 * seg:2 * seg + 2] = [chr(d >> 8), chr(d & 0xFF)]
            st._rt_seen.add(seg)
            text = "".join(st._rt_chars)
            end = text.find("\r")
            complete = end != -1 and all(s in st._rt_seen for s in range(-(-end // 4)))
            if complete or len(st._rt_seen) == 16:
                st.rt = (text[:end] if end != -1 else text).strip()


# ----------------------------------------------------------------- waveform helpers
def biphase_waveform(data_bits: list[int], prev_e: int, samples_per_bit: int) -> tuple[np.ndarray, int]:
    """Differentially encode, then turn each bit into a +/- (or -/+) pair of half-symbols."""
    half = samples_per_bit // 2
    out = np.empty(len(data_bits) * samples_per_bit)
    e = prev_e
    for k, d in enumerate(data_bits):
        e ^= d
        s = 1.0 if e else -1.0
        out[k * samples_per_bit:k * samples_per_bit + half] = s
        out[k * samples_per_bit + half:(k + 1) * samples_per_bit] = -s
    return out, e


class BiphaseBitRecovery:
    """Receiver side: complex baseband (8 samples per bit) -> data bits.

    1. Carrier phase: squaring a BPSK signal removes the data, leaving 2× the phase error;
       half its angle is the correction. Tracked block by block so small frequency errors are followed.
    2. Bit timing: try all 8 possible starting points, keep the one where the two halves of each
       symbol differ most (that's what a biphase symbol looks like when you're aligned).
    3. Decide each symbol, then undo the differential encoding (bit = this XOR previous).
    """

    SPS = 8
    PHASE_BLOCK = 64

    def __init__(self):
        self.pending = np.zeros(0, dtype=complex)
        self.phase = 0.0
        self.prev_symbol = 0

    def process(self, z: np.ndarray) -> list[int]:
        buf = np.concatenate([self.pending, z])
        n_blocks = len(buf) // self.PHASE_BLOCK
        if n_blocks == 0:
            self.pending = buf
            return []
        usable = buf[:n_blocks * self.PHASE_BLOCK].reshape(n_blocks, self.PHASE_BLOCK)
        r = np.empty(usable.shape)
        for i, blk in enumerate(usable):
            est = 0.5 * np.angle(np.sum(blk * blk))
            # pick est or est+π, whichever is closer to the previous estimate (no sudden flips)
            cands = np.array([est, est + np.pi, est - np.pi])
            diff = np.angle(np.exp(1j * (cands - self.phase)))
            self.phase = float(self.phase + diff[np.argmin(np.abs(diff))])
            r[i] = np.real(blk * np.exp(-1j * self.phase))
        r = r.ravel()

        sps, half = self.SPS, self.SPS // 2
        n_sym = len(r) // sps - 1
        best_o, best_m = 0, -1.0
        for o in range(sps):
            seg = r[o:o + n_sym * sps].reshape(n_sym, sps)
            metric = np.sum(np.abs(seg[:, :half].sum(1) - seg[:, half:].sum(1)))
            if metric > best_m:
                best_o, best_m = o, metric
        seg = r[best_o:best_o + n_sym * sps].reshape(n_sym, sps)
        symbols = (seg[:, :half].sum(1) - seg[:, half:].sum(1)) > 0
        bits = []
        for s in symbols.astype(int):
            bits.append(s ^ self.prev_symbol)
            self.prev_symbol = s
        consumed = best_o + n_sym * sps
        leftover = buf[consumed:]
        self.pending = leftover
        return bits
