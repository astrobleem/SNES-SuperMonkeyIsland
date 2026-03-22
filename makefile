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

datafiles := $(converted_graphics) $(converted_sprite_animations) $(converted_bg_animations) $(tadaudiobin)
builddirs := $(sort $(dir $(objects) $(datafiles)) $(linkdir))

#link 65816 objects
all: $(linkobjectfile)
	$(linker) $(linkflags) $(linkobjectfile) $(romfile)
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
