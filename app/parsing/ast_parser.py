"""
Code Understanding — Python AST parsing.

Parses a single Python source file into a structured representation:
imports, top-level functions, classes (with their methods), function
signatures, docstrings, decorators, outgoing calls, and line ranges.

This is deliberately Python-only for now. Other languages will be added
via Tree-sitter later; anything that consumes ParsedModule should treat
`language` as the switch point for that future work.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ImportInfo:
    """A single import statement."""

    statement: str          # reconstructed source, e.g. "from os.path import join as j"
    module: str | None      # target module, e.g. "os.path"; None for bare "import x"
    names: list[str]        # imported symbol names ([] for "import module")
    line: int


@dataclass
class FunctionInfo:
    """A function or method definition."""

    name: str
    qualified_name: str     # "ClassName.method" for methods, else just "name"
    signature: str          # reconstructed def line, e.g. "def foo(x: int) -> str"
    args: list[str]
    decorators: list[str]
    docstring: str | None
    start_line: int
    end_line: int
    calls: list[str]        # names/attribute-chains of things this function calls
    is_method: bool
    parent_class: str | None
    is_async: bool = False


@dataclass
class ClassInfo:
    """A class definition."""

    name: str
    bases: list[str]
    decorators: list[str]
    docstring: str | None
    start_line: int
    end_line: int
    method_names: list[str] = field(default_factory=list)  # qualified names of its methods


@dataclass
class ParsedModule:
    """Structured contents of one parsed Python file."""

    file_path: str
    module_docstring: str | None
    imports: list[ImportInfo]
    functions: list[FunctionInfo]  # flattened: top-level functions AND methods
    classes: list[ClassInfo]
    syntax_error: str | None = None  # set if the file could not be parsed


def _extract_call_name(node: ast.Call) -> str | None:
    """Best-effort reconstruction of the name being called, e.g. `foo`,
    `self.bar`, `module.submodule.baz`. Returns None if it's not a
    simple name/attribute chain (e.g. a call on a call result).
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        cur = func.value
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
        return func.attr  # fall back to just the final attribute name
    return None


def _collect_calls(node: ast.AST) -> list[str]:
    """Collect every call expression inside `node`'s subtree, but do not
    descend into nested function/class definitions (those get their own
    entries when visited separately).
    """
    calls: list[str] = []

    class CallCollector(ast.NodeVisitor):
        def visit_Call(self, call_node: ast.Call) -> None:
            name = _extract_call_name(call_node)
            if name:
                calls.append(name)
            self.generic_visit(call_node)

        def visit_FunctionDef(self, _: ast.FunctionDef) -> None:
            return  # don't recurse into nested defs

        def visit_AsyncFunctionDef(self, _: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, _: ast.ClassDef) -> None:
            return

    collector = CallCollector()
    for child in ast.iter_child_nodes(node):
        collector.visit(child)

    return calls


def _function_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = node.args
    names = [a.arg for a in args.posonlyargs]
    names += [a.arg for a in args.args]
    if args.vararg:
        names.append("*" + args.vararg.arg)
    names += [a.arg for a in args.kwonlyargs]
    if args.kwarg:
        names.append("**" + args.kwarg.arg)
    return names


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    try:
        args_src = ast.unparse(node.args)
    except Exception:
        args_src = ", ".join(_function_args(node))
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({args_src}){returns}"


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[str]:
    out = []
    for d in node.decorator_list:
        try:
            out.append("@" + ast.unparse(d))
        except Exception:
            out.append("@<decorator>")
    return out


def _end_line(node: ast.AST) -> int:
    # end_lineno is available on Python 3.8+; fall back defensively.
    return getattr(node, "end_lineno", getattr(node, "lineno", 0))


def _parse_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parent_class: str | None,
) -> FunctionInfo:
    qualified_name = f"{parent_class}.{node.name}" if parent_class else node.name
    return FunctionInfo(
        name=node.name,
        qualified_name=qualified_name,
        signature=_signature(node),
        args=_function_args(node),
        decorators=_decorators(node),
        docstring=ast.get_docstring(node),
        start_line=node.lineno,
        end_line=_end_line(node),
        calls=sorted(set(_collect_calls(node))),
        is_method=parent_class is not None,
        parent_class=parent_class,
        is_async=isinstance(node, ast.AsyncFunctionDef),
    )


def _parse_import(node: ast.Import | ast.ImportFrom) -> list[ImportInfo]:
    try:
        statement = ast.unparse(node)
    except Exception:
        statement = ""

    if isinstance(node, ast.Import):
        return [
            ImportInfo(statement=statement, module=alias.name, names=[], line=node.lineno)
            for alias in node.names
        ]

    # ast.ImportFrom
    module = ("." * node.level) + (node.module or "")
    return [
        ImportInfo(
            statement=statement,
            module=module,
            names=[alias.name for alias in node.names],
            line=node.lineno,
        )
    ]


def parse_source(source: str, file_path: str) -> ParsedModule:
    """Parse Python source text into a ParsedModule.

    Never raises on malformed source: syntax errors are captured in
    `ParsedModule.syntax_error` and empty lists are returned for the rest,
    so callers can skip/flag the file without crashing an indexing run.
    """
    try:
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError) as e:
        logger.warning("Syntax error parsing %s: %s", file_path, e)
        return ParsedModule(
            file_path=file_path,
            module_docstring=None,
            imports=[],
            functions=[],
            classes=[],
            syntax_error=str(e),
        )

    imports: list[ImportInfo] = []
    functions: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.extend(_parse_import(node))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_parse_function(node, parent_class=None))

        elif isinstance(node, ast.ClassDef):
            bases = []
            for b in node.bases:
                try:
                    bases.append(ast.unparse(b))
                except Exception:
                    bases.append("<base>")

            method_names: list[str] = []
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    fn = _parse_function(child, parent_class=node.name)
                    functions.append(fn)
                    method_names.append(fn.qualified_name)

            classes.append(
                ClassInfo(
                    name=node.name,
                    bases=bases,
                    decorators=_decorators(node),
                    docstring=ast.get_docstring(node),
                    start_line=node.lineno,
                    end_line=_end_line(node),
                    method_names=method_names,
                )
            )

    return ParsedModule(
        file_path=file_path,
        module_docstring=ast.get_docstring(tree),
        imports=imports,
        functions=functions,
        classes=classes,
        syntax_error=None,
    )


def parse_python_file(path: str | Path) -> ParsedModule:
    """Read and parse a Python file from disk."""
    path = Path(path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("Could not read %s: %s", path, e)
        return ParsedModule(
            file_path=str(path),
            module_docstring=None,
            imports=[],
            functions=[],
            classes=[],
            syntax_error=str(e),
        )
    return parse_source(source, str(path))