#!/usr/bin/env python3
"""Generate per-song .terrificaudio + plan.json for the MI1 soundtrack batch.

  python tools/audio/gen_song_projects.py            # all songs
  python tools/audio/gen_song_projects.py r085_melee # one song

Each SPEC below is one song's arrangement: voice assignments (recon-driven),
chair instruments (hybrid doctrine: proven winners carry over -- ph_bass for
prog 65, ph_horn for prog 91, MT-32 flute/organ/marimba/xylo/bottle; new
programs use the auto-extracted mt32_pNN bank), drum maps, echo room, tempo
frame. Mix knobs start from the r010/r028-approved priors; the ear pass tunes.

Reads: build/soundtrack_scan/recon.json (octave ranges),
       build/extract_jobs_report.json (+2,3) (new-instrument metadata).
Writes: audio/songs/<song>/<song>.{mid,terrificaudio,plan.json}
"""
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN = REPO / 'build/soundtrack_scan'
SONGS_DIR = REPO / 'audio/songs'

RELEASE_RATES = {"lead": "E21", "doubler": "E20", "bass": "E23",
                 "pad": "E21", "arp": "E20", "ornament": "E20"}
VOL_PRIORS = {"lead": 1.10, "doubler": 0.90, "pad": 0.55, "arp": 0.80,
              "ornament": 0.60, "bass": 1.15, "drums": 1.15}
ROOMS = {
    "bloom":    {"echo_feedback": 108, "echo_volume": 24},   # r010 vista
    "interior": {"echo_feedback": -86, "echo_volume": 36},   # r028 bar room
}

# Static instrument templates (proven entries from r010/r028 projects).
# Octave ranges are filled per song from the channel ranges that use them.
LIB = {
    'mt32_lead_flute': dict(source='../../samples/instruments/mt32_lead_flute.wav',
                            freq=525.0, loop='loop_with_filter', loop_setting=6400,
                            envelope='adsr 15 2 7 4'),
    'mt32_organ':      dict(source='../../samples/instruments/mt32_organ.wav',
                            freq=330.275, loop='loop_with_filter', loop_setting=4800,
                            envelope='adsr 15 1 7 0'),
    'mt32_marimba':    dict(source='../../samples/instruments/mt32_marimba.wav',
                            freq=375.0, loop='loop_with_filter', loop_setting=1600,
                            envelope='adsr 15 1 0 0'),
    'mt32_xylophone':  dict(source='../../samples/instruments/mt32_xylophone.wav',
                            freq=666.667, loop='loop_with_filter', loop_setting=1600,
                            envelope='adsr 15 2 0 0'),
    'mt32_bottle':     dict(source='../../samples/instruments/mt32_bottle.wav',
                            freq=660.0, loop='loop_with_filter', loop_setting=4800,
                            envelope='adsr 15 2 7 10'),
    'ph_bass':         dict(source='../../samples/phantasia/Phantasia_Soft_Bass.brr',
                            freq=111.111, loop='none', envelope='adsr 15 7 7 12'),
    'ph_horn':         dict(source='../../samples/phantasia/Phantasia_French_Horn.brr',
                            freq=500.0, loop='none', envelope='adsr 15 7 7 0'),
}
DRUM_LIB = ['mt32_drum_kick', 'mt32_drum_rim', 'mt32_drum_bongo',
            'mt32_drum_conga_med', 'mt32_drum_conga_hi', 'mt32_drum_claves',
            'mt32_drum_cabasa']

# Standard drum placeholder -> instrument resolution; per-spec 'drums' lists
# the placeholders the song uses, 'drum_note_overrides' handles MT-32 keys
# the GM map lacks or collapses, null = key silent on MT-32 (dropped).
STD_DRUMS = {
    'drum_kick': 'mt32_drum_kick',       # 35,36
    'drum_rim': 'mt32_drum_rim',         # 37 via override
    'drum_snare': 'mt32_drum_n38',       # 38
    'drum_hat': 'mt32_drum_n42',         # 42
    'drum_hat_pedal': 'mt32_drum_n44',   # 44 via override
    'drum_tom': 'mt32_drum_n45',         # 41,43,45,47 (consolidated)
    'drum_tom_hi': 'mt32_drum_n48',      # 48,50 via override
    'drum_bongo_hi': 'mt32_drum_n60',    # 60 via override
    'drum_bongo': 'mt32_drum_bongo',     # 61
    'drum_conga_med': 'mt32_drum_conga_med',  # 62
    'drum_conga_hi': 'mt32_drum_conga_hi',    # 63
    'drum_conga_lo': 'mt32_drum_n64',    # 64
    'drum_timbale': 'mt32_drum_n66',     # 66
    'drum_agogo_hi': 'mt32_drum_n67',    # 67
    'drum_agogo_lo': 'mt32_drum_n68',    # 68
    'drum_cabasa': 'mt32_drum_cabasa',   # 69
    'drum_guiro': 'mt32_drum_n73',       # 73
    'drum_claves': 'mt32_drum_claves',   # 75
}
STD_OVERRIDES = {  # applied per song, filtered to the notes it uses
    '37': 'drum_rim', '44': 'drum_hat_pedal',
    '48': 'drum_tom_hi', '50': 'drum_tom_hi',
    '60': 'drum_bongo_hi', '62': 'drum_conga_med', '63': 'drum_conga_hi',
    '64': 'drum_conga_lo', '67': 'drum_agogo_hi', '68': 'drum_agogo_lo',
    '69': 'drum_cabasa', '73': 'drum_guiro',
    '52': None, '55': None, '57': None, '76': None,   # silent on MT-32
}

# (spc, midi_ch, reduction, role, extra-knobs-dict)
SPECS = {
 'r002_monkey1': dict(
    mid='room_002_monkey-1__soun_001', title='Monkey Head Vista', room='bloom',
    chairs={'arp': 'mt32_marimba', 'ornament': 'mt32_p117', 'bass': 'ph_bass'},
    assignments=[(1, 2, 'top', 'arp'), (2, 2, 'bottom', 'arp'),
                 (3, 3, 'top', 'ornament'), (4, 1, 'top', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[37, 41, 43, 45, 48, 60, 61, 62, 63, 64, 73, 75, 76],
    note=('ch4 prog117 is a melodic tom riding one pitch (n48) -- extracted '
          'as a pitched one-shot. Toms 41/43/45 consolidated to n45.')),
 'r019_shdeck': dict(
    mid='room_019_sh-deck__soun_003', title='Ship Deck', room='bloom',
    chairs={'lead': 'mt32_lead_flute', 'doubler': 'mt32_lead_flute',
            'pad': 'mt32_organ', 'arp': 'mt32_marimba', 'bass': 'ph_bass'},
    assignments=[(1, 1, 'top', 'lead'), (2, 2, 'top', 'doubler'),
                 (3, 3, 'top', 'pad'), (4, 3, 'bottom', 'pad'),
                 (5, 4, 'top', 'arp'), (6, 5, 'top', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 62, 63], vibrato={'lead': '30,2,12'}),
 'r034_highstreet': dict(
    mid='room_034_high-stre__soun_002', title='Mêlée High Street',
    room='bloom',
    chairs={'pad': 'mt32_organ', 'bass': 'ph_bass'},
    assignments=[(1, 2, 'chord_top', 'pad', {'pan': 84}),
                 (2, 2, 'chord_bottom', 'pad', {'pan': 84}),
                 (3, 4, 'mono', 'bass'), (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 42, 43, 45, 47, 60, 61, 62, 63],
    note=('ch1 is a single doubled note; dropped. chord_top/chord_bottom kill '
          'the chord-spill ghosts; mono bass keeps true onsets. v3 LESSON '
          '(Chad: "yuck! damnforest sounds better"): the full atmosphere '
          'treatment -- hard pan 107, dry interior room, E25/E26 releases, '
          'measured vols -- read sterile and broken-stereo next to the lush '
          'bloom. The MT-32 pans through a DIFFUSE reverb that fills the '
          'opposite side; a hard dry SNES pan does not. Now: exact '
          'damnforest treatment (bloom room, default releases, prior vols) '
          'plus ONE variable, gentle organ pan 84.')),
 'r038_lookout': dict(
    mid='room_038_lookout__soun_001', title='The Lookout', room='bloom',
    chairs={'pad': 'mt32_organ', 'arp': 'mt32_marimba', 'bass': 'ph_bass'},
    assignments=[(1, 1, 'top', 'pad'), (2, 1, 'bottom', 'pad'),
                 (3, 2, 'top', 'arp'), (4, 3, 'top', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 62, 63]),
 'r041_kitchen': dict(
    mid='room_041_kitchen__soun_004', title='Scumm Bar Kitchen', room='interior',
    chairs={'lead': 'mt32_p107', 'doubler': 'mt32_p107', 'bass': 'mt32_p66',
            'pad': 'mt32_p90', 'ornament': 'mt32_p27'},
    assignments=[(1, 1, 'top', 'lead'), (2, 2, 'top', 'doubler'),
                 (3, 3, 'top', 'bass'), (4, 4, 'top', 'pad'),
                 (5, 5, 'top', 'ornament'), (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 38, 69, 75]),
 'r051_circus': dict(
    mid='room_051_circus-te__soun_003', title='Fettucini Circus', room='bloom',
    chairs={'lead': 'mt32_bottle', 'doubler': 'mt32_p90', 'bass': 'ph_bass',
            'pad': 'mt32_organ', 'ornament': 'mt32_p78', 'arp': 'mt32_marimba'},
    assignments=[(1, 1, 'top', 'lead'), (2, 2, 'top', 'doubler'),
                 (3, 3, 'top', 'bass'), (4, 4, 'top', 'pad'),
                 (5, 5, 'top', 'ornament'), (6, 6, 'top', 'arp'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[38, 44, 67, 68, 69]),
 'r058_damnforest': dict(
    mid='room_058_damnfores__soun_004', title='Forest', room='bloom',
    chairs={'pad': 'mt32_organ', 'doubler': 'mt32_p34', 'bass': 'ph_bass'},
    assignments=[(1, 2, 'chord_top', 'pad'), (2, 2, 'chord_bottom', 'pad'),
                 (3, 3, 'top', 'doubler'), (4, 4, 'mono', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 42, 43, 45, 47, 60, 61, 62, 63],
    note=('ch1 is a single doubled note; dropped. Same arrangement as '
          'highstreet: chord-split organ + mono bass (see r034 note).')),
 'r059_stans': dict(
    mid='room_059_stans__soun_000', title="Stan's Previously Owned Vessels",
    room='interior', tempo=42.5, timer=118,
    chairs={'lead': 'mt32_p8', 'doubler': 'mt32_p88', 'ornament': 'mt32_p88',
            'pad': 'mt32_p74', 'arp': 'mt32_organ', 'bass': 'mt32_p71'},
    extra=['mt32_p88_low', 'mt32_p74_low'],
    assignments=[(1, 1, 'top', 'lead'),
                 (2, 2, 'top', 'doubler',
                  {'zone_below': {'note': 64, 'instrument': 'mt32_p88_low'}}),
                 (3, 2, 'bottom', 'ornament',
                  {'zone_below': {'note': 64, 'instrument': 'mt32_p88_low'}}),
                 (4, 3, 'top', 'pad',
                  {'zone_below': {'note': 64, 'instrument': 'mt32_p74_low'}}),
                 (5, 4, 'top', 'arp'), (6, 5, 'top', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 42, 52, 55, 57],
    note=('42.5 BPM (the 25 BPM set_tempo is overridden at t=0): timer 118, '
          'quarter 1416ms (+0.3%). Rhythm keys 52/55/57 are silent on the '
          'MT-32 kit -- dropped, faithful to hardware.')),
 'r070_hellcliff': dict(
    mid='room_070_hellcliff__soun_002', title='Hell Cliff', room='bloom',
    note_scale=1,
    chairs={'doubler': 'ph_horn', 'ornament': 'ph_horn', 'lead': 'ph_horn',
            'arp': 'mt32_marimba', 'bass': 'ph_bass'},
    extra=['mt32_marimba_low'],
    assignments=[(1, 1, 'top', 'doubler'), (2, 1, 'bottom', 'ornament'),
                 (3, 3, 'top', 'arp',
                  {'zone_below': {'note': 54, 'instrument': 'mt32_marimba_low'}}),
                 (4, 4, 'top', 'lead'), (5, 2, 'top', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[35, 36, 37, 62, 63, 75],
    note=('60 BPM -> note_scale 1, same timer 167. Both prog-91 channels take '
          'ph_horn (the chair it won in r028). Marimba engine spans 36-64: '
          'low zone below 54.')),
 'r077_ghdeck': dict(
    mid='room_077_gh-deck__soun_002', title='Ghost Ship Deck', room='bloom',
    chairs={'lead': 'mt32_p53', 'doubler': 'mt32_p97', 'ornament': 'mt32_p97',
            'bass': 'ph_bass'},
    assignments=[(1, 1, 'top', 'lead'), (2, 3, 'top', 'doubler'),
                 (3, 3, 'bottom', 'ornament'), (4, 5, 'top', 'bass'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 62, 63]),
 'r078_church': dict(
    mid='room_078_church__soun_001', title='Church', room='bloom',
    chairs={'lead': 'mt32_p13', 'doubler': 'mt32_p13', 'pad': 'mt32_p13',
            'arp': 'mt32_p13', 'ornament': 'mt32_p13', 'bass': 'mt32_p13'},
    extra=['mt32_p13_low'],
    assignments=[
        (1, 1, 'voice0', 'lead',
         {'zone_below': {'note': 58, 'instrument': 'mt32_p13_low'}}),
        (2, 1, 'voice1', 'doubler',
         {'zone_below': {'note': 58, 'instrument': 'mt32_p13_low'}}),
        (3, 1, 'voice2', 'pad',
         {'zone_below': {'note': 58, 'instrument': 'mt32_p13_low'}}),
        (4, 1, 'voice3', 'arp',
         {'zone_below': {'note': 58, 'instrument': 'mt32_p13_low'}}),
        (5, 1, 'voice4', 'ornament',
         {'zone_below': {'note': 58, 'instrument': 'mt32_p13_low'}}),
        (6, 1, 'bottom', 'bass',
         {'zone_below': {'note': 58, 'instrument': 'mt32_p13_low'}})],
    drum_notes=[], release_rates={'lead': 'E21', 'doubler': 'E21',
                                  'pad': 'E21', 'arp': 'E21',
                                  'ornament': 'E21', 'bass': 'E21'},
    vols={'lead': 0.8, 'doubler': 0.7, 'pad': 0.65, 'arp': 0.65,
          'ornament': 0.65, 'bass': 0.85},
    note=('Single pipe-organ channel, chords up to 8 deep -> 6 SPC voices via '
          'the new voiceN reductions (top, next-from-top x4, bottom), all on '
          'p13 with a low zone below MIDI 58. Vol priors cut so 6 stacked '
          'organ voices do not clip.')),
 'r083_dock4': dict(
    mid='room_083_cu-dock__soun_004', title='Phatt City Docks', room='bloom',
    chairs={'lead': 'mt32_p50', 'doubler': 'mt32_p92', 'pad': 'mt32_organ',
            'bass': 'ph_bass'},
    extra=['mt32_p90'],
    assignments=[(1, 1, 'top', 'lead'),
                 (2, 2, 'top', 'doubler',
                  {'merge_ch': 3, 'merge_instrument': 'mt32_p90',
                   'merge_mode': 'fill', 'merge_min_gap_quarters': 2,
                   'merge_vol_scale': 0.6}),
                 (3, 4, 'top', 'pad'), (4, 4, 'bottom', 'pad'),
                 (5, 5, 'top', 'bass'), (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 62, 63, 64, 66, 69],
    note='ch3 (p90, 27 notes) gap-fills into the doubler voice.'),
 'r083_dock5': dict(
    mid='room_083_cu-dock__soun_005', title='Phatt City Docks (var)',
    room='bloom',
    chairs={'lead': 'mt32_p92', 'doubler': 'mt32_p90', 'pad': 'mt32_organ',
            'bass': 'ph_bass'},
    assignments=[(1, 1, 'top', 'lead'), (2, 2, 'top', 'doubler'),
                 (3, 3, 'top', 'pad'), (4, 3, 'bottom', 'pad'),
                 (5, 4, 'top', 'bass'), (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 61, 62, 63, 64, 66, 69]),
 'r085_melee': dict(
    mid='room_085_melee__soun_001', title='Mêlée Town', room='bloom',
    chairs={'lead': 'mt32_p97', 'ornament': 'mt32_p41', 'arp': 'mt32_xylophone',
            'doubler': 'mt32_bottle', 'bass': 'mt32_p71', 'pad': 'mt32_marimba'},
    assignments=[(1, 1, 'top', 'lead'), (2, 2, 'top', 'ornament'),
                 (3, 3, 'top', 'arp'), (4, 4, 'top', 'doubler'),
                 (5, 5, 'top', 'bass'), (6, 6, 'top', 'pad'),
                 (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 41, 42, 43, 45, 47, 48, 50, 61, 62, 63, 64, 75]),
 'r096_part1': dict(
    mid='room_096_part1__soun_001', title='Part One: The Three Trials',
    room='bloom', loop=False,
    chairs={'lead': 'mt32_lead_flute', 'pad': 'mt32_organ',
            'arp': 'mt32_marimba', 'bass': 'ph_bass'},
    assignments=[(1, 1, 'top', 'lead'), (2, 3, 'top', 'pad'),
                 (3, 3, 'bottom', 'pad'), (4, 4, 'top', 'arp'),
                 (5, 5, 'top', 'bass'), (7, 9, 'drum_string', 'drums')],
    drum_notes=[36, 37, 62, 63], vibrato={'lead': '30,2,12'},
    note='Title-card sting: plays once, no loop.'),
}


def load_reports():
    info = {}
    for f in ['extract_jobs_report.json', 'extract_jobs2_report.json',
              'extract_jobs3_report.json']:
        p = REPO / 'build' / f
        if p.exists():
            for r in json.loads(p.read_text()):
                info[r['name']] = r
    return info


def octaves(lo, hi):
    return max(1, lo // 12 - 1), min(7, hi // 12 - 1)


def instrument_entry(name, reports, first_oct, last_oct):
    if name in LIB:
        e = dict(LIB[name])
    elif name in DRUM_LIB or name.startswith('mt32_drum_n'):
        e = dict(source=f'../../samples/instruments/{name}.wav', freq=65.406,
                 loop='none', envelope='gain F127')
        first_oct = last_oct = 2
    else:
        r = reports.get(name)
        if r is None:
            raise KeyError(f'no extract report for {name}')
        e = dict(source=f'../../samples/instruments/{name}.wav',
                 freq=round(float(r['freq']), 3), envelope=r['envelope'])
        if r['mode'] == 'oneshot':
            e['loop'] = 'none'
        else:
            e['loop'] = 'loop_with_filter'
            e['loop_setting'] = r['loop_setting']
    entry = {'name': name, 'source': e['source'], 'freq': e['freq'],
             'loop': e['loop'], 'evaluator': 'default',
             'ignore_gaussian_overflow': False,
             'first_octave': first_oct, 'last_octave': last_oct,
             'envelope': e['envelope']}
    if 'loop_setting' in e:
        entry['loop_setting'] = e['loop_setting']
    return entry


def build_song(song, spec, recon, reports):
    rec = recon[spec['mid']]
    out = SONGS_DIR / song
    out.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCAN / f"{spec['mid']}.mid", out / f'{song}.mid')

    # Channel range per midi_ch (recon keys are printed 1-based = midi_ch+1)
    ch_range = {int(k) - 1: (v['lo'], v['hi']) for k, v in rec['ch'].items()}

    # ---- octave needs per instrument (from the chairs that use it) ----
    needs = {}
    def need(inst, lo, hi):
        a, b = needs.get(inst, (99, -1))
        needs[inst] = (min(a, lo), max(b, hi))
    for a in spec['assignments']:
        spc, ch, red, role = a[:4]
        knobs = a[4] if len(a) > 4 else {}
        if red == 'drum_string':
            continue
        lo, hi = ch_range[ch]
        inst = spec['chairs'][role]
        zb = knobs.get('zone_below')
        if zb:
            need(zb['instrument'], lo, zb['note'] - 1)
            need(inst, zb['note'], hi)
        else:
            need(inst, lo, hi)
        mi = knobs.get('merge_instrument')
        if mi:
            mlo, mhi = ch_range[knobs['merge_ch']]
            need(mi, mlo, mhi)

    # ---- project file ----
    instruments = []
    for inst, (lo, hi) in needs.items():
        o1, o2 = octaves(lo, hi)
        instruments.append(instrument_entry(inst, reports, o1, o2))
    drums_used = sorted(set(spec.get('drum_notes', [])))
    drum_insts = []
    overrides = {}
    for n in drums_used:
        if str(n) in STD_OVERRIDES:
            ph = STD_OVERRIDES[str(n)]
            overrides[str(n)] = ph
            if ph is None:
                continue
        else:
            ph = {35: 'drum_kick', 36: 'drum_kick', 38: 'drum_snare',
                  41: 'drum_tom', 42: 'drum_hat', 43: 'drum_tom',
                  45: 'drum_tom', 47: 'drum_tom', 61: 'drum_bongo',
                  66: 'drum_timbale', 75: 'drum_claves'}.get(n)
            if ph is None:
                raise KeyError(f'{song}: unmapped drum note {n}')
        drum_insts.append(ph)
    drum_insts = sorted(set(drum_insts))
    drums_dict = {ph: STD_DRUMS[ph] for ph in drum_insts}
    for inst in drums_dict.values():
        if not any(i['name'] == inst for i in instruments):
            instruments.append(instrument_entry(inst, reports, 2, 2))

    project = {
        '_about': {'file_type': 'Terrific Audio Driver project file',
                   'version': '0.2.0-beta.2',
                   '_comment': f"{spec['title']} (MI1 soundtrack batch). "
                               f"{spec.get('note', '')}".strip()},
        'instruments': instruments,
        'samples': [], 'default_sfx_flags': {'one_channel': True,
                                             'interruptible': True},
        'high_priority_sound_effects': [], 'sound_effects': [],
        'low_priority_sound_effects': [], 'sound_effect_file': '',
        'songs': [{'name': song, 'source': f'{song}.mml'}],
    }
    (out / f'{song}.terrificaudio').write_text(
        json.dumps(project, indent=1) + '\n', encoding='utf-8')

    # ---- plan ----
    room = ROOMS[spec.get('room', 'bloom')]
    vols = spec.get('vols', {})
    assignments = []
    for a in spec['assignments']:
        spc, ch, red, role = a[:4]
        knobs = dict(a[4]) if len(a) > 4 else {}
        entry = {'spc': spc, 'midi_ch': ch, 'reduction': red, 'role': role,
                 'vol_scale': vols.get(role, VOL_PRIORS[role])}
        if role == 'bass':
            entry['echo'] = False
        entry.update(knobs)
        assignments.append(entry)
    plan = {
        '_about': f"{spec['title']} -- generated by gen_song_projects.py "
                  f"(MI1 soundtrack batch, hybrid doctrine). "
                  f"{spec.get('note', '')}".strip(),
        'dialect': 'terrific', 'title': spec['title'],
        'composer': 'M. Land / P. McConnell / C. Bajakian',
        'arranger': 'SMI pipeline',
        'tempo': spec.get('tempo', 30), 'timer': spec.get('timer', 167),
        'zenlen': 192, 'note_scale': spec.get('note_scale', 2),
        'grid': '32nd', 'grid_tolerance': 1, 'min_note_mml_ticks': 2,
        'release_through_rests': True,
        'release_rates': spec.get('release_rates', RELEASE_RATES),
        'drum_timing': 'tick', 'loop': spec.get('loop', True),
        'echo_length': 32, 'echo_fir': '64 63 0 0 0 0 0 0', **room,
        # The converter declares all six role instruments; unused roles point
        # at an instrument the song already loads (no extra sample RAM).
        'instruments': {**{r: spec['chairs'].get(r, next(iter(spec['chairs'].values())))
                           for r in ('lead', 'doubler', 'bass', 'pad', 'arp', 'ornament')},
                        **({'extra': spec['extra']} if spec.get('extra') else {}),
                        'drums': drums_dict},
        'drum_note_overrides': {k: v for k, v in overrides.items()},
        'assignments': assignments,
        'drum_overflow_spc': 8,
    }
    if spec.get('vibrato'):
        plan['vibrato'] = spec['vibrato']
    (out / f'{song}.plan.json').write_text(
        json.dumps(plan, indent=1) + '\n', encoding='utf-8')
    print(f"{song}: {len(instruments)} instruments, "
          f"{len(assignments)} voices, drums={drum_insts}")


def main():
    recon = json.loads((SCAN / 'recon.json').read_text())
    reports = load_reports()
    only = sys.argv[1:] or list(SPECS)
    for song in only:
        build_song(song, SPECS[song], recon, reports)
    return 0


if __name__ == '__main__':
    sys.exit(main())
