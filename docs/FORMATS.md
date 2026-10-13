# For Honor file formats (as read by `fh.py`)

Notes from reading the PC version. Everything here is implemented in `fh.py`; offsets are little-endian.

## `.forge` container (version 27)

Metadata is read in fixed-size *units*. Each unit is XXTEA-encrypted (delta `0x9e3779b9`, rounds `6 + 52/n`):

- 128-bit key, tweaked per unit: low 64 bits `-= offset`, high 64 bits `+= offset` (offset = file position of the unit).
- Trailing `len % 4` bytes are XORed with the bytes of the offset.
- The key is one of 96 candidates built from the CRC-32/MPEG-2 table `T`: for `i` in 0..31 and index patterns
  `(6,5,2,1)`, `(6,4,3,2)`, `(7,6,4,2)`, key = `T[i*8 + p]`. The right one decrypts the first 8 bytes to `scimitar`.

| Unit | Size | Content |
|---|---|---|
| 0 | 9 | magic `scimitar\0` |
| 9 | 0x411 | `u32 version (0x1b)`, `u64 offset of next block` |
| block | 0x2c | `u32 count, u32, u64, u32 x3, u32 total entries, u32 table count, u64 table offset` |
| table | 0x30 | `u32 count, u32, u64 entries offset, u64 next table (-1 = none), u32 base index, u32, u64, u64` |
| entry | 0x14 | `u64 offset, u64 id, u32 size` |

### Payloads

`[data][u32 raw size][u32 0xF3508EA0 | codec]`, optionally followed by one flag byte. Flag `1`: the first `min(n, 0x400)` bytes are XXTEA
with key `(id, id)` and tweak `n`. Codec 11 = Oodle Leviathan, 10 = stored.

## Object data

- `gamedatapc_*.forge`: object files. An 8-byte entry is a stub holding the 64-bit id of the real file in `gameobjectpc_*.forge`.
- Object file: `u16 count`, then records `[kind u8][id u64][0x26|0x27][type u32][name: u8 len + bytes][u16 len][body]`.
- External reference: `0x0f, file id u64, type u32, name`.
- Entry id `0x6d` is the dependency table: `u32 n`, then `file id u64, u32 c`, then `c x (sub id u64, u32 m, m x asset id u64)`.

| Type | Meaning |
|---|---|
| `0x415d9568` | mesh |
| `0x85c817c3` | material |
| `0x59ce4d13` | texture map |
| `0x95741049` | bone |

### Mesh record body (starts `53 ae ea f8`)

`+28` asset id, `+36` real asset size, `+52` mesh id, `+68` bone count, `+80` bone palette: 80-byte entries
`a1 e7 f0 9e, CRC32(bone name), 16 floats inverse bind (row-vector; bone world = inverse(transpose))`.
Later in the body: material ids 9 bytes apart, one per LOD0 submesh record, in record order.

Names ending `_sim` are cloth-simulation proxies; `_map` meshes are the rendered cloth.

### Bone record

`id u64, type, CRC32(name), kind u8 (2 = parent id u64 follows)`, then two `pos(4f) quat(4f)` blocks
(second = local to parent), misc, `u32 name length`, name.

## Mesh buffer (starts `13 00 00 00`)

`u32 0x13, u32, u32 stride, u32 sizes[5]` (vertices, cluster indices, flat indices, …), data at `0x54`.
Vertices are stored as `stride/4` separate streams of 4 bytes per vertex.

| Stride | Streams |
|---|---|
| `0x28` skinned | 0-2 float x,y,z; 3 normal (u8x4); 6 UV (half x2); 8 bone indices (u8x4); 9 weights (u8x4) |
| `0x18` static | 0 = x,y int16; 1 = z, w int16 with position = `xyz * w / 32768`; 2 normal; 5 UV (half x2) |

Index section 1 holds clusters of 64 triangles (192 `u16`). After the data: 32-byte records
`(4, 0, vertex count, first cluster, cluster count, index, 0, 0)`, one per LOD and submesh; LOD0 is the leading
records whose vertex counts add up to the vertex total.

Mesh and texture assets may be deltas: if the asset is `size + 9` bytes, it is `[delta][u64 base asset id][1]` and the
real data is `delta + base` byte-wise.

Some outfits store skinned vertices in a space other than the reference pose; multiply by
`reference bone world x inverse bind` of a palette bone to fix (see `bind_correction`).

## Textures

Texture object: after marker `3d 4b 0c c3`: `u32 size, u32 width, u32 height`, mip count at `+28`, format at `+41`
(2 = BC1, 4/5 = BC3, 6 = BC5). `<name>_CHRTM_0` is the base mip chain, `<name>_CHRTM_1` the top mip.
Colour-mask maps (`…_ColorMaskMap`) mark three tintable regions in R, G and B.
