#!/usr/bin/env python3
"""Decode + fingerprint MI1 SCUMM v5 `soun_NNN` resources.

This is the authoritative replacement for "guess the tune by the room number
baked into the filename". It walks the on-disk `SOU ` container exactly the way
ScummVM's resource reader does, extracts the playable MIDI sub-chunk, and emits
a STABLE MUSICAL FINGERPRINT so two SCUMM sound IDs that hold the same piece of
music fingerprint identically -- regardless of which physical room block they
were extracted from.

ON-DISK FORMAT (authority: ScummVM engines/scumm/sound.cpp / imuse/imuse.cpp)
---------------------------------------------------------------------------
A music/sfx resource is a tagged container. All chunk sizes are big-endian u32.

  'SOU ' <u32 size>                          container (sound.cpp:1305)
    then a sequence of device sub-chunks, each:  <tag> <u32 size> <body>
      'ROL '  Roland MT-32 / LAPC MIDI         pri 3/5   (sound.cpp:1330)
      'GMD '  General MIDI                      pri 4     (sound.cpp:1341)
      'ADL '  AdLib / OPL2 MIDI                 pri 1/10  (sound.cpp:1321)
      'SBL '  SoundBlaster raw VOC (digital sfx) pri 15   (sound.cpp:1318)
      'SPK '  PC speaker                         pri 11   (sound.cpp:1347)
      'AMI ' / 'TOWS' / 'MAC '  platform variants
  The interpreter scans every sub-chunk and plays the HIGHEST-priority one its
  output device supports (sound.cpp:1379 `if (pri > best_pri)`).

  A MIDI device sub-chunk ('ROL '/'GMD '/'ADL ') wraps:
      'MDhd' <u32 size> <body>   16-byte iMUSE header; in MI1 it's all zeroes
                                 and unused (imuse_player.cpp:239).
      'MThd' ... 'MTrk' ...      a STANDARD SMF (imuse.cpp:122-145 looks for the
                                 'MThd' tag; findStartOfSound, imuse.cpp:88-148).
  The actual notes live in the SMF MTrk stream. iMUSE plays the MThd/MTrk
  directly through the MIDI parser (imuse_player.cpp:214-231).

  'SBL ' wraps 'AUhd'/'AUdt' raw 8-bit unsigned PCM (sound.cpp:234-316). These
  are digital sound EFFECTS, not music.

FINGERPRINT
-----------
For MIDI resources we canonicalize the MTrk event stream into a tempo- and
header-independent token list: per delta-time, the set of (channel, pitch,
on/off) events, plus program changes. Hashing that gives `tune_fp` -- the SAME
tune fingerprints the same even if two copies differ in an MDhd byte or a
trailing pad. We also report `bytes_fp` (sha1 of the chosen sub-chunk) for exact
byte-identity, and `notes` (note_on count) as a cheap sanity signal.

USAGE
-----
  python3 tools/scumm/decode_sound.py soun_098_room070            # one resource
  python3 tools/scumm/decode_sound.py --all                       # full table
  python3 tools/scumm/decode_sound.py --all --dups                # group by tune
  python3 tools/scumm/decode_sound.py --encd-scan                 # room->startSound map
  python3 tools/scumm/decode_sound.py --id 98 --verbose           # chunk dump for id 98
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOUND_DIR = ROOT / "data" / "scumm_extracted" / "sounds"
ROOMS_DIR = ROOT / "data" / "scumm_extracted" / "rooms"

# ScummVM SOU sub-chunk priorities (engines/scumm/sound.cpp:1314-1350).
# We pick the music chunk the way a native-MT32 CD setup would: ROL beats GMD
# beats ADL. SBL (digital sfx) is reported but never treated as "the tune".
MIDI_PRIORITY = {b"ROL ": 5, b"GMD ": 4, b"AMI ": 3, b"MAC ": 2, b"ADL ": 1}
SBL_TAG = b"SBL "
SPK_TAG = b"SPK "


# --------------------------------------------------------------------------- #
#  SOU container walking                                                       #
# --------------------------------------------------------------------------- #

def iter_sou_chunks(data: bytes):
    """Yield (tag, offset_of_body, body_bytes) for each sub-chunk of a SOU
    container, per sound.cpp:1305-1386. Sizes are big-endian and EXCLUDE the
    8-byte tag+size header (matching `size = readUint32BE() + 8`)."""
    if len(data) < 16 or data[:4] != b"SOU ":
        return
    # SOU header: 'SOU ' <u32 total_size>. Body starts at offset 8.
    pos = 8
    end = len(data)
    while pos + 8 <= end:
        tag = data[pos:pos + 4]
        size = struct.unpack_from(">I", data, pos + 4)[0]
        body_start = pos + 8
        body_end = min(body_start + size, end)
        yield tag, body_start, data[body_start:body_end]
        # advance by header + payload (sound.cpp: pos += readUint32BE()+8)
        pos = body_start + size


def pick_music_chunk(data: bytes):
    """Return (tag, body) of the highest-priority MIDI sub-chunk, or None.
    Mirrors sound.cpp's best_pri selection but restricted to MIDI devices."""
    best = None
    best_pri = -1
    for tag, _off, body in iter_sou_chunks(data):
        pri = MIDI_PRIORITY.get(tag, -1)
        if pri > best_pri:
            best_pri = pri
            best = (tag, body)
    return best


def chunk_inventory(data: bytes):
    """List every sub-chunk tag present (for diagnostics)."""
    return [tag.decode("latin1") for tag, _o, _b in iter_sou_chunks(data)]


def classify(data: bytes) -> str:
    if len(data) < 12 or data[:4] != b"SOU ":
        return "stub"
    tags = {t.encode() if isinstance(t, str) else t
            for t, _o, _b in iter_sou_chunks(data)}
    if any(t in MIDI_PRIORITY for t in tags):
        return "midi"
    if SBL_TAG in tags:
        return "sbl"
    if SPK_TAG in tags:
        return "spk"
    return "unknown"


# --------------------------------------------------------------------------- #
#  SMF extraction + canonical fingerprint                                     #
# --------------------------------------------------------------------------- #

def extract_smf(midi_chunk_body: bytes) -> bytes | None:
    """The MIDI device body is MDhd <hdr> then a standard SMF. Find 'MThd'."""
    idx = midi_chunk_body.find(b"MThd")
    if idx < 0:
        return None
    return midi_chunk_body[idx:]


def _read_vlq(buf: bytes, pos: int):
    val = 0
    while True:
        b = buf[pos]
        pos += 1
        val = (val << 7) | (b & 0x7F)
        if not (b & 0x80):
            break
    return val, pos


def smf_note_tokens(smf: bytes):
    """Parse the SMF into a canonical (channel, status_class, key) token list,
    ordered by absolute tick then channel then key. Tempo/meta/header bytes are
    excluded so the fingerprint depends only on the musical content.

    Returns (tokens, note_on_count, programs, n_tracks) or None on parse error.

    This is a self-contained SMF reader (no mido dependency) so the tool runs
    anywhere. It handles SMF type 0/1/2 by merging all tracks on an absolute
    tick timeline -- which is what we want for a content fingerprint."""
    if smf[:4] != b"MThd":
        return None
    hdr_len = struct.unpack_from(">I", smf, 4)[0]
    fmt, ntrk, _div = struct.unpack_from(">HHH", smf, 8)
    pos = 8 + hdr_len
    events = []  # (abs_tick, channel, cls, key)  cls: 0=off 1=on 2=prog
    programs = set()
    note_on = 0
    tracks_seen = 0
    while pos + 8 <= len(smf):
        if smf[pos:pos + 4] != b"MTrk":
            break
        trk_len = struct.unpack_from(">I", smf, pos + 4)[0]
        tpos = pos + 8
        tend = min(tpos + trk_len, len(smf))
        abs_tick = 0
        running = 0
        tracks_seen += 1
        while tpos < tend:
            delta, tpos = _read_vlq(smf, tpos)
            abs_tick += delta
            if tpos >= tend:
                break
            status = smf[tpos]
            if status & 0x80:
                running = status
                tpos += 1
            else:
                status = running
            ev = status & 0xF0
            ch = status & 0x0F
            if status == 0xFF:  # meta
                mtype = smf[tpos]; tpos += 1
                mlen, tpos = _read_vlq(smf, tpos)
                tpos += mlen
            elif status in (0xF0, 0xF7):  # sysex
                slen, tpos = _read_vlq(smf, tpos)
                tpos += slen
            elif ev in (0x80, 0x90):  # note off / note on
                key = smf[tpos]; vel = smf[tpos + 1]; tpos += 2
                if ev == 0x90 and vel > 0:
                    events.append((abs_tick, ch, 1, key))
                    note_on += 1
                else:
                    events.append((abs_tick, ch, 0, key))
            elif ev in (0xA0, 0xB0, 0xE0):  # poly-AT / CC / pitch-bend: 2 data
                tpos += 2
            elif ev == 0xC0:  # program change: 1 data
                prog = smf[tpos]; tpos += 1
                programs.add(prog)
                events.append((abs_tick, ch, 2, prog))
            elif ev == 0xD0:  # channel AT: 1 data
                tpos += 1
            else:
                # Unknown/garbage -> bail this track
                break
        pos = tpos if tpos > pos else tend
        pos = (pos + 7) // 1  # no-op; keep linear
        pos = tend  # always jump to declared track end (robust to padding)

    events.sort()
    return events, note_on, sorted(programs), tracks_seen


def fingerprint(data: bytes):
    """Return a dict describing this resource's identity, or {'kind':...} only
    for non-music. Music dict has: kind, midi_tag, bytes_fp, tune_fp, notes,
    programs, ticks, chunks."""
    info = {"kind": classify(data), "chunks": chunk_inventory(data)}
    if info["kind"] != "midi":
        info["bytes_fp"] = hashlib.sha1(data).hexdigest()[:16]
        return info
    picked = pick_music_chunk(data)
    if picked is None:
        info["kind"] = "midi-noplay"
        return info
    tag, body = picked
    info["midi_tag"] = tag.decode("latin1").strip()
    info["bytes_fp"] = hashlib.sha1(body).hexdigest()[:16]
    smf = extract_smf(body)
    if smf is None:
        info["tune_fp"] = None
        return info
    parsed = smf_note_tokens(smf)
    if parsed is None:
        info["tune_fp"] = None
        return info
    events, note_on, programs, ntrk = parsed
    # Canonical token stream: delta-encode ticks so identical music with a
    # different absolute start offset still matches; include channel/class/key.
    toks = bytearray()
    prev = 0
    for tick, ch, cls, key in events:
        d = tick - prev
        prev = tick
        toks += struct.pack(">IBBB", d & 0xFFFFFFFF, ch, cls, key)
    info["tune_fp"] = hashlib.sha1(bytes(toks)).hexdigest()[:16]
    info["notes"] = note_on
    info["programs"] = programs
    info["n_tracks"] = ntrk
    return info


# --------------------------------------------------------------------------- #
#  ENCD startSound scanner (room -> music it requests)                         #
# --------------------------------------------------------------------------- #

# SCUMM v5 opcode 0x1C = startSound(byte id); 0x9C = startSound(var). Authority:
# tools/scumm/opcodes_v5.py table[0x1C]='startSound', table[0x9C]='startSound'.
def scan_startsound_immediates(script: bytes):
    """Heuristic but reliable for ENCD: report every 0x1C immediate-arg
    startSound found at a byte boundary. ENCD entry scripts are tiny and the
    music call is almost always the first or near-first opcode. We report ALL
    0x1C <id> occurrences; the caller cross-references with the resource table."""
    out = []
    for i in range(len(script) - 1):
        if script[i] == 0x1C:
            out.append(script[i + 1])
    return out


def encd_scan():
    """For each room dir, decode ENCD startSound(immediate) calls."""
    rows = []
    for d in sorted(ROOMS_DIR.iterdir()):
        encd = d / "scripts" / "encd.bin"
        if not encd.is_file():
            continue
        m = re.match(r"room_(\d+)", d.name)
        room = int(m.group(1)) if m else -1
        ids = scan_startsound_immediates(encd.read_bytes())
        if ids:
            rows.append((room, d.name, ids))
    return rows


# --------------------------------------------------------------------------- #
#  Driver                                                                      #
# --------------------------------------------------------------------------- #

def parse_stem(path: Path):
    stem = path.stem  # soun_NNN_roomMMM
    sid = int(stem.split("_")[1])
    m = re.search(r"room(\d+)", stem)
    room = int(m.group(1)) if m else None
    return sid, room


def all_resources():
    out = []
    for p in sorted(SOUND_DIR.glob("soun_*.bin")):
        sid, room = parse_stem(p)
        out.append((sid, room, p))
    return out


def cmd_one(path: Path, verbose: bool):
    data = path.read_bytes()
    info = fingerprint(data)
    print(f"{path.name}  ({len(data)} bytes)")
    print(f"  kind        : {info['kind']}")
    print(f"  chunks      : {info.get('chunks')}")
    if info["kind"] == "midi":
        print(f"  midi device : {info.get('midi_tag')}")
        print(f"  notes       : {info.get('notes')}")
        print(f"  programs    : {info.get('programs')}")
        print(f"  tune_fp     : {info.get('tune_fp')}")
    print(f"  bytes_fp    : {info.get('bytes_fp')}")
    if verbose and info["kind"] == "midi":
        for tag, off, body in iter_sou_chunks(data):
            print(f"    sub-chunk {tag.decode('latin1')!r:8s} at 0x{off-8:06x}  "
                  f"len={len(body)}")


def cmd_all(dups: bool):
    rows = []
    for sid, room, p in all_resources():
        info = fingerprint(p.read_bytes())
        rows.append((sid, room, p.name, info))
    if dups:
        groups = defaultdict(list)
        for sid, room, name, info in rows:
            if info["kind"] == "midi" and info.get("tune_fp"):
                groups[info["tune_fp"]].append((sid, room, info.get("notes")))
        print("=== Distinct MIDI tunes (by canonical fingerprint) ===")
        for fp, members in sorted(groups.items(),
                                  key=lambda kv: -len(kv[1])):
            ids = ", ".join(f"{s}(rm{r})" for s, r, _n in sorted(members))
            notes = members[0][2]
            print(f"  tune {fp}  notes={notes:<5} x{len(members):<2} : {ids}")
        return
    print(f"{'id':>4} {'room':>5} {'kind':<9} {'dev':<4} {'notes':>5} "
          f"{'tune_fp':16} {'bytes_fp':16}  programs")
    for sid, room, name, info in rows:
        if info["kind"] == "midi":
            print(f"{sid:>4} {str(room):>5} {info['kind']:<9} "
                  f"{info.get('midi_tag',''):<4} {str(info.get('notes','')):>5} "
                  f"{str(info.get('tune_fp')):16} {info.get('bytes_fp',''):16}  "
                  f"{info.get('programs')}")
        else:
            print(f"{sid:>4} {str(room):>5} {info['kind']:<9} "
                  f"{'':<4} {'':>5} {'':16} {info.get('bytes_fp',''):16}")


def cmd_encd():
    print("=== ENCD startSound(immediate) calls per room ===")
    print("(opcode 0x1C; cross-reference id with the resource table)")
    for room, name, ids in encd_scan():
        print(f"  room {room:>3} {name:<22} startSound -> {ids}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="resource stem or filename, e.g. soun_098_room070")
    ap.add_argument("--id", type=int, help="resource by SCUMM id")
    ap.add_argument("--all", action="store_true", help="table of every resource")
    ap.add_argument("--dups", action="store_true", help="with --all, group by tune fingerprint")
    ap.add_argument("--encd-scan", action="store_true", help="room->startSound call map")
    ap.add_argument("--verbose", action="store_true", help="dump sub-chunk offsets")
    args = ap.parse_args()

    if args.encd_scan:
        cmd_encd()
        return 0
    if args.all:
        cmd_all(args.dups)
        return 0
    if args.id is not None:
        matches = [p for s, r, p in all_resources() if s == args.id]
        if not matches:
            print(f"no resource with id {args.id}", file=sys.stderr)
            return 1
        cmd_one(matches[0], args.verbose)
        return 0
    if args.name:
        cand = SOUND_DIR / args.name
        if not cand.is_file() and not args.name.endswith(".bin"):
            cand = SOUND_DIR / (args.name + ".bin")
        if not cand.is_file():
            globbed = list(SOUND_DIR.glob(args.name + "*"))
            if globbed:
                cand = globbed[0]
        if not cand.is_file():
            print(f"not found: {args.name}", file=sys.stderr)
            return 1
        cmd_one(cand, args.verbose)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
