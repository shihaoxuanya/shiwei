from PyInstaller.utils.hooks import collect_all, collect_submodules


datas = []
binaries = []
hiddenimports = collect_submodules("shiwei_ai")

for package in (
    "jieba",
    "docling",
    "docling_core",
    "lancedb",
    "onnxruntime",
    "pyarrow",
    "rapidocr",
    "transformers",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

analysis = Analysis(
    ["shiwei_ai/worker/main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="shiwei-ai-worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Keep JSONL stdio on both platforms (never --windowed / pythonw). Windows
    # Rust uses CREATE_NO_WINDOW; macOS starts the bundled binary with pipes.
    console=True,
    contents_directory="_internal",
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="shiwei-ai-worker",
)
