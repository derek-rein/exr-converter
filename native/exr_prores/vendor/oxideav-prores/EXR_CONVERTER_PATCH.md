# EXR Converter patch to oxideav-prores 0.1.1

This directory is the published `oxideav-prores` 0.1.1 crate from crates.io
(MIT, by Mark Karpeles, upstream https://github.com/OxideAV/oxideav-prores)
with one fix. It is not a fork for publishing. `native/exr_prores/Cargo.toml`
points at it through `[patch.crates-io]`, so `Cargo.lock` records it as a path
dependency instead of the registry checksum.

## Fix: 4:4:4 chroma block order

Inside each 16x16 macroblock a 4:4:4 picture codes four 8x8 blocks per chroma
component. FFmpeg (`proresdec`, `prores_ks`) uses column order for them:
top-left, bottom-left, top-right, bottom-right. Upstream 0.1.1 reused the luma
raster order (top-left, top-right, bottom-left, bottom-right) in both
`encoder.rs` and `decoder.rs`. Its own round trip therefore passed, while every
standard decoder swapped the top-right and bottom-left chroma blocks of every
macroblock. On real footage that showed up as blocky colour errors at edges
(up to 0.18 in ACEScct code values on a 1920x1080 render). ProRes 4444 and
4444 XQ were both affected; 4:2:2 profiles were not.

Changed files:

- `src/slice.rs`: new `CHROMA_444_OFFSETS = [(0, 0), (0, 1), (1, 0), (1, 1)]`
  (`(bx, by)` in 8-pixel units) plus a unit test.
- `src/encoder.rs`, `src/decoder.rs`: 4:4:4 chroma uses `CHROMA_444_OFFSETS`
  instead of the luma order.

EXR Converter checks the result against FFmpeg in
`tests/test_oxideav_prores.py` (`TestOxideavFfmpegChroma`).

## Updating

When upstream ships a release with the same fix, delete this directory and the
`[patch.crates-io]` entry, then bump `oxideav-prores` in
`native/exr_prores/Cargo.toml`. Until then, a new upstream version has to be
re-vendored with this change applied.
