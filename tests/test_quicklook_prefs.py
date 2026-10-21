"""Quick Look format toggles, JSON contract, and appex Info.plist claims."""

from __future__ import annotations

import json
import plistlib
import re
import sys
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QGroupBox

from src.core.quicklook_formats import (
    APP_GROUP_ID,
    QUICKLOOK_FORMATS,
    decode_preferences,
    encode_preferences,
    merge_type_declarations,
    supported_content_types,
)
from src.core.quicklook_prefs import (
    format_enabled,
    publish_quicklook_preferences,
    set_format_enabled,
)
from src.gui.preferences import PreferencesDialog

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_quicklook_appex as ql_build  # noqa: E402


def test_defaults_and_saved_bool(settings):
    assert format_enabled(settings, "exr") is True
    assert format_enabled(settings, "dpx") is True
    assert format_enabled(settings, "png") is False
    assert format_enabled(settings, "dng") is False
    assert format_enabled(settings, "nope") is False
    set_format_enabled(settings, "exr", False)
    set_format_enabled(settings, "png", True)
    assert format_enabled(settings, "exr") is False
    assert format_enabled(settings, "png") is True


def test_stringly_false_from_ini(settings):
    settings.setValue("quicklook/format/dpx", "false")
    assert format_enabled(settings, "dpx") is False


def test_json_round_trip_and_unknown_keys():
    text = encode_preferences({"exr": False, "png": True, "made-up": True})
    parsed = decode_preferences(text)
    assert parsed is not None
    assert parsed["exr"] is False
    assert parsed["png"] is True
    assert parsed["dpx"] is True
    assert parsed["jpeg"] is False
    assert "made-up" not in parsed
    payload = json.loads(text)
    assert payload["version"] == 1
    assert set(payload["formats"]) == {fmt.key for fmt in QUICKLOOK_FORMATS}
    assert decode_preferences("not json") is None
    assert decode_preferences("[]") is None


def test_publish_writes_destinations_only(settings, tmp_path):
    set_format_enabled(settings, "webp", True)
    dest = tmp_path / "group" / "quicklook.json"
    written = publish_quicklook_preferences(settings, destinations=[dest])
    assert written == [dest]
    flags = json.loads(dest.read_text(encoding="utf-8"))["formats"]
    assert flags["webp"] is True
    assert flags["exr"] is True


def test_type_declarations_merge_without_dropping_bundle_id():
    info = {
        "CFBundleIdentifier": "com.vfxtools.exrconverter",
        "UTExportedTypeDeclarations": [
            {"UTTypeIdentifier": "com.example.other", "UTTypeDescription": "Other"}
        ],
    }
    merged = merge_type_declarations(info)
    assert merged["CFBundleIdentifier"] == "com.vfxtools.exrconverter"
    exported = [item["UTTypeIdentifier"] for item in merged["UTExportedTypeDeclarations"]]
    assert exported[0] == "com.example.other"
    assert "com.vfxtools.dpx-image" in exported
    imported = [item["UTTypeIdentifier"] for item in merged["UTImportedTypeDeclarations"]]
    assert imported == ["com.ilm.openexr-image"]
    assert "com.ilm.openexr-image" in supported_content_types()
    assert "public.mpeg" not in supported_content_types()
    assert "com.vfxtools.dpx-image" in supported_content_types()


def test_swift_catalog_and_app_group_match_python():
    formats = (ROOT / "packaging/macos/quicklook/Sources/EXRQLCore/Formats.swift").read_text(
        encoding="utf-8"
    )
    found = dict(
        re.findall(
            r'QuickLookFormat\(\s*key: "(\w+)".*?defaultEnabled: (true|false)',
            formats,
            flags=re.S,
        )
    )
    assert set(found) == {fmt.key for fmt in QUICKLOOK_FORMATS}
    for fmt in QUICKLOOK_FORMATS:
        assert found[fmt.key] == ("true" if fmt.default_enabled else "false")
    swift_prefs = (
        ROOT / "packaging/macos/quicklook/Sources/EXRQLCore/Preferences.swift"
    ).read_text(encoding="utf-8")
    assert APP_GROUP_ID in swift_prefs
    with (ROOT / "packaging/macos/entitlements.plist").open("rb") as handle:
        host = plistlib.load(handle)
    assert host["com.apple.security.application-groups"] == [APP_GROUP_ID]
    appex_plist = ROOT / "packaging/macos/quicklook/Entitlements/appex.entitlements"
    with appex_plist.open("rb") as handle:
        appex = plistlib.load(handle)
    assert appex["com.apple.security.application-groups"] == [APP_GROUP_ID]


def test_preview_info_plist_is_data_based():
    info = ql_build.extension_info_plist(
        bundle_id=ql_build.PREVIEW_ID,
        executable="EXRConverterPreview",
        display_name="EXR Converter Quick Look",
        version="0.0.0",
        principal_class="EXRConverterPreview.PreviewProvider",
        extension_point="com.apple.quicklook.preview",
        preview=True,
    )
    extension = info["NSExtension"]
    assert isinstance(extension, dict)
    attrs = extension["NSExtensionAttributes"]
    assert isinstance(attrs, dict)
    types = attrs["QLSupportedContentTypes"]
    assert isinstance(types, list)
    assert attrs["QLIsDataBasedPreview"] is True
    assert "com.ilm.openexr-image" in types
    assert "public.mpeg-4" not in types
    thumb = ql_build.extension_info_plist(
        bundle_id=ql_build.THUMB_ID,
        executable="EXRConverterThumbnail",
        display_name="EXR Converter Thumbnails",
        version="0.0.0",
        principal_class="EXRConverterThumbnail.ThumbnailProvider",
        extension_point="com.apple.quicklook.thumbnail",
        preview=False,
    )
    thumb_ext = thumb["NSExtension"]
    assert isinstance(thumb_ext, dict)
    thumb_attrs = thumb_ext["NSExtensionAttributes"]
    assert isinstance(thumb_attrs, dict)
    assert "QLIsDataBasedPreview" not in thumb_attrs


def test_preferences_dialog_quicklook_section(qapp, settings):
    dlg = PreferencesDialog(settings)
    titles = [box.title() for box in dlg.findChildren(QGroupBox)]
    if sys.platform == "darwin":
        assert "Quick Look" in titles
        exr = dlg.findChild(QCheckBox, "ql_exr")
        png = dlg.findChild(QCheckBox, "ql_png")
        assert exr is not None and exr.isChecked()
        assert png is not None and not png.isChecked()
    else:
        assert "Quick Look" not in titles


def test_release_workflow_embeds_quicklook_on_macos_only():
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    marker = "name: Embed Quick Look extensions"
    assert text.count(marker) == 1
    window = text[text.index(marker) : text.index(marker) + 500]
    assert "runner.os == 'macOS'" in window
    assert "build_quicklook_appex.py" in window
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "scripts/build_quicklook_appex.py" in makefile
