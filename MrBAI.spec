# -*- mode: python ; coding: utf-8 -*-
# Build with:  pyinstaller --noconfirm MrBAI.spec
import os
import sys
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
pkgs = ["google.genai", "webview", "certifi"]
if sys.platform == "win32":
    pkgs += ["clr_loader", "pythonnet"]          # pywebview's Windows (WebView2) backend
for pkg in pkgs:
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MrBAI",
    console=False,                                # no terminal window
    upx=False,
    icon="assets/icon.ico" if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="MrBAI")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Mr. B AI.app",
        icon="assets/icon.icns",
        bundle_identifier="com.mrb.ai",
        info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": os.environ.get("APP_VERSION", "1.0.0")},
    )
