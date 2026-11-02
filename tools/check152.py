#!/usr/bin/env python3
"""Check Bridge Commander scripts for Python 1.5.2 incompatibilities.

BC runs Python 1.5.2. `python -m py_compile` CANNOT catch these rules - modern
Python happily accepts e.g. `continue` inside a `try` body, which then dies at
import time in-game with "SyntaxError: 'continue' not properly in loop".

Usage:  python check152.py <file.py> [more.py ...]
Exit:   0 = clean, 1 = problems found, 2 = usage error

Prefers AST analysis. Files using 1.5.2 `print "x"` statement syntax cannot be
parsed by a Python 3 AST, so those fall back to regex scanning (reported as such).
"""
import ast
import re
import sys

# --- things 1.5.2 does not have -------------------------------------------
BUILTINS_ABSENT = ('enumerate', 'zip', 'sorted', 'sum', 'bool', 'isinstance_',
                   'set', 'dict', 'True', 'False', 'None_')
STR_METHODS = ('split', 'join', 'strip', 'lstrip', 'rstrip', 'replace', 'find',
               'lower', 'upper', 'startswith', 'endswith')


class Visitor(ast.NodeVisitor):
    def __init__(self):
        self.problems = []
        self.loop_try_depth = []

    def add(self, node, msg):
        self.problems.append((getattr(node, 'lineno', 0), msg))

    # `continue` is illegal inside a try BODY in 1.5.2 (legal in an except handler)
    def visit_Try(self, node):
        for stmt in node.body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Continue):
                    self.add(sub, "'continue' inside a try body - illegal in 1.5.2 "
                                  "(move it, or restructure; it IS legal in an except handler)")
        self.generic_visit(node)

    def visit_ListComp(self, node):
        self.add(node, "list comprehension - not in 1.5.2 (use a for loop / map / filter)")
        self.generic_visit(node)

    def visit_DictComp(self, node):
        self.add(node, "dict comprehension - not in 1.5.2")
        self.generic_visit(node)

    def visit_SetComp(self, node):
        self.add(node, "set comprehension - not in 1.5.2")
        self.generic_visit(node)

    def visit_GeneratorExp(self, node):
        self.add(node, "generator expression - not in 1.5.2")
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        op = type(node.op).__name__
        self.add(node, "augmented assignment (%s=) - not in 1.5.2, write 'x = x + 1'" % op)
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in ('True', 'False'):
            self.add(node, "%s - no booleans in 1.5.2, use 1 / 0" % node.id)
        elif node.id in ('enumerate', 'zip', 'sorted', 'sum', 'set'):
            self.add(node, "%s() - not a 1.5.2 builtin" % node.id)
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        # bare string methods: "x".split() / var.split()  -> use the string module
        if isinstance(f, ast.Attribute) and f.attr in STR_METHODS:
            # Python 3.8+ parses every string literal as ast.Constant
            if isinstance(f.value, ast.Constant) and isinstance(f.value.value, str):
                self.add(node, "string method .%s() on a literal - 1.5.2 has no str methods; "
                               "use the string module (e.g. string.%s(s, ...))" % (f.attr, f.attr))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module == '__future__':
            self.add(node, "__future__ import - not in 1.5.2")
        self.generic_visit(node)


REGEX_CHECKS = (
    (re.compile(r'\bTrue\b|\bFalse\b'), "True/False - no booleans in 1.5.2, use 1 / 0"),
    (re.compile(r'\[[^\]\n]*\bfor\b[^\]\n]*\bin\b[^\]\n]*\]'), "looks like a list comprehension"),
    (re.compile(r'[\w\)\]]\s*(\+=|-=|\*=|/=|%=)'), "augmented assignment - not in 1.5.2"),
    (re.compile(r'\b(enumerate|zip|sorted|sum)\s*\('), "non-1.5.2 builtin"),
    (re.compile(r'\bfrom\s+__future__\b'), "__future__ import - not in 1.5.2"),
    (re.compile(r'^\s*@\w'), "decorator - not in 1.5.2"),
    (re.compile(r'\bclass\s+\w+\s*\([^)]*\)\s*:\s*$'), None),  # informational, skipped
)


def check_regex(path, text):
    problems = []
    in_try = False
    try_indent = 0
    for i, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith('#'):
            continue
        for rx, msg in REGEX_CHECKS:
            if msg is None:
                continue
            if rx.search(line):
                problems.append((i, msg))
        # crude continue-in-try detection
        if re.match(r'^\s*try\s*:', line):
            in_try = True
            try_indent = len(line) - len(line.lstrip())
        elif in_try:
            cur = len(line) - len(line.lstrip())
            if stripped and cur <= try_indent and not re.match(r'^\s*(except|finally)', line):
                in_try = False
            elif re.match(r'^\s*continue\b', line) and not re.match(r'^\s*except', line):
                problems.append((i, "'continue' possibly inside a try body - illegal in 1.5.2"))
    return problems


def check(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except IOError as exc:
        print("%s: CANNOT READ (%s)" % (path, exc))
        return 1

    mode = 'ast'
    try:
        tree = ast.parse(text)
    except SyntaxError:
        mode = 'regex'
        problems = check_regex(path, text)
    else:
        v = Visitor()
        v.visit(tree)
        problems = v.problems

    if problems:
        print("%s  [%s]  %d problem(s):" % (path, mode, len(problems)))
        for lineno, msg in sorted(set(problems)):
            print("   line %-5s %s" % (lineno, msg))
        return 1

    note = "" if mode == 'ast' else "  (regex fallback - file uses 1.5.2 print-statement syntax)"
    print("%s  [%s]  clean%s" % (path, mode, note))
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    rc = 0
    for path in argv[1:]:
        if check(path):
            rc = 1
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
