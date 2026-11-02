---
title: Variables
weight: 17
description: Every <token> variable for slate, burn-in, watermark and transcode output paths, plus environment variables
---

Text fields that build names or overlays take **`<token>` variables**. Type
them, or right-click the field and pick one from the **Insert Variable** menu.
One set of rules covers every field:

- A token is a name in angle brackets: `<shot>`, `<reel>`.
- Case does not matter, and `_` and `-` are ignored, so `<reel_name>`,
  `<ReelName>` and `<reel-name>` are the same token.
- Some tokens have other spellings (aliases), listed below. They give the same
  value.
- `<` and `>` can't appear in Windows file names and are rarely used on macOS
  or Linux, so a token is never confused with real text.

There are two sets of tokens: [output path tokens](#output-path-tokens) for
Transcode names and folders, and [overlay tokens](#overlay-tokens) for the
slate, burn-in and visible watermark. Output paths also take
[environment variables](#environment-variables).

## Output path tokens

Used by the **Transcode** tab's **Name** and **Folder** fields and by
`transcode --name` / `--output-dir` ([CLI](./cli.md)).

### Source

Read from the clip. The clip is only opened for these when the pattern uses one.

| Token | Aliases | Value |
|-------|---------|-------|
| `<name>` | `<filename>`, `<file>`, `<clip>`, `<clipname>`, `<input>` | Source file name without the extension |
| `<reel>` | `<reelname>`, `<tape>`, `<tapename>` | Reel name from the clip's metadata (see [reel](#reel)) |
| `<srcresolution>` | `<sourceresolution>`, `<srcres>`, `<sourceres>` | Source size, `3840x2160` |
| `<srcwidth>` | `<sourcewidth>`, `<srcresx>`, `<srcresolutionx>`, `<sourceresolutionx>` | Source width, `3840` |
| `<srcheight>` | `<sourceheight>`, `<srcresy>`, `<srcresolutiony>`, `<sourceresolutiony>` | Source height, `2160` |
| `<srcfps>` | `<sourcefps>` | Source frame rate, `23.976` or `25` |
| `<timecode>` | `<tc>` | Start timecode, with `:` turned into `_`: `14_57_41_00` |
| `<parent>` | `<folder>` | Name of the folder the clip is in |

### Output

What the transcode writes.

| Token | Aliases | Value |
|-------|---------|-------|
| `<codec>` | | Output codec: `ProRes422Proxy`, `ProRes422LT`, `ProRes422`, `ProRes422HQ`, `ProRes4444`, `ProRes4444XQ`, `DNxHR_LB`, `DNxHR_SQ`, `DNxHR_HQ`, `DNxHR_HQX`, `DNxHR_444`, `H264`, `HEVC`, `HEVC10`, `HEVC12`, `AV1` |
| `<resolution>` | `<res>`, `<outresolution>` | Output size after scaling, `1920x1080` |
| `<width>` | `<resx>`, `<resolutionx>` | Output width |
| `<height>` | `<resy>`, `<resolutiony>` | Output height |
| `<fps>` | | Output frame rate (after any conform) |
| `<date>` | | Today's date, `YYYY-MM-DD` |

### How paths are built

- A `/` (or `\`) in **Name** makes sub-folders, next to each clip or inside
  the custom folder. `..` is not allowed.
- A token's value can't add folders: `/`, `:` and other characters that
  aren't allowed in file names become `_` inside a value.
- A token with no value (a clip with no reel) drops its folder level, and any
  `_`, `-`, `.` or space left at the ends of a name is trimmed.
  `<reel>/<reel>_<name>` on a clip with no reel gives just `<name>`.
- An unknown token stops that clip with an error in the log that lists the
  tokens you can use.
- The extension comes from the container and is added for you.

Example: the name `<reel>/<name>/<srcresolution>/<name>_<codec>` turns
`A_0001C001_261022_145741_a12S6.mov` into
`A_0001/A_0001C001_261022_145741_a12S6/3840x2160/A_0001C001_261022_145741_a12S6_ProRes422HQ.mov`.

### Reel

`<reel>` is read from the clip's metadata first: container and stream tags
for QuickTime / MXF / MP4 (`reel_name`, `ReelName`,
`com.apple.quicktime.reel_name`, `reel_id`, `tape_name`, `tape`, `reel_no`),
and the SDK metadata for R3D, BRAW and camera RAW plugins. When there is none,
it is the reel part of a camera clip name:

| Clip name | `<reel>` |
|-----------|----------|
| `A_0001C001_261022_145741_a12S6` (ARRI) | `A_0001` |
| `A001C003_220101_R1AB` (ARRI, Sony) | `A001` |
| `A001_C003_0101XY` (RED) | `A001` |
| `interview_final` | empty |

## Environment variables

Output paths (Transcode **Name** and **Folder**, `--name`, `--output-dir`)
also expand environment variables, **Unix style on every platform**, Windows
included. They are filled in before the `<>` tokens: `$` for values from the
pipeline, `<>` for values from the clip.

| Form | Value |
|------|-------|
| `$VAR` | The value of `VAR` |
| `${VAR}` | The same; the braces mark where the name ends, so `${SHOW}_v001` is `SHOW` followed by `_v001`, while `$SHOW_v001` reads a variable called `SHOW_v001` |
| `${VAR:-default}` | `default` when `VAR` is unset or empty |
| `$$` | A literal `$` |

A variable that is set but empty gives an empty value. An **unset** variable
with no default stops that clip with an error in the log
(`Environment variable $SHOW is not set`), so a typo never ends up in a path.
A `$` not followed by a name (`$5`) is left as typed. Windows `%VAR%` is not
expanded.

On the command line the shell expands `$VAR` inside double quotes before the
app sees it; inside single quotes the app does it. Quote patterns either way,
because the shell reads `<` and `>` as redirection:

```bash
uv run python main.py transcode -i clips/ -o "$DAILIES_ROOT/<date>/<reel>" --name '<name>_<codec>'
```

Example folder: `$DAILIES_ROOT/${SHOW:-misc}/<date>/<reel>`.

## Overlay tokens

Used by the **burn-in** fields and the **visible watermark** text on
EXR → Video ([slate / burn-in / watermark](./gui.md#slate--burn-in--watermark)).
Values come from the slate fields and the frame being rendered. An unknown
token is left in the text as typed, and a known token with no value becomes
empty.

### Frame

| Token | Aliases | Value |
|-------|---------|-------|
| `<frame>` | `<f>` | Current frame number, padded (changes every frame) |
| `<startframe>` | `<start>` | First frame of the range |
| `<endframe>` | `<end>` | Last frame of the range |
| `<framerange>` | `<range>` | Frame range, `1001-1100` |

`<frame>` makes the overlay render on every frame (one render thread);
without it the overlay is drawn once and reused.

### Shot

| Token | Aliases | Value |
|-------|---------|-------|
| `<show>` | | Show / production code |
| `<sequence>` | `<seq>` | Sequence name |
| `<shot>` | | Shot name |
| `<version>` | `<ver>` | Version string, `v0003` |
| `<take>` | | Take number |

### Production

| Token | Aliases | Value |
|-------|---------|-------|
| `<artist>` | | Artist name |
| `<vendor>` | | Studio / vendor name |
| `<scope>` | | Scope of work |
| `<shottypes>` | | Shot types, `2d comp, roto` |
| `<submitfor>` | `<status>` | Submission status: `WIP`, `FINAL`, `CBB` |
| `<date>` | | The slate date, or today (`YYYY-MM-DD`) |
| `<fps>` | | Frames per second |
| `<resolution>` | `<res>` | Output resolution, `1920x1080` |
| `<input>` | `<file>`, `<filename>` | Input file / sequence name |

## Invisible watermark payload

The [invisible watermark](./gui.md#invisible-watermark) payload template in
Preferences is the one field with its own older syntax, in curly braces:
`{user}`, `{host}`, `{date}` (`YYMMDD`), `{time}` (`HHMM`) and `{src}`. The
payload is at most 8 ASCII characters, so it keeps short tokens.
