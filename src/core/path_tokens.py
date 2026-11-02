"""``<token>`` variables for transcode output folders and file names.

Uses the same ``<token>`` syntax as the slate / burn-in variables
(:mod:`src.render.tokens`): case-insensitive, and ``_`` / ``-`` are ignored, so
``<reelname>``, ``<reel_name>`` and ``<Reel-Name>`` are one token. A pattern
such as ``<reel>/<name>/<srcresolution>/<name>_<codec>`` turns
``A_0001C001_261022_145741_a12S6.mov`` into
``A_0001/A_0001C001_261022_145741_a12S6/3840x2160/A_0001C001_…_ProRes422HQ``.

Source values (reel, timecode, source resolution) come from the clip's own
metadata. When a clip has no reel name in its metadata, the reel is read from
a camera-style clip name (``A_0001C001…`` → ``A_0001``, ``A001C003…`` →
``A001``).

Both fields also expand Unix-style environment variables (``$SHOW``,
``${SHOW}``, ``${SHOW:-default}``) on every platform, before the tokens: ``$``
for values from the pipeline, ``<token>`` for values from the clip.

Qt-free so the GUI, the CLI and the tests share it.
"""

from __future__ import annotations

import datetime
import os
import re
from collections.abc import Mapping
from pathlib import Path

from ..render.tokens import TOKEN_RE, token_key

# (group label, [(canonical name, "<display>", description)]) — the same shape
# as :data:`src.render.tokens.TOKEN_GROUPS`, so ``TokenLineEdit`` shows either.
PATH_TOKEN_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Source",
        [
            ("name", "<name>", "Source file name without extension"),
            ("reel", "<reel>", "Reel name from the clip's metadata (or its camera clip name)"),
            ("srcresolution", "<srcresolution>", "Source resolution, e.g. 3840x2160"),
            ("srcwidth", "<srcwidth>", "Source width"),
            ("srcheight", "<srcheight>", "Source height"),
            ("srcfps", "<srcfps>", "Source frame rate"),
            ("timecode", "<timecode>", "Start timecode, e.g. 14_57_41_00"),
            ("parent", "<parent>", "Name of the folder the clip is in"),
        ],
    ),
    (
        "Output",
        [
            ("codec", "<codec>", "Codec, e.g. ProRes422HQ or H264"),
            ("resolution", "<resolution>", "Output resolution, e.g. 1920x1080"),
            ("width", "<width>", "Output width"),
            ("height", "<height>", "Output height"),
            ("fps", "<fps>", "Output frame rate"),
            ("date", "<date>", "Today's date (YYYY-MM-DD)"),
        ],
    ),
]

# Alternate spellings (already passed through ``token_key``) → canonical name.
PATH_ALIASES: dict[str, str] = {
    "filename": "name",
    "file": "name",
    "clip": "name",
    "clipname": "name",
    "input": "name",
    "reelname": "reel",
    "tape": "reel",
    "tapename": "reel",
    "sourceresolution": "srcresolution",
    "srcres": "srcresolution",
    "sourceres": "srcresolution",
    "sourcewidth": "srcwidth",
    "sourceheight": "srcheight",
    "srcresx": "srcwidth",
    "srcresy": "srcheight",
    "sourceresolutionx": "srcwidth",
    "sourceresolutiony": "srcheight",
    "srcresolutionx": "srcwidth",
    "srcresolutiony": "srcheight",
    "resx": "width",
    "resy": "height",
    "resolutionx": "width",
    "resolutiony": "height",
    "sourcefps": "srcfps",
    "res": "resolution",
    "outresolution": "resolution",
    "tc": "timecode",
    "folder": "parent",
}

KNOWN_TOKENS: frozenset[str] = frozenset(n for _g, toks in PATH_TOKEN_GROUPS for n, _d, _t in toks)
# Tokens that need the clip opened (metadata / source geometry).
SOURCE_PROBE_TOKENS: frozenset[str] = frozenset(
    {"reel", "srcresolution", "srcwidth", "srcheight", "srcfps", "timecode"}
)
# Tokens that need the output geometry (which needs the source size).
OUTPUT_GEOMETRY_TOKENS: frozenset[str] = frozenset({"resolution", "width", "height", "fps"})

# Characters that can't be in a file or folder name (any OS).
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SEPARATORS = re.compile(r"[\\/]+")

# Metadata keys (after ``_norm_key``) that hold a reel name, best first.
_REEL_KEYS: tuple[str, ...] = ("reelname", "reel", "reelid", "tapename", "tape", "reelno")
_TIMECODE_KEYS: tuple[str, ...] = ("timecode", "starttimecode", "tcstart")
# ARRI ``A_0001C001…``, RED / Sony / Canon ``A001C003…`` / ``A001_C003…``.
_CAMERA_REEL_RE = re.compile(r"^([A-Za-z]_?\d{3,4})_?C\d{3}", re.ASCII)


def canonical(name: str) -> str:
    key = token_key(name)
    return PATH_ALIASES.get(key, key)


def tokens_in(text: str | None) -> set[str]:
    """Canonical names of every ``<token>`` in *text* (known or not)."""
    return {canonical(m.group(1)) for m in TOKEN_RE.finditer(text or "")}


def needs_source_probe(*texts: str | None) -> bool:
    return any(tokens_in(t) & SOURCE_PROBE_TOKENS for t in texts)


def needs_output_geometry(*texts: str | None) -> bool:
    return any(tokens_in(t) & OUTPUT_GEOMETRY_TOKENS for t in texts)


def has_tokens(text: str | None) -> bool:
    return bool(TOKEN_RE.search(text or ""))


def known_tokens_hint() -> str:
    return ", ".join(d for _g, toks in PATH_TOKEN_GROUPS for _n, d, _t in toks)


def tokens_tooltip() -> str:
    lines: list[str] = []
    for group, toks in PATH_TOKEN_GROUPS:
        lines.append(f"{group}:")
        lines += [f"  {d}  {desc}" for _n, d, desc in toks]
    return "\n".join(lines)


def fps_label(fps: float) -> str:
    if not fps:
        return ""
    if abs(fps - round(fps)) < 1e-3:
        return str(int(round(fps)))
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def _norm_key(key: str) -> str:
    """``meta:com.apple.quicktime.reel_name`` → ``reelname`` (last dotted part)."""
    key = key.split(":", 1)[-1].rsplit(".", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _pick(meta: Mapping[str, str], keys: tuple[str, ...]) -> str:
    normed: dict[str, str] = {}
    for k, v in meta.items():
        v = str(v or "").strip()
        if v:
            normed.setdefault(_norm_key(str(k)), v)
    for key in keys:
        if normed.get(key):
            return normed[key]
    return ""


def reel_from_clip_name(name: str) -> str:
    """Reel from a camera clip name: ``A_0001C001_…`` → ``A_0001``; else ``""``."""
    m = _CAMERA_REEL_RE.match(Path(name).stem)
    return m.group(1) if m else ""


def _parse_dims(text: str) -> tuple[int, int]:
    m = re.match(r"\s*(\d+)\s*[x×]\s*(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _libav_source_info(path: str) -> dict[str, str]:
    """Reel / timecode / geometry of a PyAV-readable clip (container + stream tags)."""
    import av

    from .video import probe_video

    meta: dict[str, str] = {}
    try:
        with av.open(path) as container:
            meta.update({str(k): str(v) for k, v in container.metadata.items()})
            for stream in container.streams:
                for k, v in stream.metadata.items():
                    meta.setdefault(str(k), str(v))
    except Exception:
        pass
    info = {"reel": _pick(meta, _REEL_KEYS), "timecode": _pick(meta, _TIMECODE_KEYS)}
    try:
        w, h, fps, _n = probe_video(path)
    except Exception:
        w = h = 0
        fps = 0.0
    info.update(srcwidth=str(w or ""), srcheight=str(h or ""), srcfps=fps_label(fps))
    return info


def _raw_source_info(path: str) -> dict[str, str]:
    """Same for camera RAW / plugin sources, via their metadata probe."""
    from .video import probe_video_metadata

    meta = probe_video_metadata(path)
    w, h = _parse_dims(meta.get("Video resolution", ""))
    try:
        fps = float(meta.get("Video fps", "") or 0)
    except ValueError:
        fps = 0.0
    return {
        "reel": _pick(meta, _REEL_KEYS),
        "timecode": _pick(meta, _TIMECODE_KEYS),
        "srcwidth": str(w or ""),
        "srcheight": str(h or ""),
        "srcfps": fps_label(fps),
    }


def probe_source_info(path: str | os.PathLike[str]) -> dict[str, str]:
    """Source token values for *path*: reel, timecode, srcwidth/height/fps.

    Never raises; values it can't read are ``""``. The reel falls back to the
    camera clip name when the metadata has none.
    """
    from .video import is_libav_unsafe_media

    p = os.fspath(path)
    try:
        from .braw import is_braw_path
        from .r3d import is_r3d_path
        from .source_plugins import plugin_for_path

        raw = bool(
            plugin_for_path(p) or is_r3d_path(p) or is_braw_path(p) or is_libav_unsafe_media(p)
        )
        info = _raw_source_info(p) if raw else _libav_source_info(p)
    except Exception:
        info = {}
    info = {
        k: str(info.get(k, "") or "")
        for k in ("reel", "timecode", "srcwidth", "srcheight", "srcfps")
    }
    if not info["reel"]:
        info["reel"] = reel_from_clip_name(Path(p).name)
    return info


def build_path_values(
    source: str | os.PathLike[str],
    *,
    codec: str = "",
    width: int = 0,
    height: int = 0,
    fps: float = 0.0,
    source_info: Mapping[str, str] | None = None,
    today: datetime.date | None = None,
) -> dict[str, str]:
    """Token → value map for one clip. *source_info* is :func:`probe_source_info`."""
    src = Path(source)
    info = dict(source_info or {})
    if not info.get("reel"):
        info["reel"] = reel_from_clip_name(src.name)
    sw, sh = info.get("srcwidth", ""), info.get("srcheight", "")
    return {
        "name": src.stem,
        "parent": src.parent.name,
        "reel": info.get("reel", ""),
        "timecode": info.get("timecode", ""),
        "srcwidth": sw,
        "srcheight": sh,
        "srcresolution": f"{sw}x{sh}" if sw and sh else "",
        "srcfps": info.get("srcfps", ""),
        "codec": codec,
        "width": str(width) if width else "",
        "height": str(height) if height else "",
        "resolution": f"{width}x{height}" if width and height else "",
        "fps": fps_label(fps),
        "date": (today or datetime.date.today()).isoformat(),
    }


def substitute_path(text: str, values: Mapping[str, str]) -> str:
    """Replace ``<token>``s in *text*. Unknown tokens raise :class:`ValueError`.

    A value can't add folders or bad characters: ``/``, ``:`` and the like in a
    value become ``_``. The literal text around tokens is left as typed.
    """

    def _sub(m: re.Match[str]) -> str:
        key = canonical(m.group(1))
        if key not in KNOWN_TOKENS:
            raise ValueError(f"Unknown name token <{m.group(1)}> (use {known_tokens_hint()}).")
        return BAD_NAME_CHARS.sub("_", str(values.get(key, "") or ""))

    return TOKEN_RE.sub(_sub, text or "")


_ENV_RE = re.compile(
    r"\$\$"  # $$ → a literal $
    r"|\$\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
    r"|\$(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
)


def expand_env(text: str | None, env: Mapping[str, str] | None = None) -> str:
    """Expand Unix-style environment variables in *text*, on every platform.

    ``$VAR`` and ``${VAR}`` take the variable's value; ``${VAR:-default}`` uses
    *default* when ``VAR`` is unset or empty; ``$$`` is a literal ``$``. An unset
    variable with no default raises :class:`ValueError`, so a typo never lands
    in a path. A ``$`` not followed by a name is left as is.
    """
    environ = os.environ if env is None else env

    def _sub(m: re.Match[str]) -> str:
        if m.group(0) == "$$":
            return "$"
        name = m.group("braced") or m.group("bare")
        value = environ.get(name)
        default = m.group("default")
        if default is not None:
            return value if value else default
        if value is None:
            raise ValueError(
                f"Environment variable ${name} is not set "
                f"(use ${{{name}:-fallback}} for a default)."
            )
        return value

    return _ENV_RE.sub(_sub, text or "")


def _clean_segment(seg: str) -> str:
    # A token with no value leaves a stray separator, e.g. "<reel>_<name>"
    # with no reel → "_A001C003"; trim those along with spaces and dots.
    return BAD_NAME_CHARS.sub("_", seg).strip(" ._-")


def expand_name_pattern(pattern: str, values: Mapping[str, str]) -> tuple[list[str], str]:
    """Expand a name *pattern* into (sub-folders, file stem).

    ``/`` (or ``\\``) in the pattern makes sub-folders. Empty folder parts (a
    token with no value) are dropped. ``..`` is not allowed.
    """
    expanded = substitute_path(pattern, values)
    parts = [_clean_segment(p) for p in _SEPARATORS.split(expanded)]
    if any(p == ".." for p in _SEPARATORS.split(expanded)):
        raise ValueError("The file name pattern can't use '..'.")
    stem = parts[-1] if parts else ""
    folders = [p for p in parts[:-1] if p]
    if not stem:
        raise ValueError("The file name pattern produced an empty name.")
    return folders, stem


def expand_folder(folder: str, values: Mapping[str, str]) -> Path:
    """Expand tokens in an output *folder* path.

    A folder level that is only tokens with no value is dropped
    (``/out/<reel>/day1`` with no reel → ``/out/day1``). Text without tokens
    is left exactly as typed.
    """
    if not has_tokens(folder):
        return Path(folder).expanduser()
    parts = re.split(r"([\\/]+)", folder)  # levels and separators, alternating
    out: list[str] = []
    skip_sep = False
    for part in parts:
        if _SEPARATORS.fullmatch(part or ""):
            if skip_sep:
                skip_sep = False
                continue
            out.append(part)
            continue
        if not has_tokens(part):
            out.append(part)
            continue
        val = substitute_path(part, values).strip()
        if not val.strip(" ._-"):
            skip_sep = True  # drop this level and the separator after it
            continue
        out.append(val)
    return Path("".join(out)).expanduser()


__all__ = [
    "KNOWN_TOKENS",
    "PATH_ALIASES",
    "PATH_TOKEN_GROUPS",
    "build_path_values",
    "canonical",
    "expand_env",
    "expand_folder",
    "expand_name_pattern",
    "has_tokens",
    "needs_output_geometry",
    "needs_source_probe",
    "probe_source_info",
    "reel_from_clip_name",
    "substitute_path",
    "tokens_in",
    "tokens_tooltip",
]
