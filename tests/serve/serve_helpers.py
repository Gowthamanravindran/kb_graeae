from agent_helpers import FIX

TOKEN = "t" * 64


def write_cfg(tmp_path, registry=None, name="mini"):
    y = tmp_path / f"{name}.kb.yaml"
    lines = [f"project: {name}", "repos:"]
    for r in ("mini-fe", "mini-be"):
        lines.append(f"  - {{name: {r}, role: {'frontend' if r == 'mini-fe' else 'backend'}, local_path: {FIX / r}}}")
    if registry:
        lines.append(f"registry: {registry}")
    y.write_text("\n".join(lines) + "\n")
    return y


