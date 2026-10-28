import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest


def _pg_bin() -> Path | None:
    found = shutil.which("initdb")
    if found:
        return Path(found).parent
    for d in sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True):
        if (d / "initdb").exists():
            return d
    return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def postgres_url():
    """A throwaway Postgres cluster, or skip if the binaries are not installed."""
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip("postgres binaries not installed")
    root = os.geteuid() == 0
    if root and shutil.which("runuser") is None:
        pytest.skip("running as root without runuser")
    data = Path(tempfile.mkdtemp(prefix="kbpg-"))
    if root:
        shutil.chown(data, "postgres")

    def run(*args):
        cmd = ["runuser", "-u", "postgres", "--", *args] if root else list(args)
        subprocess.run(cmd, check=True, capture_output=True)

    port = _free_port()
    run(str(bindir / "initdb"), "-D", str(data / "db"), "-A", "trust", "-U", "kb")
    run(str(bindir / "pg_ctl"), "-D", str(data / "db"), "-o", f"-p {port} -k {data} -c listen_addresses=127.0.0.1",
        "-l", str(data / "log"), "-w", "start")
    try:
        yield f"postgresql+psycopg://kb@127.0.0.1:{port}/postgres"
    finally:
        run(str(bindir / "pg_ctl"), "-D", str(data / "db"), "-m", "immediate", "stop")
        shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(params=["sqlite", "postgres"])
def db_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'reg.db'}"
    url = request.getfixturevalue("postgres_url")
    from sqlalchemy import create_engine, text

    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    name = f"t{int(time.time() * 1e6)}"
    with eng.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    eng.dispose()
    return url.rsplit("/", 1)[0] + f"/{name}"
