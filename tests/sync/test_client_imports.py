import subprocess
import sys


def test_client_needs_no_server_dependencies():
    code = ("import sys; sys.modules['sqlalchemy'] = None; sys.modules['fastapi'] = None; sys.modules['boto3'] = None\n"
            "import kb.sync.commands, kb.sync.client, kb.sync.pack_io, kb.cli\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
