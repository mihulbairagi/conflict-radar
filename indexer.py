"""indexer.py - Codebase Call Graph Indexer for Conflict Radar.

Walks a directory of Python files, extracts functions, classes, and calls
using the standard library `ast` module, and stores them in FalkorDB.
All database writes use MERGE to guarantee idempotent, duplicate-free re-runs.
"""

import ast
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from falkordb import FalkorDB


def connect_graph(host: str = os.environ.get("FALKORDB_HOST", "127.0.0.1"), port: int = 6379, graph_name: str = "conflict_radar"):
    """Connect to the FalkorDB instance and select the graph."""
    client = FalkorDB(host=host, port=port)
    return client.select_graph(graph_name)


def is_overload_stub(node: ast.AST) -> bool:
    """Return True if node has an @overload or @typing.overload decorator."""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "overload":
            return True
        elif isinstance(dec, ast.Attribute) and dec.attr == "overload":
            return True
    return False


def find_method_in_class_hierarchy(
    rel_file: str,
    class_name: str,
    method_name: str,
    class_methods: Dict[str, Dict[str, Set[str]]],
    class_bases: Dict[str, Dict[str, List[str]]],
    visited: Optional[Set[str]] = None,
) -> Optional[str]:
    """Look up method_name in class_name or its base classes defined in the same file."""
    if visited is None:
        visited = set()
    if class_name in visited:
        return None
    visited.add(class_name)

    # 1. Check if method_name is defined directly in this class
    methods = class_methods.get(rel_file, {}).get(class_name, set())
    if method_name in methods:
        return f"{rel_file}::{class_name}.{method_name}"

    # 2. Check base classes defined in the same file recursively
    bases = class_bases.get(rel_file, {}).get(class_name, [])
    for base in bases:
        if base in class_methods.get(rel_file, {}):
            target = find_method_in_class_hierarchy(
                rel_file, base, method_name, class_methods, class_bases, visited
            )
            if target:
                return target

    return None


def resolve_call(
    call: ast.Call,
    current_file: str,
    import_map: Dict[str, str],
    module_alias_map: Dict[str, str],
    file_symbols: Dict[str, Set[str]],
    current_class: Optional[str] = None,
    class_methods: Optional[Dict[str, Dict[str, Set[str]]]] = None,
    class_bases: Optional[Dict[str, Dict[str, List[str]]]] = None,
) -> Optional[str]:
    """Resolve an ast.Call to a target symbol ID."""
    func = call.func

    # 1. Direct function or class call: func(...)
    if isinstance(func, ast.Name):
        name = func.id
        if name in import_map:
            return import_map[name]
        if name in file_symbols.get(current_file, set()):
            return f"{current_file}::{name}"
        return None

    # 2. Attribute calls: target.method(...)
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        target_name = func.value.id
        attr_name = func.attr

        # 2a. self.X(...) or cls.X(...) inside a method
        if target_name in ("self", "cls") and current_class and class_methods and class_bases:
            return find_method_in_class_hierarchy(
                current_file, current_class, attr_name, class_methods, class_bases
            )

        # 2b. alias.func(...) or alias.Class(...) where alias is an imported module
        if target_name in module_alias_map:
            target_file = module_alias_map[target_name]
            if attr_name in file_symbols.get(target_file, set()):
                return f"{target_file}::{attr_name}"
            return None

        # 2c. Calls on arbitrary variables (adapter.send, req.url.decode, etc.) remain unresolved
        return None

    return None


def index_codebase(target_dir: str, graph=None) -> Dict[str, int]:
    """Parse all Python files in target_dir and write the call graph to FalkorDB."""
    root_path = Path(target_dir).resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"Target directory '{target_dir}' does not exist.")

    # Collect Python files, skipping virtualenvs and cache
    py_files: List[Path] = []
    for root, dirs, files in os.walk(root_path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("venv", ".venv", "__pycache__", "build", "dist")]
        for file in files:
            if file.endswith(".py"):
                py_files.append(Path(root) / file)

    files_data: List[str] = []
    symbols_data: List[Dict[str, str]] = []  # {id, name, file, kind}
    contains_edges: List[Tuple[str, str]] = []  # (file_path, symbol_id)
    file_symbols: Dict[str, Set[str]] = {}  # file_path -> set of local symbol names
    module_to_file: Dict[str, str] = {}  # module dot path -> relative file path
    class_methods: Dict[str, Dict[str, Set[str]]] = {}  # file_path -> {class_name: set of method names}
    class_bases: Dict[str, Dict[str, List[str]]] = {}  # file_path -> {class_name: list of base names}

    # Pass 1: Discover files, classes, and functions (skipping @overload stubs)
    for file_path in py_files:
        try:
            rel_file = file_path.relative_to(root_path).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        files_data.append(rel_file)
        file_symbols[rel_file] = set()
        class_methods[rel_file] = {}
        class_bases[rel_file] = {}

        # Register module mappings for import resolution
        mod_name = rel_file.removesuffix(".py").replace("/", ".")
        module_to_file[mod_name] = rel_file
        short_stem = file_path.stem
        if short_stem not in module_to_file:
            module_to_file[short_stem] = rel_file

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(file_path))
        except Exception as e:
            print(f"[Warning] Failed to parse {rel_file}: {e}")
            continue

        for node in ast.iter_child_nodes(tree):
            # Top-level functions
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_overload_stub(node):
                    continue
                sym_id = f"{rel_file}::{node.name}"
                symbols_data.append({"id": sym_id, "name": node.name, "file": rel_file, "kind": "function"})
                contains_edges.append((rel_file, sym_id))
                file_symbols[rel_file].add(node.name)

            # Classes and methods
            elif isinstance(node, ast.ClassDef):
                class_id = f"{rel_file}::{node.name}"
                symbols_data.append({"id": class_id, "name": node.name, "file": rel_file, "kind": "class"})
                contains_edges.append((rel_file, class_id))
                file_symbols[rel_file].add(node.name)

                class_methods[rel_file][node.name] = set()
                class_bases[rel_file][node.name] = [
                    b.id for b in node.bases if isinstance(b, ast.Name)
                ]

                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if is_overload_stub(item):
                            continue
                        method_id = f"{rel_file}::{node.name}.{item.name}"
                        symbols_data.append({"id": method_id, "name": f"{node.name}.{item.name}", "file": rel_file, "kind": "function"})
                        contains_edges.append((rel_file, method_id))
                        file_symbols[rel_file].add(f"{node.name}.{item.name}")
                        class_methods[rel_file][node.name].add(item.name)

    # Pass 2: Discover and resolve function calls
    call_edges: List[Tuple[str, str]] = []  # (caller_symbol_id, callee_symbol_id)

    for file_path in py_files:
        try:
            rel_file = file_path.relative_to(root_path).as_posix()
        except ValueError:
            rel_file = file_path.as_posix()

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=str(file_path))
        except Exception:
            continue

        # Map imported names to symbol IDs or module targets
        import_map: Dict[str, str] = {}
        module_alias_map: Dict[str, str] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    as_name = alias.asname or alias.name
                    target_file = module_to_file.get(alias.name) or module_to_file.get(alias.name.split(".")[-1])
                    if target_file:
                        module_alias_map[as_name] = target_file
            elif isinstance(node, ast.ImportFrom):
                mod_str = node.module or ""
                target_mod_file = module_to_file.get(mod_str) or module_to_file.get(mod_str.split(".")[-1])

                for alias in node.names:
                    as_name = alias.asname or alias.name
                    submod_key = f"{mod_str}.{alias.name}" if mod_str else alias.name
                    if submod_key in module_to_file:
                        module_alias_map[as_name] = module_to_file[submod_key]
                    elif alias.name in module_to_file:
                        module_alias_map[as_name] = module_to_file[alias.name]
                    elif target_mod_file:
                        import_map[as_name] = f"{target_mod_file}::{alias.name}"

        # Helper to inspect calls inside a function or method body
        def collect_calls(caller_id: str, body_nodes: List[ast.AST], current_class: Optional[str] = None):
            for sub_node in body_nodes:
                for call_node in ast.walk(sub_node):
                    if isinstance(call_node, ast.Call):
                        callee_id = resolve_call(
                            call_node,
                            rel_file,
                            import_map,
                            module_alias_map,
                            file_symbols,
                            current_class=current_class,
                            class_methods=class_methods,
                            class_bases=class_bases,
                        )
                        if callee_id:
                            call_edges.append((caller_id, callee_id))

        # Inspect top-level functions and class methods
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if is_overload_stub(node):
                    continue
                caller_id = f"{rel_file}::{node.name}"
                collect_calls(caller_id, node.body, current_class=None)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if is_overload_stub(item):
                            continue
                        method_id = f"{rel_file}::{node.name}.{item.name}"
                        collect_calls(method_id, item.body, current_class=node.name)

    call_edges = list(set(call_edges))

    # Write to FalkorDB if graph is provided
    if graph is not None:
        try:
            graph.query("CREATE CONSTRAINT FOR (s:Symbol) REQUIRE s.id IS UNIQUE")
            graph.query("CREATE CONSTRAINT FOR (f:File) REQUIRE f.path IS UNIQUE")
        except Exception:
            pass  # Constraint may already exist

        # 1. Merge File nodes
        for f_path in files_data:
            graph.query("MERGE (:File {path: $path})", {"path": f_path})

        # 2. Merge Symbol nodes
        for sym in symbols_data:
            graph.query(
                "MERGE (:Symbol {id: $id, name: $name, file: $file, kind: $kind})",
                sym
            )

        # 3. Merge (File)-[:CONTAINS]->(Symbol) edges
        for f_path, sym_id in contains_edges:
            graph.query(
                "MATCH (f:File {path: $file}), (s:Symbol {id: $sym}) "
                "MERGE (f)-[:CONTAINS]->(s)",
                {"file": f_path, "sym": sym_id}
            )

        # 4. Merge (Symbol)-[:CALLS]->(Symbol) edges
        for caller_id, callee_id in call_edges:
            graph.query(
                "MATCH (a:Symbol {id: $caller}), (b:Symbol {id: $callee}) "
                "MERGE (a)-[:CALLS]->(b)",
                {"caller": caller_id, "callee": callee_id}
            )

    return {
        "files_count": len(files_data),
        "symbols_count": len(symbols_data),
        "contains_count": len(contains_edges),
        "calls_count": len(call_edges)
    }


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    host = os.environ.get("FALKORDB_HOST", "127.0.0.1")
    print(f"Connecting to FalkorDB on {host}:6379...")
    try:
        g = connect_graph()
        stats = index_codebase(target, graph=g)
        print(f"Successfully indexed '{target}':")
        print(f"  Files:    {stats['files_count']}")
        print(f"  Symbols:  {stats['symbols_count']}")
        print(f"  Contains: {stats['contains_count']}")
        print(f"  Calls:    {stats['calls_count']}")
    except Exception as err:
        print(f"Failed to connect or index: {err}")
        print("Ensure FalkorDB is running: docker compose up -d")
