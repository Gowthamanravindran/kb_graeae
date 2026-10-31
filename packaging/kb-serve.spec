# PyInstaller spec for the packaged Graeae engine (one-folder). Build: bash packaging/build-sidecar.sh
import importlib.metadata

from PyInstaller.utils.hooks import collect_all, collect_submodules

# Graphify loads each language's parser by name at run time (`tree_sitter_ruby`, `tree_sitter_python`, …), which
# PyInstaller can't see, so every installed tree-sitter grammar is bundled explicitly.
grammars = sorted({d.metadata["Name"].replace("-", "_") for d in importlib.metadata.distributions()
                   if d.metadata["Name"] and d.metadata["Name"].lower().startswith("tree-sitter-")})

datas, binaries, hiddenimports = [], [], []
for pkg in (*grammars, "graphify", "langgraph", "langchain_core", "claude_agent_sdk", "langfuse",
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
