import textwrap

import pytest

from kb.config import load_config
from kb.errors import ConfigError

SPEC_EXAMPLE = """
project: sop
repos:
  - name: sop-web
    url: git@github.com:acme/sop-fe.git
    path: packages/sop-web
    role: frontend
  - name: sop-backend
    url: git@github.com:acme/sop-backend.git
    role: backend
linkers:
  - type: graphql
    from: sop-web
    to: sop-backend
context_docs:
  - sop-backend:CONTEXT.md
  - sop-backend:docs/adr/**
ignore: ["**/*.svg", "**/__generated__/**"]
registry: https://kb.acme.internal
langfuse: http://localhost:3000
"""


def write(tmp_path, text):
    p = tmp_path / "kb.yaml"
    p.write_text(textwrap.dedent(text))
    return p


def test_loads_spec_example(tmp_path):
    cfg = load_config(write(tmp_path, SPEC_EXAMPLE))
    assert cfg.project == "sop"
    assert cfg.repos[0].role == "frontend"
    assert cfg.repos[0].path == "packages/sop-web"
    assert cfg.linkers[0].from_ == "sop-web"
    assert cfg.langfuse == "http://localhost:3000"


def test_repo_needs_exactly_one_source(tmp_path):
    p = write(tmp_path, """
    project: x
    repos:
      - name: a
        role: backend
        url: git@x:a.git
        local_path: ./a
    """)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "url" in str(e.value) and "local_path" in str(e.value)


def test_repo_with_no_source_rejected(tmp_path):
    p = write(tmp_path, """
    project: x
    repos:
      - name: a
        role: backend
    """)
    with pytest.raises(ConfigError):
        load_config(p)


def test_linker_refers_to_unknown_repo(tmp_path):
    p = write(tmp_path, """
    project: x
    repos:
      - name: a
        role: backend
        local_path: ./a
    linkers:
      - type: graphql
        from: nope
        to: a
    """)
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "nope" in str(e.value)


def test_local_path_resolved_relative_to_yaml(tmp_path):
    p = write(tmp_path, """
    project: x
    repos:
      - name: a
        role: backend
        local_path: ./code/a
    """)
    cfg = load_config(p)
    assert cfg.repos[0].local_path == (tmp_path / "code" / "a").resolve()


def test_context_doc_globs_split(tmp_path):
    cfg = load_config(write(tmp_path, SPEC_EXAMPLE))
    assert ("sop-backend", "docs/adr/**") in cfg.context_doc_globs()


def test_invalid_yaml_is_config_error(tmp_path):
    p = write(tmp_path, "project: [unclosed")
    with pytest.raises(ConfigError):
        load_config(p)


def test_sop_example_loads():
    from pathlib import Path

    cfg = load_config(Path(__file__).parent.parent / "examples" / "sop.kb.yaml")
    assert [r.name for r in cfg.repos] == ["sop-web", "sop-backend"]
    assert cfg.linkers[0].options["backend_style"] == "graphql-compose"
