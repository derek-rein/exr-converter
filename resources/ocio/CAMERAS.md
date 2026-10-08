# Extra camera transforms

The file `aces-studio-v4.ocio` in this directory starts from the official
[ASWF ACES Studio Config v4.0.0](https://github.com/AcademySoftwareFoundation/OpenColorIO-Config-ACES)
(ACES 2.0, OCIO 2.5). That base is BSD-3-Clause; see
[ACES-STUDIO-LICENSE.txt](./ACES-STUDIO-LICENSE.txt). Every colorspace name
from that file is unchanged.

EXR Converter adds the input transforms below. They are **specification**
transforms: the manufacturer's published curve, then the published primaries
into ACES2065-1. They are not Academy spectral camera IDTs.

Rec.2020 and Rec.709 matrices are the inverses of this config's existing
`Linear Rec.2020` and `Linear Rec.709 (sRGB)` transforms (Bradford adaptation,
same as the rest of the utility spaces). After the curve, N-Log, F-Log,
F-Log2, and L-Log Rec.2020 match `Linear Rec.2020`. Cinema Gamut uses the
existing `Linear CinemaGamut D55` matrix. S-Gamut uses the same matrix as
`Linear S-Gamut3`: Sony publishes the same chromaticities for S-Gamut and
S-Gamut3, and that stock matrix is the CAT02 primaries conversion.

Neutral camera grey (equal RGB) maps to equal ACES2065-1, so 18% reflectance
lands on linear 0.18.

Code values are full-range floats: 0 is code 0 and 1 is code 1023 (or
4095, and so on). Where a maker's formula is legal-range inside that code
word (S-Log, S-Log2, Canon Log), the legal offset is part of the curve, same
convention as the stock S-Log3 spaces treating full-range code values.

## Added colorspaces

| Colorspace | Gamut | Source | License / redistribution |
| --- | --- | --- | --- |
| `N-Log N-Gamut` | Rec.2020 (N-Gamut) | Nikon, *N-Log Specification Document* v1.0.0, 1 September 2018. Decode: `code < 452` → `(code/650)^3 - 0.0075`, else `exp((code-619)/150)`, code = value × 1023. | Equations only. Nikon's cube LUTs (download center sw/258, older Z 6/Z 7 cubes at sw/140) are **not** in the repo; those downloads do not grant a clear right to redistribute the files. The 1D LUT `luts/nlog_to_lin.spi1d` is generated from the equation by `scripts/build_nlog_spi1d.py`. |
| `F-Log F-Gamut` | Rec.2020 (F-Gamut) | Fujifilm, *F-Log Data Sheet* Ver.1.0 / Ver.1.1. `a=0.555556`, `b=0.009468`, `c=0.344676`, `d=0.790453`, `e=8.735631`, `f=0.092864`, `cut1=0.00089`. 18% grey is code 470/1023. | Equations only. Fujifilm viewing LUTs are not bundled. |
| `F-Log2 F-Gamut` | Rec.2020 | Fujifilm, *F-Log2 Data Sheet* Ver.1.0. `a=5.555556`, `b=0.064829`, `c=0.245281`, `d=0.384316`, `e=8.799461`, `f=0.092864`, `cut1=0.000889`. 18% grey is code 400/1023. | Equations only. |
| `L-Log Rec.2020` | Rec.2020 | Leica Camera AG, *L-Log Reference Manual* (current SL2 / SL2-S / SL3 / Q3 and later). `out = 0.27·log10(1.3·in + 0.0115) + 0.6` above reflectance 0.006, else `8·in + 0.09`. | Equations only. Leica's downloadable viewing LUTs are not bundled. |
| `L-Log Rec.709` | Rec.709 | Same curve. The manual assigns Rec.709 to the SL (Typ 601) only; later bodies are Rec.2020. | Same as above. |
| `S-Log S-Gamut` | S-Gamut | Sony, *S-Log White Paper*. `y = 0.432699·log10(t + 0.037584) + 0.616596 + 0.03` with `t` the 0–10 sensor exposure (reflection / 0.9), then legal-range 10-bit scaling. 18% grey is code 394/1023. | Published formula and chromaticities. |
| `S-Log2 S-Gamut` | S-Gamut | Sony, *S-Log2 Technical Paper*. S-Log of `(155/219)·sensor`, same legal-range scaling. 18% grey is code 347/1023. | Published formula and chromaticities. |
| `CanonLog CinemaGamut D55` | Cinema Gamut (daylight) | Canon Log as in Larry Thorpe, *Canon-Log Transfer Characteristic* (2012) and Canon's later Input Transform packages. The 2012 IRE formula and the later legal-range constants are the same curve: 0% → code 128/1023, 18% → 351/1023. OCIO has no Canon Log 1 builtin; this config uses that curve. Cinema Gamut uses the stock matrix. | Curve constants match the public Canon papers. OCIO Canon Log 2 / Log 3 builtins are BSD-3-Clause (OpenColorIO). |
| `CanonLog Rec.2020` | Rec.2020 | Same Canon Log curve. | Same. |
| `CanonLog Rec.709` | Rec.709 | Same Canon Log curve. | Same. |
| `CanonLog2 Rec.2020` | Rec.2020 | OCIO builtin `CURVE - CANON_CLOG2_to_LINEAR` (Canon Input Transform v1.2 constants) plus this config's Rec.2020 matrix. Stock `CanonLog2 CinemaGamut D55` is unchanged. | OCIO builtin, BSD-3-Clause. |
| `CanonLog2 Rec.709` | Rec.709 | Same curve, Rec.709 matrix. | OCIO builtin, BSD-3-Clause. |
| `CanonLog3 Rec.2020` | Rec.2020 | OCIO builtin `CURVE - CANON_CLOG3_to_LINEAR` plus the Rec.2020 matrix. Stock `CanonLog3 CinemaGamut D55` is unchanged. | OCIO builtin, BSD-3-Clause. |
| `CanonLog3 Rec.709` | Rec.709 | Same curve, Rec.709 matrix. | OCIO builtin, BSD-3-Clause. |

Curve-only named transforms (no gamut matrix): `N-Log - Curve`, `F-Log - Curve`,
`F-Log2 - Curve`, `L-Log - Curve`, `S-Log - Curve`, `S-Log2 - Curve`,
`Canon Log - Curve`.

## Left out on purpose

| Candidate | Why it is not in the config |
| --- | --- |
| Nikon `.cube` LUTs | No clear redistribution grant. The spec equation is shipped instead. |
| Fujifilm F-Log2C / F-Gamut C | F-Log2C uses the F-Log2 curve, but the F-Log2 data sheet does not publish F-Gamut C primaries. `F-Log2 F-Gamut` is Rec.2020 only. |
| Blackmagic Film Gen 4 | No public curve equation. Gen 5 is already in the stock config. |
| DJI D-Log M / D-Log2 | DJI's published white paper is D-Log / D-Gamut, already in the stock config. D-Log M constants are not in that paper. |
| GoPro Protune / GP-Log | The ACES 1.0.3 community Protune matrix is marked experimental. GoPro has not published Protune Native or GP-Log primaries as a spec we can implement exactly. |
| Samsung Log, Z CAM Z-Log2, Kinefinity KineLog | No stable official curve-and-primaries pair with a license we could ship. |
