builddir := build
sourcedir := src
datadir := data
distdir := distribution
linkdir := $(builddir)/lnk

romext := sfc
romfile := $(builddir)/SuperMonkeyIsland.$(romext)

linker := ./tools/wla-dx-9.5-svn/wlalink/wlalink
linkflags := -dsr
linkobjectfile := $(linkdir)/linkobjs.lst

assembler := ./tools/wla-dx-9.5-svn/wla-65816
assemblerflags := -o

gfxconverter :=./tools/gracon.py

# Allow switching to superfamiconv for faster builds
# Set USE_SUPERFAMICONV=1 environment variable to enable
ifdef USE_SUPERFAMICONV
gfxconverter := ./tools/gfx_converter.py --tool superfamiconv --pad-to-32x32
endif

verify := -verify on
gfx_font_flags := $(verify) -optimize off -palettes 1 -bpp 2 -mode bg
gfx_font4bpp_flags := $(verify) -optimize off -palettes 1 -bpp 4 -mode bg

gfx_bg_flags := $(verify) -optimize on -tilethreshold 15 -palettes 3 -bpp 4 -mode bg
gfx_directcolor_flags := $(verify) -optimize on -directcolor on -tilethreshold 10 -palettes 1 -bpp 8 -mode bg
gfx_sprite_flags := $(verify) -optimize on -tilethreshold 10 -palettes 2 -bpp 4 -mode sprite

animation_converter := ./tools/animationWriter.py

ifdef USE_SUPERFAMICONV
animation_converter := ./tools/animationWriter_sfc.py
endif

# TAD audio compiler
tadcompiler := ./tools/tad/tad-compiler.exe
tadproject := audio/smi.terrificaudio
tadaudiobin := $(builddir)/audio/tad-audio-data.bin

RD := $(RM) -r
MD := mkdir -p

empty :=
space := $(empty) $(empty)

asmsource := 65816
asmobj := o
asmheader := h

image := png
tile := tiles
spriteanimation := animation



sourcefiles := $(shell find $(sourcedir)/ -type f -name '*.$(asmsource)')
objects := $(addprefix $(builddir)/,$(patsubst %.$(asmsource), %.$(asmobj), $(sourcefiles)))
configfiles := $(shell find $(sourcedir)/ -type f -name '*.inc' -o -name '*.opcodes' -o -name '*.registers')
scriptfiles := $(shell find $(sourcedir)/ -type f -name '*.inc' -o -name '*.opcodes' -o -name '*.script') $(shell find $(datadir)/ -type f -name '*.inc' -o -name '*.opcodes' -o -name '*.script')
interfacefiles := $(shell find $(sourcedir)/ -type f -name '*.interface')
inheritancefiles := $(shell find $(sourcedir)/ -type f -name '*.inheritance')


graphics := $(shell find $(datadir)/ -type f -name '*.gfx_normal.$(image)')
graphics += $(shell find $(datadir)/ -type f -name '*.gfx_font*.$(image)')
converted_graphics := $(sort $(addprefix $(builddir)/,$(patsubst %.$(image), %.$(tile), $(graphics))))

bg_animations := $(shell find $(datadir)/ -type d -name '*.gfx_bg')
bg_animations += $(shell find $(datadir)/ -type d -name '*.gfx_directcolor')
converted_bg_animations := $(sort $(addprefix $(builddir)/,$(addsuffix .$(spriteanimation), $(bg_animations))))
sprite_animations := $(shell find $(datadir)/ -type d -name '*.gfx_sprite')
converted_sprite_animations := $(sort $(addprefix $(builddir)/,$(addsuffix .$(spriteanimation), $(sprite_animations))))

# ROM data packer (room tiles + script bytecodes for upper ROM banks)
romdatabin := $(builddir)/rom_data.bin
romdatainc := $(builddir)/rom_data.inc
romdatapacker := python3 ./tools/rom_pack_data.py

objroomtable := $(builddir)/obj_room_table.inc
scummsoundmap := $(builddir)/audio/scumm_sound_map.inc
sparklechr := $(datadir)/logo_sparkle.chr
sparklepal := $(datadir)/logo_sparkle.pal
datafiles := $(converted_graphics) $(converted_sprite_animations) $(converted_bg_animations) $(tadaudiobin) $(romdatabin) $(objroomtable) $(scummsoundmap) $(sparklechr) $(sparklepal)
builddirs := $(sort $(dir $(objects) $(datafiles)) $(linkdir))

#link 65816 objects, then append ROM data
all: $(linkobjectfile)
	$(linker) $(linkflags) $(linkobjectfile) $(romfile)
	@echo "Appending ROM data (room tiles + scripts) to upper banks..."
	cat $(romdatabin) >> $(romfile)
	$(MD) $(distdir)
	cp $(romfile) $(distdir)/SuperMonkeyIsland.sfc

#create necessary directory substructure in build directory
$(builddirs):
	$(MD) $@

#create 65816 object linkfile
$(linkobjectfile): $(objects)
	$(shell echo "[objects]" > $(linkobjectfile))
	$(foreach obj, $(objects), $(shell echo "$(obj)" >> $(linkobjectfile)))

#compile 65816 assembler sourcefiles
#Static Pattern Rules $(targets): target-pattern: target-prereqs
$(objects): $(builddir)/%.$(asmobj): %.$(asmsource) %.$(asmheader) $(configfiles) $(scriptfiles) $(interfacefiles) $(inheritancefiles) $(datafiles) | $(builddirs)
	$(assembler) $(assemblerflags) $< $@


#generate LucasArts logo sparkle OBJ-layer CHR + sub-palette
$(sparklechr) $(sparklepal): data/scumm_extracted/rooms/room_010_logo/palette.bin tools/convert_sparkle_chr.py
	python3 ./tools/convert_sparkle_chr.py

#generate object-to-room lookup table (used by loadRoomWithEgo room=0)
$(builddir)/obj_room_table.inc: $(wildcard data/scumm_extracted/rooms/room_*/metadata.json) | $(builddirs)
	python3 ./tools/gen_obj_room_table.py

#generate SCUMM sound ID -> TAD dispatch map (reads smi.terrificaudio sfx list)
$(scummsoundmap): $(wildcard data/scumm_extracted/sounds/soun_*.bin) $(tadproject) tools/scumm/gen_audio_map.py | $(builddirs)
	python3 ./tools/scumm/gen_audio_map.py

#apply SCUMM script patches (text replacements, position overrides) before packing
patchedscriptsstamp := $(builddir)/scumm_patched_scripts/.stamp
$(patchedscriptsstamp): tools/patch_scripts.py tools/scumm_patches.json $(wildcard data/scumm_extracted/scripts/scrp_*.bin) | $(builddirs)
	python3 ./tools/patch_scripts.py --src-dir data/scumm_extracted --out-dir $(builddir)/scumm_patched_scripts --patches tools/scumm_patches.json
	@touch $@

#pack room + script data into ROM data blob (produces both .bin and .inc)
$(romdatabin): data/snes_converted/rooms/manifest.json $(wildcard data/snes_converted/rooms/room_*) $(wildcard data/scumm_extracted/scripts/scrp_*.bin) $(wildcard data/scumm_extracted/rooms/room_*/scripts/*.bin) $(patchedscriptsstamp) | $(builddirs)
	$(romdatapacker) --rooms-dir data/snes_converted/rooms --output-bin $(romdatabin) --output-inc $(romdatainc) --scripts-override-dir $(builddir)/scumm_patched_scripts

# rom_data.inc is produced as a side-effect of rom_data.bin
$(romdatainc): $(romdatabin)

#compile TAD audio data (run tad-compiler to produce binary blob)
$(tadaudiobin): $(tadproject) $(wildcard audio/songs/*.mml) $(wildcard audio/sfx/*.txt) $(wildcard audio/samples/*.wav) | $(builddirs)
	$(tadcompiler) 64tass-export --hirom --output-asm $(builddir)/audio/tad-audio-data.asm --output-bin $(tadaudiobin) --output-inc $(builddir)/audio/tad-enums.inc --section "AudioData0" $(tadproject)


#convert graphic files. conversion flags are determined by special string inside filename ".gfx_%." (e.g. fixed8x8.gfx_font.png) and fetched from corresponding variable name ($(gfx_font_flags) in this case)
$(converted_graphics): $(builddir)/%.$(tile): %.$(image) | $(builddirs)
	$(gfxconverter) $($(filter gfx_%, $(subst .,$(space), $@))_flags) -infile $< -outfilebase $(patsubst %.$(tile), %, $@)


#convert sprite animation folders to sprite animation file
$(converted_sprite_animations): $(builddir)/%.$(spriteanimation): % | $(builddirs)
	$(animation_converter) -mode sprite -infolder $< -outfile $@

#convert bg animation folders to sprite animation file
$(converted_bg_animations): $(builddir)/%.$(spriteanimation): % | $(builddirs)
	$(animation_converter) $($(filter gfx_%, $(subst .,$(space), $@))_flags) -infolder $< -outfile $@



clean:
	$(RD) $(builddir)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "Build artifacts cleaned"

clean-all: clean
	@echo "Cleaning WLA-DX build artifacts..."
	cd tools/wla-dx-9.5-svn && make clean 2>/dev/null || true
	cd tools/wla-dx-9.5-svn/wlalink && make clean 2>/dev/null || true
	@echo "All artifacts cleaned (including WLA-DX)"
