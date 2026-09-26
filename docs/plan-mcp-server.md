---
title: MCP server (design)
weight: 95
description: Design for a local MCP server so agents can probe media and run video ↔ EXR converts
---

**Status:** proposal (no server in the tree yet)
**Date:** 2026-09-26
**Related code:** `src/cli.py` (`run_cli`, `resolve_v2e_spaces`, `resolve_e2v_spaces`), `src/core/convert.py` (`run_video_to_exr`, `run_exr_to_video`), `src/core/constants.py`, `src/core/video.py`, `src/core/sequence.py`, `src/core/ocio_utils.py`

This note is the design for a **local [MCP](https://modelcontextprotocol.io) server** so Cursor and other agents can use EXR Converter the same way the CLI does: probe media, resolve OCIO, and convert **video ↔ OpenEXR**. It is a design record, in the same spirit as [12-bit ProRes (oxideav)](./plan-12bit-prores-oxideav.md).

---

## 1. Recommendation

Ship a **stdio** MCP server as an **optional extra**, in-process, on top of the existing Python convert API.

| Choice | Decision |
|--------|----------|
| Transport | **stdio** (the host launches the server as a subprocess). Streamable HTTP is a later deployment concern and is out of scope. |
| SDK | Official **MCP Python SDK v2** (`mcp` on PyPI, `MCPServer`). Pin `mcp>=2,<3`. |
| Call path | Call `run_video_to_exr` / `run_exr_to_video` and the CLI color resolvers. The server speaks MCP on stdout, so it must not shell out to `main.py` and must not `print` protocol-bound stdout. |
| Install | Optional extra, script entry `exr-converter-mcp`. Default `uv sync` and **Nuitka** bundles stay free of the SDK. |
| v1 tools | Probe, list codecs / color spaces, convert both directions. Slate, burn-in, and watermark stay GUI-only. |

Agents already can run `uv run python main.py video2exr …` in a shell. An MCP server is still worth it: typed tools, progress, cooperative cancel, honest codec metadata, and a path policy the model cannot skip by inventing a shell command.

---

## 2. What agents need

The jobs that show up in practice match the CLI, not the GUI:

1. **Inspect** a clip or image sequence (resolution, frame count, fps, color-space candidates, whether R3D / BRAW bridges are present).
2. **Choose** a codec and OCIO spaces with the same defaults the CLI uses when `--src` / `--dst` are omitted.
3. **Convert** video → EXR or sequence → video, with progress and a way to stop.
4. **Learn the ladder** on this machine: VideoToolbox only on macOS, software ProRes always 10-bit, oxideav `prores_ox_*` only when `exr_prores` is built.

Slate, burn-in, watermark, preferences, and the sequence player are **QPainter / Qt**. They stay on the GUI. v1 tool descriptions should say overlays are unavailable so the model does not invent flags for them.

---

## 3. Why this shape

### Call the library, not the CLI process

`run_cli` already does the right orchestration:

- OCIO config: `--ocio`, else `$OCIO`, else bundled ACES Studio (`resolve_ocio_for_cli`, `_resolve_config_source`).
- Color: `resolve_v2e_spaces` / `resolve_e2v_spaces` (probe + `find_equivalent_space` + roles).
- Encode/decode: `run_video_to_exr` / `run_exr_to_video`, which already take `progress`, `cancel_check`, and `log` callbacks.
- Codecs: `available_video_codecs()` filters platform and oxideav availability. Bit depth lives on `VideoCodecSpec`.

Those callbacks are the MCP progress and cancel hooks. A subprocess wrapper would fight stdio (MCP owns stdout), lose structured errors, and duplicate color logic.

`resolve_*_spaces` currently take an `argparse.Namespace`. The server can pass a `SimpleNamespace` with `input`, `src`, and `dst`. A later cleanup can turn that into a small dataclass shared by CLI and MCP. Do not fork a second color policy.

### stdio, optional extra, not in the frozen app

Desktop users install a Nuitka binary. Agents on a checkout run `uv`. The SDK pulls a server stack (Pydantic and friends) that the GUI never imports. Keep it optional:

```toml
[project.optional-dependencies]
mcp = ["mcp>=2,<3"]

[project.scripts]
exr-converter-mcp = "src.mcp_server:main"
```

`src/core/convert.py` does not import PySide6. The MCP entry must import `src.core` and the CLI helpers only. Importing `main.py` is harmless today (Qt loads inside `main()`), but the server should not call `main()`.

Nuitka strip lists, AppImage, and DMG stay unchanged. A frozen binary is a poor MCP host: stdout is the protocol, and the bundle is built to open a GUI.

### One long tool call, with a timeout escape hatch

Converts run for seconds to many minutes. v1 tools **block until the job finishes** and stream `progress` / log lines, matching Ctrl-C on the CLI (`cancel_check` → `ConversionCancelled`).

Run the blocking `run_*` function on a worker thread (`anyio.to_thread.run_sync`) so the MCP event loop can emit progress and watch cancellation. `run_*` invokes `progress` on the calling thread; hop back to the async loop before `await ctx.report_progress(...)`. Map client `notifications/cancelled` onto the same `threading.Event` the CLI uses for SIGINT.

Hold a process-wide lock so two tool calls cannot start two process pools. The convert path already uses `multiprocessing` **spawn** (`_MP_CTX` in `convert.py`).

Some hosts kill a tool call on a short timeout. If that shows up in practice, add a second pair — `start_convert` plus `convert_status` — with one in-memory job. Do not build that queue until a host actually times out. The stdio process lives for the agent session, so in-memory job state is enough.

Confirm on the pinned SDK that the tool `Context` exposes the cancel event (`cancel_requested` on the shared context). If the high-level `Context` hides it, poll the session another way during implementation and record the exact attribute in this doc.

---

## 4. Tool surface (v1)

Names are stable API once shipped. Descriptions and return values are what the model sees; keep bit-depth wording aligned with [CLI](./cli.md) and [ProRes](./prores.md).

Annotations: read-only tools set `read_only_hint=True` and `open_world_hint=False`. Convert tools set `read_only_hint=False`, `destructive_hint=False`, `idempotent_hint=False`. `destructive_hint=True` only when `overwrite` is true.

### `probe_media`

Read-only. One absolute path: a video file, `.r3d` / `.nev` / `.braw`, a sequence directory, or an existing frame.

Returns JSON:

| Field | Source |
|-------|--------|
| `kind` | `video` or `sequence` |
| `path` | resolved absolute path |
| `width`, `height`, `fps`, `frame_count` | `probe_video_metadata` or `find_exr_sequence_info` |
| `frames` | first/last frame numbers for sequences |
| `colorspace_candidates` | `guess_video_colorspace_candidates` or still metadata / scene-referred vs display |
| `suggested_src`, `suggested_dst` | same resolvers as the CLI, on the active config |
| `bridge` | `r3d`, `braw`, or empty; include the missing-SDK message when the extension is absent |
| `warnings` | probe failures that are not fatal |

### `list_codecs`

Read-only. Returns `available_video_codecs()` as records: `key`, `display_name`, `bit_depth`, `chroma`, `pix_fmt`, `libav_codec`, `platforms`. Omit codecs `is_available()` / oxideav filtering already drops. The description states that software ProRes (`prores`, `prores_4444`, `prores_xq`) is **10-bit** encode.

### `list_color_spaces`

Read-only. Optional `ocio_config` path (same resolution as CLI when omitted). Returns family → names from `color_space_families`, plus the active config path and OCIO version. Cap the payload: families and names, not the whole `.ocio` file.

### `list_exr_compressions`

Read-only. `EXR_COMPRESSIONS` and the default `dwaa`.

### `convert_video_to_exr`

| Argument | Default | Maps to |
|----------|---------|---------|
| `input` | required | video / R3D / BRAW path |
| `output_dir` | `<parent>/<stem>/` | `default_v2e_output_dir` |
| `ocio_config` | CLI resolution | `resolve_ocio_for_cli` |
| `src`, `dst` | auto | `resolve_v2e_spaces` |
| `compression` | `dwaa` | `EXR_COMPRESSIONS` |
| `dwa_level`, `zip_level` | library | `exr_opts` |
| `scale` | `1.0` | |
| `padding` | `4` | |
| `start_frame` | `1001` | |
| `frame_range` | all | `parse_frame_range` |
| `deinterlace` | `auto` | `auto` / `on` / `off` |
| `workers` | `0` (auto) | |
| `overwrite` | `false` | see path policy |

Returns `{output_dir, sequence_pattern, src_space, dst_space, frames_written, warnings}`.

### `convert_exr_to_video`

| Argument | Default | Maps to |
|----------|---------|---------|
| `input` | required | directory or existing frame |
| `output` | sibling path | `default_e2v_output_path` |
| `codec` | `prores` | key from `list_codecs` |
| `fps` | `24` | |
| `ocio_config`, `src`, `dst` | CLI defaults | `resolve_e2v_spaces` |
| `crf`, `preset` | codec default | `codec_opts` for H.264 / HEVC |
| `scale`, `frame_range`, `workers`, `overwrite` | same as above | |

Reject unknown or unavailable codec keys with the same error the CLI prints (`codec … is not available on this platform`). Returns `{output, src_space, dst_space, frames, codec, bit_depth, warnings}`.

Display destinations that are OCIO **displays** keep today’s export behavior (viewing-rule default view, not Un-tone-mapped). The tool description should say that in one sentence so agents do not “fix” it by picking a utility transform unless the user asked for colorimetric output.

### Resources and prompts

Resources (the host attaches them; the model does not have to call a tool):

| URI | Body |
|-----|------|
| `exr-converter://version` | `APP_VERSION` |
| `exr-converter://codecs` | same payload as `list_codecs` |
| `exr-converter://defaults` | default spaces, compression, codec, fps, start frame |

Do not expose the user’s disk as resources. Paths arrive as tool arguments.

Prompts can wait. Two that match real phrasing, when added: `ingest_plate` (video → ACEScg EXR) and `review_export` (sequence → Rec.709 ProRes).

`MCPServer` `instructions` should be short and factual: absolute paths, probe before convert, software ProRes is 10-bit, overlays are GUI-only, R3D/BRAW need optional bridges, omitted color spaces follow the CLI.

---

## 5. Path policy

The server runs as the user. It can write anywhere that user can write. Tools need a policy so a confused argument does not clobber a show directory.

1. Require paths that resolve to absolute paths. Expand `~`. Reject empty and non-local inputs.
2. When the client advertises **roots**, call `roots/list` and refuse inputs and outputs outside those `file://` roots. When the client sends no roots, allow any path the OS user can use — same trust model as the CLI.
3. `overwrite: false` (default): refuse if the output directory already contains `stem.*.exr`, or if the output video file exists.
4. `overwrite: true`: replace those outputs only. Do not delete sibling files that are not part of the sequence pattern being written.
5. Return the resolved absolute output path in the tool result so the agent does not guess.

Log lines go to MCP logging (`ctx.info` / `ctx.warning`) and stderr. Never stdout.

---

## 6. Host configuration

Cursor (and Claude Desktop, and any other stdio host) only needs a command. Example for a source checkout:

```json
{
  "mcpServers": {
    "exr-converter": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/absolute/path/to/exr-converter",
        "--extra",
        "mcp",
        "exr-converter-mcp"
      ]
    }
  }
}
```

`uv run` uses the project Python 3.13 environment. `$OCIO` and bridge library paths belong in the server entry’s `env` when the host does not inherit the user shell.

There is no network listener, no token, and no cloud service in v1.

---

## 7. Layout and tests

```text
src/mcp_server.py    # MCPServer, tools, main(); or src/mcp/ if it grows past one module
tests/test_mcp_server.py
```

Keep the module free of Qt imports. Color and output-path helpers stay in `src/cli.py` / `src/core/` and are called, not copied.

Tests use the SDK’s in-memory client against the server object (no subprocess, no stdio flake):

| Test | Kind |
|------|------|
| Tool list and codec records match `available_video_codecs()` | unit |
| Path policy: relative path rejected, outside roots rejected, overwrite guard | unit |
| `probe_media` on a tiny synthetic sequence | unit or existing fixture |
| Convert tools call `run_*` with the resolved spaces | unit with the convert functions monkeypatched |
| One real tiny convert | `@pytest.mark.integration`, same as today’s CLI media tests |

`make test-unit` must pass without the `mcp` extra installed: skip the module when `import mcp` fails, or install the extra in CI. Prefer installing the extra in CI so the skip does not hide a broken server. Default `uv sync` for GUI hacking can stay lean; document `uv sync --extra mcp`.

When the server ships, update [CLI](./cli.md) with the launch snippet, [CHANGELOG.md](../CHANGELOG.md) under `Added`, and this file’s status line. Until then this page is the only user-facing mention.

---

## 8. Implementation order

1. Optional extra, `exr-converter-mcp` entry, server instructions, `list_codecs`, `list_exr_compressions`, `exr-converter://version`. In-memory tests.
2. `probe_media` and `list_color_spaces` using the existing probe and OCIO helpers.
3. Path policy + both convert tools, progress, cancel, single-flight lock.
4. Docs and changelog in the same change as the working server.

Out of scope until someone asks: Streamable HTTP, authentication, slate / burn-in / watermark, driving the Qt window, remote render farms, bundling the server inside Nuitka.

---

## 9. Risks

| Risk | Mitigation |
|------|------------|
| Host tool timeout mid-convert | Progress on the blocking call first; add `start_convert` / `convert_status` only if a host cuts the call off |
| stdout corruption | No `print` in the server; logging on stderr; do not subprocess the CLI |
| Duplicate color policy | Call `resolve_v2e_spaces` / `resolve_e2v_spaces` |
| Agents treat ProRes 4444 as 12-bit | Codec records and tool text use `VideoCodecSpec.bit_depth` |
| Two heavy converts at once | One-job lock |
| MCP SDK v2 API drift | Pin `mcp>=2,<3`; spike `Context` cancel before wiring `cancel_check` |
| Qt or GPU preview pulled into the agent process | Do not import `src.gui` |
| Accidental overwrite | Default `overwrite: false` plus optional roots |
