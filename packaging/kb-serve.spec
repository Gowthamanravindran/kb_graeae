# PyInstaller spec for the packaged KB engine (one-folder). Build: bash packaging/build-sidecar.sh
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [], [], []
for pkg in ("tree_sitter_typescript", "graphify", "langgraph", "langchain_core", "claude_agent_sdk", "langfuse",
            "tiktoken", "networkx", "graphql"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:
        continue
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += collect_submodules("kb") + ["kb_registry.paths", "uvicorn.logging", "uvicorn.loops.auto",
                                              "uvicorn.protocols.http.auto", "uvicorn.lifespan.on"]

a = Analysis(["../src/kb/frozen.py"], pathex=["../src"], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=["kb_registry.api", "kb_registry.models", "sqlalchemy", "alembic", "boto3", "botocore",
                       "psycopg", "moto", "pytest", "tkinter"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="kb-serve", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="kb-serve")
