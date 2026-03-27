;BW-RAM memory map definitions
;SA-1 BW-RAM: 128KB at $40:0000-$41:FFFF
;Addresses are 24-bit for long addressing (sta.l $40xxxx,x)

;=== PERSISTENT REGION ($40:0000-$40:7FFF, 32KB) ===
;Battery-backed. Survives power cycles. Written only during save ops.

.define BWRAM_SAVE_HEADER		$400000
	.define BWRAM_SAVE_MAGIC	$400000		;4 bytes: "SMI1" ($534D4931)
	.define BWRAM_SAVE_VERSION	$400004		;2 bytes: format version
	.define BWRAM_SAVE_SLOTS	$400006		;2 bytes: slot count (3)
	.define BWRAM_SAVE_SLOTSIZE	$400008		;2 bytes: slot size (8192)
	.define BWRAM_SAVE_ACTIVE	$40000A		;2 bytes: last used slot (0-2)

.define BWRAM_SAVE_SLOT0		$400020		;~8KB
.define BWRAM_SAVE_SLOT1		$402000		;8KB
.define BWRAM_SAVE_SLOT2		$404000		;8KB
.define BWRAM_SAVE_SETTINGS		$406000		;8KB: player prefs, controls

;=== VOLATILE REGION ($40:8000-$41:FFFF, 96KB) ===
;Zeroed on every boot. Used freely as scratch/work memory.

.define BWRAM_VOLATILE_BASE		$408000

.define BWRAM_CGRAM_SHADOW		$408000		;256 bytes: 128 colors x 2B BGR555
.define BWRAM_CGRAM_SHADOW_SIZE	256

.define BWRAM_SA1_COMPOSITE		$408100		;2KB: SA-1 sprite compositing
.define BWRAM_SA1_COMPOSITE_SIZE	2048

.define BWRAM_PALETTE_SCRATCH	$408900		;1.75KB: darkenPalette temps

.define BWRAM_SAVE_STAGING		$409000		;12KB: serialize VM state before save
.define BWRAM_FUTURE_BUFFERS	$40C000		;16KB: cutscene data, strings, etc.

;=== BANK $41 — Future expansion (64KB) ===
.define BWRAM_EXPANSION_BASE	$410000

;=== Save header magic ===
.define BWRAM_MAGIC_LO			$4D53		;"SM" (little-endian of "SMI1")
.define BWRAM_MAGIC_HI			$3149		;"I1"
.define BWRAM_FORMAT_VERSION	1

;=== Volatile clear range ===
.define BWRAM_VOLATILE_CLEAR_START	$8000	;offset within bank $40
.define BWRAM_VOLATILE_CLEAR_SIZE	$8000	;32KB ($40:8000-$40:FFFF)
