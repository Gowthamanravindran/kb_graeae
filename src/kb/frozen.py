"""Entry point of the packaged engine (PyInstaller): `kb-serve [serve options]` or
`kb-serve --run-graphify <graphify args>` (so the app needs no separate Graphify install)."""

from __future__ import annotations

import sys


def _run_kb(args: list[str]) -> int:
    from kb.cli import app

    try:
        app(args, standalone_mode=False)
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--run-graphify"]:
        import graphify.__main__ as gm

        sys.argv = ["graphify", *argv[1:]]
        return int(gm.main() or 0)
    return _run_kb(["serve", *argv])


if __name__ == "__main__":
    sys.exit(main())
