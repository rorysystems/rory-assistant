# PyInstaller: gera dist/Rory Assistant/ (pasta, não arquivo único: abre mais rápido).
# Rodar da raiz do projeto:  pyinstaller --noconfirm packaging/rory-assistant.spec
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APP_NAME = "Rory Assistant"

datas = [
    (str(ROOT / "ui"), "ui"),
    (str(ROOT / "assets"), "assets"),
]
# MarkItDown usa o Magika (modelo de detecção de tipo) e plugins por import dinâmico.
datas += collect_data_files("magika")
datas += collect_data_files("markitdown")
hiddenimports = collect_submodules("markitdown") + collect_submodules("claude_agent_sdk")
# transcrição local (faster-whisper + ctranslate2 + PyAV) e OCR do Windows (pywinrt)
datas += collect_data_files("faster_whisper")  # modelo VAD (silero) vem como dado do pacote
hiddenimports += collect_submodules("faster_whisper") + ["ctranslate2", "av"]
if sys.platform == "win32":
    hiddenimports += collect_submodules("winrt")

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    # extras do markitdown[all] que o app nao usa (PDF e feito com pypdf/pypdfium2)
    excludes=[
        "tkinter", "matplotlib", "IPython", "pytest",
        "speech_recognition", "pdfminer", "pydub", "azure", "youtube_transcript_api",
    ],
    noarchive=False,
)

# O SDK traz um claude.exe próprio (~345 MB). O app usa o Claude Code instalado na
# máquina (precisa estar logado de qualquer jeito), então ele fica de fora.
a.datas = [d for d in a.datas if "_bundled" not in d[0].replace("\\", "/")]
a.binaries = [b for b in a.binaries if "_bundled" not in b[0].replace("\\", "/")]

pyz = PYZ(a.pure)

icon = str(ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon-256.png"))

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    console=False,
    icon=icon,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name=APP_NAME, upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(ROOT / "assets" / "icon.icns") if (ROOT / "assets" / "icon.icns").exists() else None,
        bundle_identifier="com.rorysystems.assistant",
        info_plist={"NSHighResolutionCapable": True},
    )
