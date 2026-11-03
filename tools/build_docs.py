#!/usr/bin/env python3
"""Generate source-indexed CLI and model-input references without importing models.

Run from any directory. --check reports stale/missing generated pages without
writing them. Python's standard library is sufficient; no jobs or models run.
"""

import argparse
import ast
from pathlib import Path
import re
import runpy
import sys


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/reference"
GROUPS = {
    "generation": ("Generation, setup, and shared helpers", [
        "models/*.py", "models/run-events", "nersc/*.py", "tools/*.py",
    ]),
    "cache": ("Cache construction, publication, and deletion", ["models/cache/*.py"]),
    "analysis": ("Analysis and statistical diagnostics", ["models/analysis/*.py"]),
    "inference": ("Standalone Bayesian inference", ["inference/*.py"]),
    "maintenance": ("Standalone maintenance", ["models/maintenance/*.py"]),
    "osg": ("OSG staging, submission, and draining", ["osg/*.py"]),
    "model-tools": ("Custom model helpers", [
        "models/music-hydro/*.py", "models/music-hydro/EOS/PCE/*.py",
        "models/music-hydro/utilities/*.py", "models/trento/*.py",
        "models/iS3D/*.py", "models/iS3D/scripts/*.py",
    ]),
}


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
    return "../../" + relative + ("#L" + str(line) if line else "")


def script_section(path):
    global CURRENT_SOURCE
    CURRENT_SOURCE = path.read_text()
    title = path.relative_to(ROOT).as_posix()
    lines = ["## " + title, "", "[Source](" + source_link(path) + ")", ""]
    try:
        tree = ast.parse(CURRENT_SOURCE)
    except SyntaxError as error:
        # Upstream MUSIC utilities include retained Python 2-era helpers.
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
    shared_inference = path.parent == ROOT / "inference" and any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "I"
        and node.func.attr in ("cli", "add_input_arguments", "add_parameter_arguments", "add_plot_arguments")
        for node in ast.walk(tree)
    )
    if shared_inference:
        lines += ["Uses shared options declared in [inference.py](#inferenceinferencepy). "
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


def cli_pages():
    for group, (title, patterns) in GROUPS.items():
        if group == "inference":
            # Inference owns its documentation and can regenerate it without the
            # surrounding repository. Keep the handbook's old URL as a pointer.
            inference_docs = runpy.run_path(str(ROOT / "inference/build_docs.py"))
            yield inference_docs["OUT"], inference_docs["reference_page"]()
            yield OUT / "cli-inference.md", (
                "# Standalone inference command reference\n\n"
                "The maintained [inference command and flag reference](../../inference/CLI.md) "
                "lives with the [inference guide](../../inference/README.md). "
                "Regenerate it with `python inference/build_docs.py`, or run "
                "`python inference/build_docs.py --check` to check it without writing.\n"
            )
            continue
        paths = sorted({p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()})
        lines = ["# " + title + ": source argument index", "",
                 "[Command guide](commands.md) · [Handbook](../README.md)", "",
                 "Generated by `python3 tools/build_docs.py`; edit the source help or the guide, "
                 "then regenerate. Tables list argument **declarations**, including shared "
                 "helpers, hidden options, and all subcommands together. An option in a table "
                 "does not necessarily belong to every subcommand. Use `COMMAND SUBCOMMAND --help` "
                 "for accepted combinations. Dynamic expressions are shown unevaluated.", "",
                 "Parser context names the subcommand when directly declared, otherwise "
                 "the source parser variable and helper function. Shared helpers/loops can "
                 "supply the same option to multiple commands.", "",
                 "`default=None` can mean a later INI/environment fallback; it is not the "
                 "resolved campaign default. Required values may be supplied by the shared "
                 "INI where supported. Missing `type` normally means text; `store_true` and "
                 "`store_false` are Boolean switches; `nargs` controls value count.", "",
                 "The standard `-h, --help` is implicit in argparse. Options added in loops "
                 "are shown once as their source declaration. See shared-helper entries in "
                 "[generation](cli-generation.md) for status/config arguments used by other tools.", "",
                 "## Contents", ""]
        for path in paths:
            relative = path.relative_to(ROOT).as_posix()
            anchor = re.sub(r"[^\w\- ]", "", relative.lower()).replace(" ", "-")
            lines.append("- [" + relative + "](#" + anchor + ")")
        lines.append("")
        for path in paths:
            lines.extend(script_section(path))
        yield OUT / ("cli-" + group + ".md"), "\n".join(lines)


def input_page():
    lines = ["# Model input defaults in this checkout", "",
             "[Physics guide](physics.md) · [Configuration](configuration.md)", "",
             "Generated by `python3 tools/build_docs.py` from active entries in the "
             "MUSIC template and iS3D parameter file. Values are **template values**; "
             "rendering, custom EOS logic, runtime overrides, and separately supplied "
             "production configurations can supersede them. `{}` is filled from "
             "`parameters.dat`, not a usable numeric value. Inline notes come from "
             "the input files; read adjacent source comments for enumerated modes.", ""]
    for relative in ("config/initialization/music_input_template", "models/iS3D/iS3D_parameters.dat"):
        path = ROOT / relative
        lines += ["## " + relative, "", "| Key | Template value | Inline note | Source |", "|---|---|---|---|"]
        for n, raw in enumerate(path.read_text().splitlines(), 1):
            text, _, comment = raw.partition("#")
            text = text.strip()
            if not text or text == "EndOfData":
                continue
            match = re.match(r"(\w+)\s*(?:=\s*|\s+)(.+)", text)
            if match:
                name, value = match.groups()
                lines.append("| " + code(name) + " | " + code(value) + " | " + cell(comment)
                             + " | [line " + str(n) + "](" + source_link(path, n) + ") |")
        lines.append("")
    return OUT / "model-input-defaults.md", "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report stale pages; write nothing.")
    args = parser.parse_args()
    pages = list(cli_pages()) + [input_page()]
    stale = []
    for path, contents in pages:
        if path.is_file() and path.read_text() == contents:
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
    if args.check and stale:
        for path in stale:
            print("STALE:", path, file=sys.stderr)
        return 1
    print("Checked" if args.check else "Generated", len(pages), "reference pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
