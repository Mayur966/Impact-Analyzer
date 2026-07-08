import ast
import os
import sys


def find_calls(func_node):
    calls = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append(node.func.id)
    return calls


def find_imports(tree):
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local_name = alias.asname or alias.name
                imports[local_name] = f"{node.module}.{alias.name}"
    return imports


def analyze_file(file_path):
    with open(file_path, "r") as f:
        source = f.read()

    tree = ast.parse(source, filename=file_path)

    imports = find_imports(tree)

    functions = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.name, node.lineno, find_calls(node)))

    return imports, functions


def resolve_call(name, module, imports, all_functions):
    if name in imports:
        candidate = imports[name]
    else:
        candidate = f"{module}.{name}"

    if candidate in all_functions:
        return candidate
    return None


def build_dependency_map(folder):
    # Phase 1: parse every .py file once, collecting its imports and functions.
    file_data = {}
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".py"):
            continue
        module = filename[:-3]  # strip the ".py"
        file_data[module] = analyze_file(os.path.join(folder, filename))

    # Collect the fully-qualified name of every function in the project.
    all_functions = set()
    for module, (imports, functions) in file_data.items():
        for name, lineno, calls in functions:
            all_functions.add(f"{module}.{name}")

    # Phase 2: resolve each raw call name to a fully-qualified function.
    dependency_map = {}
    for module, (imports, functions) in file_data.items():
        for name, lineno, calls in functions:
            qualified = f"{module}.{name}"
            resolved = []
            for called in calls:
                target = resolve_call(called, module, imports, all_functions)
                if target and target not in resolved:
                    resolved.append(target)
            dependency_map[qualified] = resolved

    return dependency_map


if __name__ == "__main__":
    folder = sys.argv[1] if len(sys.argv) > 1 else "sample_code"
    dependency_map = build_dependency_map(folder)

    print(f"Dependency map for {folder}/:")
    for func in sorted(dependency_map):
        callees = dependency_map[func]
        if callees:
            print(f"  {func} → calls {', '.join(callees)}")
        else:
            print(f"  {func} → calls nothing")
