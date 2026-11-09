"""Syntax check for ui/*.js without Node (tree-sitter). Exit code 1 on error.

Needs: .venv\\Scripts\\python.exe -m pip install tree-sitter tree-sitter-javascript
"""
import pathlib
import sys

import tree_sitter as ts
import tree_sitter_javascript as tj

parser = ts.Parser(ts.Language(tj.language()))
root = pathlib.Path(__file__).resolve().parent.parent / "ui"
bad = 0


def walk(node, src, name):
    global bad
    if node.type == "ERROR" or node.is_missing:
        bad += 1
        line = node.start_point[0] + 1
        snippet = src[node.start_byte:node.end_byte][:80].decode("utf-8", "replace")
        print(f"{name}:{line}: {node.type} {snippet!r}")
    for child in node.children:
        walk(child, src, name)


for f in sorted(root.glob("*.js")):
    src = f.read_bytes()
    walk(parser.parse(src).root_node, src, f.name)

print("ok" if not bad else f"{bad} error(s)")
sys.exit(1 if bad else 0)
