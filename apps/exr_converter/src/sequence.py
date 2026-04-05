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

        results.append(
            {
                "name": s.head().rstrip("."),
                "frames": len(items),
                "range": range_str,
                "resolution": res_str,
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
