# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir + windowed。用法（repo 根目錄）：
#   venv\Scripts\python -m PyInstaller --noconfirm --clean packaging\IcopeTool.spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent
datas, binaries, hiddenimports = [], [], []
for package in ("ddddocr", "onnxruntime"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

datas += [
    (str(ROOT / "icope_tool" / "resources"), "icope_tool/resources"),
    (str(ROOT / "icope_tool" / "services" / "pdf" / "fonts"), "icope_tool/services/pdf/fonts"),
    (str(ROOT / "examples"), "examples"),
]
hiddenimports += ["smartcard.scard", "smartcard.pcsc.PCSCReader", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
                  "PySide6.QtPrintSupport", "PySide6.QtSvg"]

a = Analysis(
    [str(ROOT / "icope_tool" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "tkinter", "matplotlib", "IPython", "pytest", "pytestqt",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuick3D",
        "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest",
    ],
    noarchive=False,
)
# 瘦身：ddddocr 預設只用 common_old.onnx；不需要影片解碼與軟體 OpenGL；Qt 翻譯只留繁中
DROP = ("ddddocr/common.onnx", "ddddocr/common_det.onnx", "opencv_videoio_ffmpeg", "opengl32sw.dll")


def _keep(entry):
    dest = entry[0].replace("\\", "/")
    if any(token in dest for token in DROP):
        return False
    if "/translations/" in dest and dest.endswith(".qm"):
        return dest.rsplit("/", 1)[-1] in ("qtbase_zh_TW.qm",)
    return True


a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IcopeTool",
    console=False,
    icon=str(ROOT / "icope_tool" / "resources" / "app.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="IcopeTool", upx=False)
