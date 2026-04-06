from __future__ import annotations

from pathlib import Path

import pyseq


def _probe_resolution(filepath: str) -> tuple[int, int]:
    """Read width and height from an EXR header without decoding pixels."""
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(filepath)
        if inp:
            spec = inp.spec()
            w, h = spec.width, spec.height
            inp.close()
            return w, h
    except Exception:
        pass
    return 0, 0


def probe_exr_colorspace(directory: str) -> str:
    """Return the oiio:ColorSpace from the first EXR in *directory*, or ''."""
    seqs = pyseq.get_sequences(directory)
    for s in sorted(
        (s for s in seqs if s.tail().lower() == ".exr"),
        key=lambda s: s.head(),
    ):
        items = list(s)
        if not items:
            continue
        try:
            import OpenImageIO as oiio

            inp = oiio.ImageInput.open(items[0].path)
            if inp:
                cs = inp.spec().getattribute("oiio:ColorSpace")
                inp.close()
                if cs:
                    return str(cs)
        except Exception:
            pass
        break
    return ""


def probe_exr_metadata(filepath: str) -> dict[str, str]:
    """Return a dict of human-readable EXR metadata from the first frame."""
    result: dict[str, str] = {}
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(filepath)
        if not inp:
            return {"error": "Could not open file"}
        spec = inp.spec()
        result["Resolution"] = f"{spec.width} \u00d7 {spec.height}"
        result["Channels"] = str(spec.nchannels)
        ch_names = [spec.channel_name(i) for i in range(spec.nchannels)]
        result["Channel names"] = ", ".join(ch_names)
        result["Pixel type"] = str(spec.format)
        comp = spec.getattribute("compression")
        if comp:
            result["Compression"] = str(comp)
        for attr in spec.extra_attribs:
            name = attr.name
            if name in ("compression",):
                continue
            val = str(attr.value)
            if len(val) > 200:
                val = val[:200] + "\u2026"
            result[name] = val
        inp.close()
    except Exception as e:
        result["error"] = str(e)
    return result


def scan_exr_sequences(directory: str) -> list[dict]:
    """Return metadata dicts for every EXR sequence found in *directory*.

    Each dict contains:
        name       - sequence head (e.g. "beauty")
        frames     - number of frames
        range      - human-readable frame range string
        resolution - "W\u00d7H" string from the first frame
        path       - the directory scanned
    """
    seqs = pyseq.get_sequences(directory)
    exr_seqs = sorted(
        (s for s in seqs if s.tail().lower() == ".exr"),
        key=lambda s: s.head(),
    )
    results = []
    for s in exr_seqs:
        items = list(s)
        frame_nums = sorted(int(i.frame) for i in items if i.frame is not None)
        if frame_nums:
            range_str = f"{frame_nums[0]}-{frame_nums[-1]}"
        else:
            range_str = "?"

        w, h = _probe_resolution(items[0].path) if items else (0, 0)
        res_str = f"{w}\u00d7{h}" if w and h else ""

        pixel_type = ""
        compression = ""
        colorspace = ""
        if items:
            try:
                import OpenImageIO as oiio

                inp = oiio.ImageInput.open(items[0].path)
                if inp:
                    spec = inp.spec()
                    pixel_type = str(spec.format)
                    comp = spec.getattribute("compression")
                    if comp:
                        compression = str(comp)
                    cs = spec.getattribute("oiio:ColorSpace")
                    if cs:
                        colorspace = str(cs)
                    inp.close()
            except Exception:
                pass

        results.append(
            {
                "name": s.head().rstrip("."),
                "frames": len(items),
                "range": range_str,
                "resolution": res_str,
                "pixel_type": pixel_type,
                "compression": compression,
                "colorspace": colorspace,
                "path": directory,
            }
        )
    return results


def find_exr_sequence(input_path: str) -> tuple[list[str], str]:
    """Resolve *input_path* to an ordered list of EXR file paths + a basename.

    *input_path* may be:
    - a directory  -> scan with pyseq, pick the first .exr sequence
    - a single .exr file -> scan its parent dir, find the sequence it belongs to
    """
    p = Path(input_path)
    if p.is_file():
        scan_dir = str(p.parent)
    elif p.is_dir():
        scan_dir = str(p)
    else:
        raise RuntimeError(f"Path does not exist: {input_path}")

    seqs = pyseq.get_sequences(scan_dir)
    exr_seqs = [s for s in seqs if s.tail().lower() == ".exr"]
    if not exr_seqs:
        raise RuntimeError(f"No EXR sequences found in {scan_dir}")

    if p.is_file():
        fname = p.name
        for s in exr_seqs:
            if any(str(item) == fname for item in s):
                return [item.path for item in s], s.head().rstrip(".")
        return [str(p)], p.stem

    seq = sorted(exr_seqs, key=lambda s: s.head())[0]
    return [item.path for item in seq], seq.head().rstrip(".")
