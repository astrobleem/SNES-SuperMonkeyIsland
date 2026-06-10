/* mt32render — feed a text event list through libmt32emu, write a WAV.
 *
 * Event list format, one event per line:
 *   <time_ms> <hex byte> [hex byte ...]
 * 3-byte channel messages are packed as short messages; lines starting
 * with F0 are played as sysex. Lines starting with '#' are comments.
 *
 * Usage: mt32render <control.rom> <pcm.rom> <events.txt> <out.wav> [tail_ms]
 *
 * Output: stereo int16 WAV at 32000 Hz (the MT-32's native DAC rate, which
 * is also the SNES DSP BRR rate — samples extracted from these renders are
 * used 1:1, no resampling).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mt32emu.h>

#define SAMPLE_RATE 32000

static void wav_header(FILE *f, unsigned data_bytes) {
    unsigned chunk = 36 + data_bytes, byterate = SAMPLE_RATE * 4;
    unsigned short align = 4, bits = 16, fmt = 1, ch = 2;
    fwrite("RIFF", 1, 4, f); fwrite(&chunk, 4, 1, f); fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f); chunk = 16; fwrite(&chunk, 4, 1, f);
    fwrite(&fmt, 2, 1, f); fwrite(&ch, 2, 1, f);
    chunk = SAMPLE_RATE; fwrite(&chunk, 4, 1, f); fwrite(&byterate, 4, 1, f);
    fwrite(&align, 2, 1, f); fwrite(&bits, 2, 1, f);
    fwrite("data", 1, 4, f); fwrite(&data_bytes, 4, 1, f);
}

int main(int argc, char **argv) {
    if (argc < 5) { fprintf(stderr, "usage: %s control.rom pcm.rom events.txt out.wav [tail_ms]\n", argv[0]); return 2; }
    unsigned tail_ms = argc > 5 ? (unsigned)atoi(argv[5]) : 2000;

    mt32emu_context ctx = mt32emu_create_context((mt32emu_report_handler_i){ NULL }, NULL);
    if (mt32emu_add_rom_file(ctx, argv[1]) < 0) { fprintf(stderr, "bad control rom\n"); return 2; }
    if (mt32emu_add_rom_file(ctx, argv[2]) < 0) { fprintf(stderr, "bad pcm rom\n"); return 2; }
    if (mt32emu_open_synth(ctx) != MT32EMU_RC_OK) { fprintf(stderr, "open_synth failed\n"); return 2; }
    /* default partial reserve + reverb mirror real-hardware boot state */

    FILE *ev = fopen(argv[3], "r");
    if (!ev) { perror(argv[3]); return 2; }
    FILE *out = fopen(argv[4], "wb");
    if (!out) { perror(argv[4]); return 2; }
    wav_header(out, 0); /* patched at the end */

    char line[8192];
    double last_ms = 0;
    unsigned total_frames = 0;
    static mt32emu_bit16s buf[SAMPLE_RATE * 4]; /* 1s stereo */

    while (fgets(line, sizeof line, ev)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        char *p = line;
        double t_ms = strtod(p, &p);
        unsigned char bytes[4096];
        int n = 0;
        while (n < 4096) {
            long b = strtol(p, &p, 16);
            if (p == line || (b == 0 && !(*p))) { /* end heuristics handled below */ }
            bytes[n++] = (unsigned char)b;
            while (*p == ' ' || *p == '\t') p++;
            if (*p == '\n' || *p == '\r' || *p == 0) break;
        }
        if (n == 0) continue;

        /* render up to this event's time */
        if (t_ms > last_ms) {
            unsigned frames = (unsigned)((t_ms - last_ms) * SAMPLE_RATE / 1000.0 + 0.5);
            while (frames) {
                unsigned chunk = frames > SAMPLE_RATE ? SAMPLE_RATE : frames;
                mt32emu_render_bit16s(ctx, buf, chunk);
                fwrite(buf, 4, chunk, out);
                total_frames += chunk;
                frames -= chunk;
            }
            last_ms = t_ms;
        }
        if (bytes[0] == 0xF0) {
            mt32emu_play_sysex(ctx, bytes, (mt32emu_bit32u)n);
        } else {
            mt32emu_bit32u msg = bytes[0];
            if (n > 1) msg |= (mt32emu_bit32u)bytes[1] << 8;
            if (n > 2) msg |= (mt32emu_bit32u)bytes[2] << 16;
            mt32emu_play_msg(ctx, msg);
        }
    }
    fclose(ev);

    unsigned frames = (unsigned)((double)tail_ms * SAMPLE_RATE / 1000.0);
    while (frames) {
        unsigned chunk = frames > SAMPLE_RATE ? SAMPLE_RATE : frames;
        mt32emu_render_bit16s(ctx, buf, chunk);
        fwrite(buf, 4, chunk, out);
        total_frames += chunk;
        frames -= chunk;
    }

    /* patch RIFF sizes */
    unsigned data_bytes = total_frames * 4;
    fseek(out, 0, SEEK_SET);
    wav_header(out, data_bytes);
    fclose(out);
    mt32emu_close_synth(ctx);
    mt32emu_free_context(ctx);
    fprintf(stderr, "rendered %u frames (%.2fs) to %s\n", total_frames, total_frames / (double)SAMPLE_RATE, argv[4]);
    return 0;
}
