"""Simulate the GSU scaler to verify expected output vs actual."""

grid_hex = "00000003000F001F001D0431063844330003030F0F1F1F3F1D3F317F387F337FC0C0000000C000E000E008F008E008F0000000E0F0C0E0F0F0E8F0F8F0E8F0FC0000000000000000000000000000000000000000000000000000000000000000040984000C703F7F3E7F1E3F143F183F09FF00FF400F4C034C0308070007200308F008D458041CFC3CD418FC3AD61EFEF0FCD4F800F808F000D008F000D00CF000000000000000000000000000000000000000000000000000000000000000000C1F000F0C0F1C1F1C1F191F1F1F1F1F1003080308071807100F100F19061F007CFEFCFCFCFCF4FCF4FCFCFCFCFCFCFC04F834C874887488E418E418E418FC0000000000000000000000000000000000000000000000000000000000000000001F1F0E000C000C010C010C010C011F1F1F00000F000F010F010F010F010F1F00F8C03004308430841004180408041D1EC03C04BC84BC84BC041C041C040C1C000000000000000000000000000000000000000000000000000000000000000000171B3F3F3F3F3F3F00000000000000001300300F201F3F0000000000000000009F9F9F9FC0C0C0C000000000000000009C039F004080C0000000000000000000C0C0E0E000000000000000000000000000C0E000000000000000000000000000"
grid = bytes.fromhex(grid_hex)

src_w, src_h, dst_w, dst_h = 19, 36, 16, 30
tiles_per_row = 3
x_step = 0x0130
y_step = 0x0133
lut = [0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01]

# Simulate scaler output
output = bytearray(4096)  # $0C00-$1BFF
non_transparent = 0
total_pixels = 0

y_accum = 0
for out_y in range(dst_h):
    src_y = (y_accum >> 8) & 0xFF
    y_in_tile = src_y & 7
    y_byte_off = y_in_tile * 2
    tile_row = src_y >> 3

    x_accum = 0
    for out_x in range(dst_w):
        src_x = (x_accum >> 8) & 0xFF
        total_pixels += 1

        # Extract source pixel
        x_in_tile = src_x & 7
        bitmask = lut[x_in_tile]
        tile_col = src_x >> 3

        tile_addr = (tile_row * tiles_per_row + tile_col) * 32 + y_byte_off

        color = 0
        if tile_addr < len(grid):
            bp0 = grid[tile_addr]
            bp1 = grid[tile_addr + 1]
            bp2 = grid[tile_addr + 16]
            bp3 = grid[tile_addr + 17]

            if bp0 & bitmask: color |= 1
            if bp1 & bitmask: color |= 2
            if bp2 & bitmask: color |= 4
            if bp3 & bitmask: color |= 8

        if color != 0:
            non_transparent += 1
            # Compute output address
            out_tile_row = out_y >> 3
            out_tile_col = out_x >> 3
            out_row_in_tile = out_y & 7
            out_base = out_tile_row * 512 + out_tile_col * 32 + out_row_in_tile * 2
            out_bitmask = lut[out_x & 7]

            for bp in range(4):
                if color & (1 << bp):
                    byte_off = ((bp >> 1) << 4) + (bp & 1)
                    output[out_base + byte_off] |= out_bitmask

        x_accum += x_step
    y_accum += y_step

out_nz = sum(1 for b in output if b != 0)
print(f"Total pixels: {total_pixels}")
print(f"Non-transparent: {non_transparent}")
print(f"Output non-zero bytes: {out_nz}")
print(f"First output tile (32 bytes): {output[:32].hex()}")
print(f"Second output tile (32 bytes): {output[32:64].hex()}")
