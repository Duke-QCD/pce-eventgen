#!/usr/bin/env python3
"""Generate the local inference CLI reference without importing inference tools.

Run `python build_docs.py --check` to report stale documentation without writing.
Only Python's standard library is required; no repository-level tools are used.
"""

import argparse
import ast
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "CLI.md"


def expression(node):
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        # Keep dynamic defaults as source, not an invented evaluated value.
        return ast.get_source_segment(CURRENT_SOURCE, node) or "(dynamic expression)"
    if isinstance(value, set):
        return "{" + ", ".join(sorted(repr(item) for item in value)) + "}"
    return str(value) if isinstance(value, str) else repr(value)


def cell(value):
    return " ".join(str(value).split()).replace("|", "&#124;").replace("<", "&lt;").replace(">", "&gt;")


def code(value):
    # HTML code avoids Markdown delimiter collisions inside source expressions.
    return "<code>" + cell(value).replace("`", "&#96;") + "</code>"


def source_link(path, line=None):
    relative = path.relative_to(ROOT).as_posix()
    return relative + ("#L" + str(line) if line else "")


def script_section(path):
    global CURRENT_SOURCE
    CURRENT_SOURCE = path.read_text()
    title = path.relative_to(ROOT).as_posix()
    lines = ["## " + title, "", "[Source](" + source_link(path) + ")", ""]
    try:
        tree = ast.parse(CURRENT_SOURCE)
    except SyntaxError as error:
        # Report the source problem without importing an invalid module.
        lines += ["This retained helper cannot be parsed as Python 3 (line "
                  + str(error.lineno) + "). It is outside the maintained Python 3 "
                  "workflow. Read the source and port/review it before use; no "
                  "argument table is inferred for it.", ""]
        return lines
    doc = ast.get_docstring(tree)
    if doc:
        # A short source-authored summary; the full introduction remains linked.
        paragraph = doc.strip().split("\n\n", 1)[0]
        lines += [cell(paragraph), ""]
    subcommands = []
    arguments = []
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def function_name(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "module"

    parser_names = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "add_parser" and node.value.args):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    parser_names[function_name(node), target.id] = expression(node.value.args[0])
    for node in sorted(ast.walk(tree), key=lambda n: getattr(n, "lineno", 0)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "add_parser" and node.args:
            kw = {k.arg: k.value for k in node.keywords}
            subcommands.append((expression(node.args[0]), expression(kw["help"]) if "help" in kw else ""))
        if node.func.attr != "add_argument" or not node.args:
            continue
        names = ", ".join(expression(arg) for arg in node.args)
        kw = {k.arg: k.value for k in node.keywords}
        declaration = "; ".join(key + "=" + expression(kw[key]) for key in (
            "type", "choices", "default", "action", "nargs", "const", "required", "dest", "metavar"
        ) if key in kw)
        help_text = expression(kw["help"]) if "help" in kw else "No help text declared; consult linked source."
        if help_text == "argparse.SUPPRESS":
            help_text = "Hidden from CLI help; see source and compatibility notes."
        receiver = ast.get_source_segment(CURRENT_SOURCE, node.func.value) or "parser"
        scope = function_name(node)
        context = parser_names.get((scope, receiver), receiver)
        if scope not in ("main", "module"):
            context += " in " + scope + "()"
        arguments.append((names, context, declaration or "argparse defaults", help_text, node.lineno))
    if subcommands:
        lines += ["Declared subcommands: " + "; ".join(code(name) + (" — " + cell(help_text) if help_text else "")
                                                            for name, help_text in subcommands) + ".", ""]
    shared_inference = path.parent == ROOT and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "I"
        and node.func.attr in ("cli", "add_input_arguments", "add_parameter_arguments", "add_plot_arguments")
        for node in ast.walk(tree)
    )
    if shared_inference:
        lines += ["Uses shared options declared in [inference.py](#inferencepy). "
                  "Run this script with `--help` for the expanded interface and launcher defaults.", ""]
    if arguments:
        lines += ["| Argument / flag | Parser context | Declaration | Meaning | Source |",
                  "|---|---|---|---|---|"]
        for names, context, declaration, help_text, line in arguments:
            lines.append("| " + code(names) + " | " + code(context) + " | " + code(declaration) + " | " + cell(help_text)
                         + " | [line " + str(line) + "](" + source_link(path, line) + ") |")
        lines.append("")
    elif not shared_inference:
        lines += ["No `argparse.add_argument` declarations found. This may be a library, "
                  "a script with fixed paths, or a tool using another argument parser. "
                  "Read its source before running it; do not assume `--help` prevents work.", ""]
    return lines


def reference_page():
    paths = sorted(ROOT.glob("*.py"))
    lines = ["# Inference command and flag reference", "",
             "[Inference guide](README.md) · [Design generation](DESIGN.md) · [Input contract](INPUTS.md)", "",
             "Generated by `python build_docs.py`; edit source help, then regenerate. "
             "This index reads declarations without importing tools. Tables include "
             "shared helper options and all subcommands together; run `python SCRIPT.py --help` "
             "for the accepted interface and launcher-specific defaults. Dynamic defaults "
             "are shown as source expressions. The standard `-h, --help` is implicit.", "",
             "## Contents", ""]
    for path in paths:
        anchor = re.sub(r"[^\w\- ]", "", path.name.lower()).replace(" ", "-")
        lines.append(f"- [{path.name}](#{anchor})")
    lines.append("")
    for path in paths:
        lines.extend(script_section(path))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report a stale/missing CLI.md; write nothing.")
    args = parser.parse_args(argv)
    contents = reference_page()
    if OUT.is_file() and OUT.read_text() == contents:
        print("Inference CLI reference is current.")
        return 0
    if args.check:
        print("STALE: CLI.md; run python build_docs.py", file=sys.stderr)
        return 1
    OUT.write_text(contents)
    print("Generated CLI.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
