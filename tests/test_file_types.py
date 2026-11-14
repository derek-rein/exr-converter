from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from src.core.file_types import (
    VIEWER_FILE_TYPES,
    linux_mime_types,
    linux_mime_xml,
    macos_document_types,
    merge_document_types,
    offered_extensions,
    offered_types,
    windows_progid,
)
from src.core.quicklook_formats import QUICKLOOK_FORMATS

ROOT = Path(__file__).resolve().parent.parent


def test_only_the_formats_nobody_else_handles_are_offered_by_default() -> None:
    assert offered_extensions() == ("exr", "dpx", "r3d", "braw")
    for opt_in in ("dng", "tif", "tiff", "mov", "mp4", "mxf"):
        assert opt_in not in offered_extensions()


def test_every_type_has_a_label_extensions_and_mime() -> None:
    keys = [t.key for t in VIEWER_FILE_TYPES]
    assert len(keys) == len(set(keys))
    for t in VIEWER_FILE_TYPES:
        assert t.label and t.extensions and "/" in t.mime
        assert all(e == e.lower() and not e.startswith(".") for e in t.extensions)


def test_macos_types_are_viewer_alternate_and_never_default() -> None:
    entries = macos_document_types()
    assert len(entries) == len(offered_types())
    for entry in entries:
        assert entry["CFBundleTypeRole"] == "Viewer"
        assert entry["LSHandlerRank"] == "Alternate"
        assert entry.get("LSItemContentTypes") or entry.get("CFBundleTypeExtensions")
    by_name = {e["CFBundleTypeName"]: e for e in entries}
    # The UTIs match the ones Quick Look already declares, so the two cannot drift.
    exr = next(f for f in QUICKLOOK_FORMATS if f.key == "exr")
    assert set(by_name["OpenEXR image"]["LSItemContentTypes"]) == set(exr.content_types)
    assert by_name["RED R3D clip"]["CFBundleTypeExtensions"] == ["r3d"]


def test_merging_keeps_other_document_types_and_is_repeatable() -> None:
    info = {"CFBundleDocumentTypes": [{"CFBundleTypeName": "Something else"}], "X": 1}
    once = merge_document_types(info)
    twice = merge_document_types(once)
    names = [d["CFBundleTypeName"] for d in twice["CFBundleDocumentTypes"]]
    assert names[0] == "Something else"
    assert len(names) == len(set(names)) == 1 + len(offered_types())
    assert twice["X"] == 1


def test_linux_desktop_entry_matches_the_catalog() -> None:
    text = (ROOT / "packaging/linux/exr-converter.desktop").read_text(encoding="utf-8")
    assert "Exec=exr-converter %F" in text  # files from the file manager, none from the menu
    line = next(ln for ln in text.splitlines() if ln.startswith("MimeType="))
    assert line == "MimeType=" + ";".join(linux_mime_types()) + ";"


def test_linux_mime_xml_is_current_and_valid() -> None:
    on_disk = (ROOT / "packaging/linux/exr-converter-mime.xml").read_text(encoding="utf-8")
    assert on_disk == linux_mime_xml()
    root = ET.fromstring(on_disk)
    types = {el.attrib["type"] for el in root}
    # Only the types the system database does not already have.
    assert "image/x-dpx" in types and "image/x-exr" not in types
    ns = "{http://www.freedesktop.org/standards/shared-mime-info}"
    globs = {g.attrib["pattern"] for g in root.iter(ns + "glob")}
    assert {"*.r3d", "*.R3D", "*.braw", "*.dpx"} <= globs


def test_windows_installer_registers_every_offered_type_without_taking_the_default() -> None:
    iss = (ROOT / "installer.iss").read_text(encoding="utf-8")
    assert "ChangesAssociations=yes" in iss
    assert 'Name: "associate"' in iss
    for ext in offered_extensions():
        prog = windows_progid(ext)
        assert rf'Software\Classes\{prog}\shell\open\command"' in iss
        assert rf'Software\Classes\.{ext}\OpenWithProgids"' in iss
        assert f'ValueName: ".{ext}"; ValueData: "{prog}"' in iss
    # Offer, never take: nothing writes an extension's own default or UserChoice.
    assert "UserChoice" not in iss
    assert not re.search(r'Subkey: "Software\\Classes\\\.\w+"; ValueType', iss)
    # Every registry line of ours belongs to the task, so unticking it registers nothing.
    block = iss.split("File types: begin")[1].split("File types: end")[0]
    for line in block.splitlines():
        if line.startswith("Root:"):
            assert "Tasks: associate" in line, line
    assert '--view ""%1""' in iss


def test_rpm_and_tarball_ship_the_mime_files() -> None:
    script = (ROOT / "scripts/package_linux.py").read_text(encoding="utf-8")
    assert "/usr/share/mime/packages/exr-converter.xml" in script
    assert "postinstall" in script and "postremove" in script
    for name in ("postinstall.sh", "postremove.sh"):
        body = (ROOT / "packaging/linux" / name).read_text(encoding="utf-8")
        assert body.startswith("#!/bin/sh") and "update-mime-database" in body
        assert "\r" not in body  # a CRLF would break the shebang


def test_the_mac_build_merges_document_types_into_the_host_plist() -> None:
    script = (ROOT / "scripts/build_quicklook_appex.py").read_text(encoding="utf-8")
    assert "merge_document_types(merge_type_declarations(info))" in script
