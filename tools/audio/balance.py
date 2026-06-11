#!/usr/bin/env python3
"""Per-channel mix balance: ours (TAD solo captures) vs Munt MT-32 solo refs.

  python tools/audio/balance.py --full-ours build/verify_echo/mix.wav

Each channel's share of its full mix, compared across renders:
  corr_dB = 20*log10( (rms_ref_solo/rms_ref_full) / (rms_ours_solo/rms_ours_full) )
Positive = our channel is too quiet relative to the reference balance; the
final column is the vol_scale multiplier that closes the gap.

Channel map, paths and defaults come from the conversion plan via solo_ab
(--plan etc., default r010). Requires solo captures in --out (run
`solo_ab.py render` first) and Munt solo refs in --ref-dir.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import solo_ab


def rms(p, a=2.0, b=None):
    d, sr = solo_ab.load(p)
    b = b if b is not None else len(d) / sr
    seg = d[int(a * sr):int(b * sr)]
    return float(np.sqrt((seg ** 2).mean()))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--full-ours', type=Path, required=True,
                        help='full-mix TAD render WAV')
    parser.add_argument('--full-ref', type=Path, default=None,
                        help='full-mix reference WAV (default: '
                             '<ref-dir>/*_reference.wav, unique match)')
    # unknown args (--plan, --ref-dir, --out, ...) pass through to solo_ab
    args, rest = parser.parse_known_args()
    solo_ab.load_config(solo_ab.parse_args(['render'] + rest))

    full_ref = args.full_ref
    if full_ref is None:
        cands = list(solo_ab.REF_DIR.glob('*_reference.wav'))
        if len(cands) != 1:
            sys.exit(f'--full-ref needed: {len(cands)} candidates in {solo_ab.REF_DIR}')
        full_ref = cands[0]

    full_ours_rms = rms(args.full_ours)
    full_ref_rms = rms(full_ref)

    print(f'{"ch":>2} {"label":20s} {"ours_dB":>8} {"ref_dB":>8} {"corr_dB":>8}  vol_scale')
    for letter, (chs, label) in solo_ab.CHANNELS.items():
        ours_p = solo_ab.OUT / f'tad_{letter}.wav'
        if not ours_p.exists():
            print(f'{letter:>2} {label:20s} (no solo capture)')
            continue
        ours = rms(ours_p)
        ref = np.sqrt(sum(rms(solo_ab.REF_DIR / f'solo_ch{c}.wav') ** 2
                          for c in chs))
        share_o = ours / full_ours_rms
        share_r = ref / full_ref_rms
        corr = 20 * np.log10(share_r / share_o)
        scale = 10 ** (corr / 20)
        print(f'{letter:>2} {label:20s} {20*np.log10(share_o):8.1f} '
              f'{20*np.log10(share_r):8.1f} {corr:+8.1f}  {scale:.2f}')


if __name__ == '__main__':
    main()
