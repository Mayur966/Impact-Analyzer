import ast
import difflib
import os
import sys


def get_function_sources(folder):
    """Map every qualified function name to its exact source code, for one folder."""
    sources = {}
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".py"):
            continue
        module = filename[:-3]
        path = os.path.join(folder, filename)
        with open(path, "r") as f:
            text = f.read()
        tree = ast.parse(text, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sources[f"{module}.{node.name}"] = ast.get_source_segment(text, node)
    return sources


def diff_functions(old_folder, new_folder):
    old = get_function_sources(old_folder)
    new = get_function_sources(new_folder)

    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(name for name in set(old) & set(new) if old[name] != new[name])

    diffs = {}
    for name in modified:
        diff = difflib.unified_diff(
            old[name].splitlines(),
            new[name].splitlines(),
            fromfile=f"{old_folder}/{name}",
            tofile=f"{new_folder}/{name}",
            lineterm="",
        )
        diffs[name] = "\n".join(diff)

    return added, removed, modified, diffs


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python diff_functions.py <old_folder> <new_folder>")
        sys.exit(1)

    old_folder, new_folder = sys.argv[1], sys.argv[2]
    added, removed, modified, diffs = diff_functions(old_folder, new_folder)

    print(f"Comparing {old_folder}/ -> {new_folder}/\n")

    print(f"Added ({len(added)}):")
    for name in added:
        print(f"  + {name}")

    print(f"\nRemoved ({len(removed)}):")
    for name in removed:
        print(f"  - {name}")

    print(f"\nModified ({len(modified)}):")
    for name in modified:
        print(f"  ~ {name}")

    for name in modified:
        print(f"\n--- diff: {name} ---")
        print(diffs[name])
