import sys
from collections import deque

from detect_functions import build_dependency_map


def build_reverse_map(dependency_map):
    # Flip "X calls Y" into "Y is called by X".
    reverse = {func: [] for func in dependency_map}
    for func, callees in dependency_map.items():
        for callee in callees:
            reverse[callee].append(func)
    return reverse


def short_name(qualified):
    return qualified.split(".")[-1]


def find_blast_radius(target, reverse_map):
    # BFS outward through callers. Depth 1 = direct, deeper = indirect.
    results = []
    visited = {target}
    queue = deque()
    for caller in reverse_map.get(target, []):
        visited.add(caller)
        queue.append((caller, target, 1))

    while queue:
        node, via, depth = queue.popleft()
        relation = "direct" if depth == 1 else f"indirect via {short_name(via)}"
        results.append((node, relation))
        for caller in reverse_map.get(node, []):
            if caller not in visited:
                visited.add(caller)
                queue.append((caller, node, depth + 1))

    return results


def resolve_target(name, dependency_map):
    if name in dependency_map:
        return [name]
    return [f for f in dependency_map if short_name(f) == name]


if __name__ == "__main__":
    target_name = sys.argv[1]
    folder = sys.argv[2] if len(sys.argv) > 2 else "sample_code"

    dependency_map = build_dependency_map(folder)
    reverse_map = build_reverse_map(dependency_map)

    targets = resolve_target(target_name, dependency_map)
    if not targets:
        print(f"No function named '{target_name}' found in {folder}/")
        sys.exit(1)

    for target in targets:
        affected = find_blast_radius(target, reverse_map)
        if affected:
            parts = [f"{short_name(node)} ({relation})" for node, relation in affected]
            print(f"Changing {short_name(target)} affects: {', '.join(parts)}")
        else:
            print(f"Changing {short_name(target)} affects: nothing")
