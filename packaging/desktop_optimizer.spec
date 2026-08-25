# PyInstaller spec for Desktop Optimizer.
#
# Build (from the project root):
#     pyinstaller packaging/desktop_optimizer.spec --noconfirm
#
# Produces a windowed onedir bundle in dist/DesktopOptimizer/. onedir is
# deliberate: a onefile build unpacks ~150 MB of Qt to %TEMP% on every
# launch, which is exactly the kind of disk churn this app exists to warn
# about.
import os
import sys

sys.path.insert(0, os.path.abspath("."))
from app.version import APP_NAME, __version__  # noqa: E402

# Qt modules this app never touches. Trimming them keeps the install
# smaller and the process image leaner.
EXCLUDES = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtWebSockets", "PySide6.QtWebChannel",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtUiTools", "PySide6.QtSpatialAudio",
    "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtSerialPort", "PySide6.QtNfc", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtStateMachine",
    "PySide6.QtTextToSpeech", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    # not used by this app, and heavy
    "tkinter", "matplotlib", "scipy", "pandas", "PIL", "pytest",
    "setuptools", "pip",
]

a = Analysis(
    ["../main.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=[],
    # truststore is imported lazily inside app.updates, and it picks its
    # platform backend dynamically -- name the modules so the frozen build
    # actually contains them.
    hiddenimports=["truststore", "truststore._windows", "truststore._api",
                   "truststore._ssl_constants"],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DesktopOptimizer",
    icon="../assets/app.ico",
    version="version_info.txt",
    console=False,          # windowed: no console flash on launch
    disable_windowed_traceback=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="DesktopOptimizer",
)
