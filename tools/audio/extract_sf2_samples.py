"""Extract named samples from a SoundFont (.sf2) to 32 kHz 16-bit mono WAVs.

The Roland SC-55 soundfont at `E:/gh/scummvm/dists/soundfonts/Roland_SC-55.sf2`
contains ~500 authentic General MIDI samples. This script pulls the ones we
want and writes them in the format tad-compiler's `wav2brr` expects (32 kHz
mono signed-16). SF2 samples are typically 22050 Hz — we resample with simple
linear interpolation, which is fine for percussion + general-purpose use.

Usage:
    python extract_sf2_samples.py            # default SC-55 + SMI drums
    python extract_sf2_samples.py --list     # list every sample name in the SF2

Pick targets by matching on sample name; a given name may appear multiple
times at different pitches (e.g. A_SAX53, A_SAX63, ...) — we take the lowest.
"""
from __future__ import annotations

import argparse
import struct
import sys
import wave
from pathlib import Path

SF2_PATH = Path(r"E:\gh\scummvm\dists\soundfonts\Roland_SC-55.sf2")
OUT_DIR = Path(__file__).resolve().parents[2] / "audio" / "samples" / "instruments"

# Write WAVs at the SF2's native rate (22050 Hz). The SPC DSP resamples
# per-voice based on the project's `freq:` value, so the WAV rate doesn't
# affect final playback — but it DOES affect auditioning: a WAV written at
# 22050 Hz will sound like the SC-55 original when opened in VLC, whereas
# an artificially-resampled 32000 Hz WAV would play 1.45× too fast.
TARGET_SR = 22_050

# Each entry: output filename (without .wav) -> (SF2 sample name, trim_samples)
# trim_samples caps the sample length in source frames to keep BRR size down;
# None = keep whole sample. Short percussive samples are fine at full length;
# tonal/looping samples benefit from trimming to one cycle if we're not going
# to support proper loop points.
# Trim values are in SOURCE (22050 Hz) frames. 2205 ≈ 100 ms. Limiting sample
# length keeps BRR-encoded size down; drums only need to ring long enough for
# the ear to register the hit before the next one (in dense patterns, 50-100 ms
# per hit is plenty). Set trim=None to keep the whole sample.
DRUM_EXTRACTIONS = {
    # Drums actually referenced by SOUN 010's H track at 32nd-grid resolution.
    "kick":     ("KICK264",  None),   # 50 ms, keep full
    "snare":    ("FATSD60A", 2200),   # cap at ~100 ms (source is ~197 ms)
    "conga_hi": ("OCNGA60",  1760),   # cap at ~80 ms
    "bongo_hi": ("HBNGO60",  1300),   # cap at ~60 ms; distinct from conga
    "claves":   ("CLAVE60",  None),   # 21 ms, keep full
}

# Melodic samples — each is a tonal instrument with SF2 loop points. We
# extract the sample FROM start UP TO end_loop so the SNES DSP can loop the
# tail indefinitely (held notes actually sustain instead of dying at sample
# end). Trim only kicks in if end_loop is beyond the trim value.
#
# Per-slot mapping (matches the role each MML channel plays in SOUN 010):
#   lead          -> SHAKU70 (shakuhachi, MI1 ch1)
#   chord         -> GLKN285 (glockenspiel, MI1 ch3)
#   arp           -> SITAR76 (sitar, MI1 ch4)
#   bass          -> ACOBS43A (acoustic bass, MI1 ch5 — MIDI mislabels it
#                              Alto Sax but the range is bass)
#   ornament      -> VIOLN76  (violin, MI1 ch7 "Fiddle")
#   fx            -> ATMOS60A (atmosphere, MI1 ch2 "FX 8 sci-fi")
MELODIC_EXTRACTIONS = {
    # Pick the SF2 variant whose native pitch lets the sample cover the
    # MIDI channel's playable range without per-channel MML transpose. SC-55
    # ships each instrument at 2-3 pitch points because the DSP can only
    # shift a single sample up ~2 octaves before it sounds chipmunk-like.
    #
    # Target ranges derived from MI1 SOUN 010 analysis:
    #   ch1 shakuhachi lead: MIDI 38-93 → need high-variant
    #   ch3 glockenspiel:    MIDI 43-83 → mid-variant
    #   ch4 sitar:           MIDI 48-89 → high-variant
    #   ch5 acobass (bass):  MIDI 26-43 → low-variant
    #   ch2 atmosphere fx:   MIDI 48-73 → mid-variant
    "shakuhachi":   ("SHAKU78",  None),   # MIDI 78 native (was 70)
    "glockenspiel": ("GLKN285",  None),   # MIDI 85 native
    "sitar":        ("SITAR76",  None),   # MIDI 76 native (mid-range)
    "acobass":      ("ACOBS43A", None),   # MIDI 43 native — bass range
    "atmosphere":   ("ATMOS60A", None),   # MIDI 60 native
}


def linear_resample(src: list[int], src_rate: int, dst_rate: int) -> list[int]:
    """Stretch/compress by simple linear interpolation. Adequate for drums."""
    if src_rate == dst_rate:
        return list(src)
    ratio = src_rate / dst_rate
    n_out = int(len(src) * dst_rate / src_rate)
    out = []
    for i in range(n_out):
        pos = i * ratio
        i0 = int(pos)
        i1 = min(i0 + 1, len(src) - 1)
        frac = pos - i0
        sample = src[i0] * (1 - frac) + src[i1] * frac
        out.append(int(round(sample)))
    return out


def write_wav(path: Path, samples: list[int], rate: int = TARGET_SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for s in samples:
        s = max(-32768, min(32767, s))
        frames += struct.pack("<h", s)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def extract_sample(sf_sample, trim: int | None) -> tuple[list[int], int]:
    """Return (16-bit PCM, source_sample_rate) for an sf2utils Sample object.

    The library exposes 16-bit samples via `.raw_sample_data` (bytes) and the
    rate via `.sample_rate`. `.start`/`.end` are frame indices — use the
    object's own slicing where possible but fall back to raw bytes if not.
    """
    data = bytes(sf_sample.raw_sample_data)
    # raw_sample_data is already signed-16 little-endian PCM of this sample
    pcm = list(struct.unpack(f"<{len(data) // 2}h", data))
    if trim is not None and len(pcm) > trim:
        pcm = pcm[:trim]
    return pcm, sf_sample.sample_rate


def find_sample_by_name(sf, name: str):
    matches = [s for s in sf.samples if s.name == name]
    if not matches:
        matches = [s for s in sf.samples if name.lower() in s.name.lower()]
    return matches[0] if matches else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sf2", type=Path, default=SF2_PATH)
    ap.add_argument("--outdir", type=Path, default=OUT_DIR)
    ap.add_argument("--list", action="store_true",
                    help="List all sample names in the SF2 and exit")
    args = ap.parse_args()

    # Import inside main so --help works without the dep.
    from sf2utils.sf2parse import Sf2File

    # Keep the file open through sample extraction — sf2utils reads raw PCM
    # lazily via the parser's file handle, so closing the file before reading
    # raises "seek of closed file".
    with open(args.sf2, "rb") as f:
        sf = Sf2File(f)

        if args.list:
            for n in sorted({s.name for s in sf.samples}):
                print(n)
            return 0

        args.outdir.mkdir(parents=True, exist_ok=True)
        rc = 0

        def do_extract(out_name, sf_name, trim, is_melodic):
            nonlocal rc
            sample = find_sample_by_name(sf, sf_name)
            if sample is None:
                print(f"skip {out_name}: {sf_name!r} not in soundfont", file=sys.stderr)
                rc = 1
                return None
            # sf2utils reports start_loop/end_loop RELATIVE to the sample
            # (both are frame indices in [0, duration]). Don't re-subtract.
            loop_start = getattr(sample, 'start_loop', None)
            loop_end = getattr(sample, 'end_loop', None)
            if is_melodic and loop_start is not None and loop_end is not None \
                    and loop_end > loop_start > 0:
                # Slice to [0, loop_end]: attack + body + loop-point region.
                # SPC DSP will loop tail by jumping back to loop_start each
                # time the BRR stream hits its END+LOOP flag at loop_end.
                cap = loop_end if trim is None else min(trim, loop_end)
                pcm, src_rate = extract_sample(sample, cap)
                loop_start_dst = int(round(loop_start * TARGET_SR / src_rate))
            else:
                pcm, src_rate = extract_sample(sample, trim)
                loop_start_dst = None

            resampled = linear_resample(pcm, src_rate, TARGET_SR)
            # BRR encoder requires a multiple of 16 frames; pad with silence
            # AFTER the loop region so it doesn't land inside the loop.
            pad = (-len(resampled)) % 16
            resampled.extend([0] * pad)
            # Also snap loop_start_dst to a 16-sample boundary (BRR block).
            if loop_start_dst is not None:
                loop_start_dst = (loop_start_dst // 16) * 16
            dst = args.outdir / f"{out_name}.wav"
            write_wav(dst, resampled)
            note = f"loop@{loop_start_dst}" if loop_start_dst is not None else "no-loop"
            print(f"  {out_name:12s} <- {sf_name:10s}  "
                  f"{src_rate}->{TARGET_SR} Hz, {len(pcm)}->{len(resampled)} frames  {note}")
            return loop_start_dst

        loop_points: dict[str, int] = {}
        for out_name, (sf_name, trim) in DRUM_EXTRACTIONS.items():
            do_extract(out_name, sf_name, trim, is_melodic=False)
        for out_name, (sf_name, trim) in MELODIC_EXTRACTIONS.items():
            lp = do_extract(out_name, sf_name, trim, is_melodic=True)
            if lp is not None:
                loop_points[out_name] = lp

        # Write a small JSON sidecar so the caller (and smi.terrificaudio
        # maintenance scripts) know where each sample's loop point is.
        lp_path = args.outdir / "_loop_points.json"
        import json as _json
        lp_path.write_text(_json.dumps(loop_points, indent=2) + "\n")
        print(f"\nloop points -> {lp_path}")
        return rc


if __name__ == "__main__":
    sys.exit(main())
