# Raided SNES sample libraries

BRR instrument samples ripped from commercial SNES games' SPC sets, as chair
candidates for the soundtrack conversion (per the hybrid doctrine: Munt/MT-32
is the generative source for any program, but a hand-prepped SNES sample that
sounds better takes the chair — proven by r028's ToP horn/bass and r010's bass).

| dir | game | why raided |
|---|---|---|
| `tales_of_phantasia/` | Tales of Phantasia | Bak Sangwoo's source set; superb winds/strings/bass |
| `secret_of_mana/` | Secret of Mana | mallets, bells, flutes, world percussion |
| `chrono_trigger/` | Chrono Trigger | bells, strings, ethnic winds |
| `dkc2/` | Donkey Kong Country 2 | the classic mallet/marimba + tight kit raid |
| `final_fantasy_vi/` | Final Fantasy VI | orchestral set, celesta/music box, choir |

Format: AddMusicK-style `.brr` (2-byte little-endian loop-offset header + raw
BRR blocks) — `tad-compiler` consumes these verbatim with `loop: "none"`
(honors the header + per-block loop flags; verified byte-exact in the live SPC
sample directory, see SKILL.md knob 18).

Each dir's `manifest.json` records per sample: block/sample counts, loop
offset/length, estimated natural f0 at 32kHz playback (= TAD's `freq` field,
autocorrelation over the loop segment) with a confidence score, and which
songs in the set referenced it (`file:srcnN`). Sort by `found_in` ubiquity to
find a game's core instruments; high `f0_confidence` + looped = melodic
instrument, `oneshot` = percussion.

Audition WAVs (32kHz mono, loops sustained ~1.5s) are generated alongside in
`build/raid/wav/<game>/` — not committed; regenerate with the ripper.

Reproduce / extend to another game:

```
# 1. SPC set (zip) from zophar.net "Nintendo SNES (SPC)" → build/raid/spc/<game>/
# 2. Rip:
python tools/audio/spc_rip_brr.py build/raid/spc/<game> audio/samples/raid/<game> \
    --wav-dir build/raid/wav/<game>
```

Caveats: an SPC is a RAM snapshot — a sample whose RAM overlapped the echo
buffer in *every* song it appears in may be absent or torn (the ripper drops
echo-region candidates). Counts include per-song tuning/bank variants of the
same instrument; the manifest + audition WAVs are for browsing, the ear picks
the chair. Estimated f0 can lock onto a subharmonic on rich tones (e.g. ToP
organ reports 27.78Hz for a 500Hz patch) — always verify the octave when
declaring `freq`.

Provenance: ripped from commercial game audio (same provenance as the
`audio/samples/phantasia/` set used by the shipped r028/r010 arrangements).
