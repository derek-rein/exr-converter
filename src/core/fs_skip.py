"""Names the browsers' background walkers skip, and the video extensions they list.

Kept free of Qt so the sequence index process can import it without PySide6.
"""

from __future__ import annotations

SEARCH_SKIP_DIRS = frozenset(
    {
        # VCS
        ".git",
        ".svn",
        ".hg",
        ".bzr",
        # Python
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        ".tox",
        ".nox",
        "build",
        "dist",
        ".eggs",
        "site-packages",
        # IDE
        ".idea",
        ".vscode",
        ".vs",
        # Temp / cache
        ".cache",
        ".tmp",
        ".temp",
        "tmp",
        "temp",
        # macOS
        ".Trash",
        ".Spotlight-V100",
        ".fseventsd",
        ".DocumentRevisions-V100",
        ".TemporaryItems",
        ".VolumeIcon.icns",
        # System / library dirs (by name — catches nested occurrences too)
        "System",
        "Library",
        "private",
        "usr",
        "bin",
        "sbin",
        "etc",
        "var",
        "opt",
        # Windows
        "Windows",
        "ProgramData",
        "Program Files",
        "Program Files (x86)",
        "$Recycle.Bin",
        "System Volume Information",
        "AppData",
        "Recovery",
        "PerfLogs",
        # Linux
        "proc",
        "sys",
        "dev",
        "run",
        "snap",
        "lost+found",
        # Package / app internals
        ".app",
        ".framework",
        ".bundle",
        ".plugin",
        ".kext",
        "__MACOSX",
        "Frameworks",
        "PlugIns",
        # Cloud-client bookkeeping (Dropbox Smart Sync / File Provider)
        ".dropbox",
        ".dropbox.cache",
        ".dropbox.device",
        ".dropbox.attr",
    }
)

SEARCH_SKIP_ABSPATHS: frozenset[str] = frozenset(
    {
        "/System",
        "/Library",
        "/private",
        "/usr",
        "/bin",
        "/sbin",
        "/etc",
        "/var",
        "/opt",
        "/cores",
        "/dev",
        "/proc",
        "/sys",
        "/run",
        "/snap",
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
        "C:\\$Recycle.Bin",
        "C:\\Recovery",
    }
)

SEARCH_SKIP_FILES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "Icon\r",
        ".localized",
        ".CFUserTextEncoding",
        ".com.apple.timemachine.donotpresent",
    }
)
SEARCH_SKIP_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".o",
        ".obj",
        ".class",
        ".swp",
        ".swo",
        ".swn",
        ".tmp",
        ".bak",
        ".orig",
        ".partial",
        ".crdownload",
        ".download",
    }
)

VIDEO_EXTS = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".mxf",
        ".webm",
        ".m4v",
        ".ts",
        ".wmv",
        ".flv",
        ".f4v",
        ".vob",
        ".ogv",
        ".ogg",
        ".3gp",
        ".3g2",
        ".m2ts",
        ".mts",
        ".mpg",
        ".mpeg",
        ".m2v",
        ".divx",
        ".rm",
        ".rmvb",
        ".asf",
        ".dv",
        ".r3d",
        ".nev",
        ".braw",
        ".ari",
        ".arx",
        ".crm",
        ".mj2",
    }
)
