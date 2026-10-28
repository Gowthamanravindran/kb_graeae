from kb.graph.model import CodeEdge, CodeGraph, CodeNode

NODE_FIELDS = set(CodeNode.model_fields)
EDGE_FIELDS = set(CodeEdge.model_fields)


def sanitize_graph(g: CodeGraph) -> dict:
    """Only the declared CodeNode/CodeEdge fields: ids, names, paths, lines. Never source text."""
    d = g.to_json()
    return {
        "nodes": [{k: v for k, v in n.items() if k in NODE_FIELDS} for n in d["nodes"]],
        "edges": [{k: v for k, v in e.items() if k in EDGE_FIELDS} for e in d["edges"]],
    }
