# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir specification for the unsigned Windows release candidate."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata


repo = Path(SPEC).resolve().parents[1]
backend = repo / "backend"

datas = [
    (str(repo / "config" / "career_strategy.yaml"), "config"),
    (str(repo / "frontend" / "dist"), "frontend/dist"),
    (str(backend / "alembic.ini"), "backend"),
    (str(backend / "alembic"), "backend/alembic"),
]
binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("uvicorn")

# These libraries use runtime plugin discovery or metadata in paths that a
# static import walk cannot always see. No browser binary/profile is bundled.
for module in ("agents", "openai", "playwright", "fitz", "docx"):
    module_datas, module_binaries, module_hidden = collect_all(module)
    datas += module_datas
    binaries += module_binaries
    hiddenimports += module_hidden

for distribution in (
    "openai",
    "openai-agents",
    "playwright",
    "PyMuPDF",
    "python-docx",
    "uvicorn",
):
    try:
        datas += copy_metadata(distribution)
    except Exception:
        # A missing optional distribution should be diagnosed by the build or
        # clean-runtime smoke test, not make the spec itself machine-specific.
        pass

# Agents SDK and MCP inspect distribution versions at import time. Recursively
# preserve installed dependency metadata without bundling any user data.
datas += copy_metadata("openai-agents", recursive=True)

a = Analysis(
    [str(backend / "app" / "portable.py")],
    pathex=[str(backend)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JobAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    contents_directory="runtime",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JobAgent",
)
