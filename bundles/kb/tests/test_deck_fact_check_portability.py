"""deck-fact-check must run wherever the plugin is installed (Claude Code or Cowork): commands name the skill dir,
scripts import only the stdlib, the document libraries (lazily) and their own siblings (spec R7, D4)."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest

from test_plugin_structure import KB_ROOT, read_text

SKILL_DIR = KB_ROOT / "skills" / "deck-fact-check"
SKILL = read_text(SKILL_DIR / "SKILL.md")
SCRIPTS = sorted(p.stem for p in SKILL_DIR.glob("*.py"))
COMMAND = re.compile(r'python3? +("[^"\n]+?\.py"|\S+\.py)')
LIBRARIES = {"lxml", "docx", "pptx", "pymupdf"}

try:
    import pptx  # noqa: F401
    import pymupdf  # noqa: F401
    HAVE_LIBS = True
except ImportError:
    HAVE_LIBS = False


class Commands(unittest.TestCase):
    def test_every_command_names_the_skill_dir_and_an_existing_script(self):
        commands = COMMAND.findall(SKILL)
        self.assertTrue(commands)
        for target in commands:
            if "<script>" in target:
                continue
            with self.subTest(target=target):
                self.assertRegex(target, r'^"<skill dir>/[a-z_]+\.py"?$')
                self.assertIn(re.search(r"([a-z_]+)\.py", target).group(1), SCRIPTS)

    def test_step_0_checks_the_dependencies_with_the_check_flag(self):
        step0 = SKILL[SKILL.index("### 0. Resolve"):SKILL.index("### 0b.")]
        self.assertIn('python "<skill dir>/pptx_sections.py" --check', step0)
        self.assertIn("deck-fact-check needs Python with python-pptx and PyMuPDF", step0)

    def test_no_word_only_command_or_file(self):
        for word in ('"<skill dir>/sections.py"', '"<skill dir>/annotate.py"', ".docx", "python-docx"):
            self.assertNotIn(word, SKILL)


class Scripts(unittest.TestCase):
    def test_imports_are_stdlib_lazy_libraries_or_siblings(self):
        allowed = set(sys.stdlib_module_names) | LIBRARIES | {"__future__"} | set(SCRIPTS)
        for name in SCRIPTS:
            tree = ast.parse((SKILL_DIR / f"{name}.py").read_text(encoding="utf-8"))
            top = {(a.name if isinstance(n, ast.Import) else n.module or "").split(".")[0]
                   for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
                   for a in (n.names if isinstance(n, ast.Import) else [n])}
            with self.subTest(script=name):
                self.assertFalse(top & LIBRARIES, f"{name}.py imports a document library at module level")
            for node in ast.walk(tree):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for m in mods:
                    with self.subTest(script=name, module=m):
                        self.assertIn(m.split(".")[0], allowed)

    def test_sibling_imports_follow_the_sys_path_idiom(self):
        idiom = "sys.path.insert(0, str(Path(__file__).resolve().parent))"
        for name in SCRIPTS:
            src = (SKILL_DIR / f"{name}.py").read_text(encoding="utf-8")
            sib = [n for n in ast.walk(ast.parse(src))
                   if (isinstance(n, ast.ImportFrom) and n.module in SCRIPTS)
                   or (isinstance(n, ast.Import) and any(a.name in SCRIPTS for a in n.names))]
            if sib:
                with self.subTest(script=name):
                    self.assertIn(idiom, src)
                    self.assertLess(src[:src.index(idiom)].count("\n") + 1, min(n.lineno for n in sib))

    @unittest.skipUnless(HAVE_LIBS, "needs python-pptx and PyMuPDF")
    def test_every_script_help_runs_from_any_working_directory(self):
        for name in SCRIPTS:
            for flags in ((), ("-I",)):
                with self.subTest(script=name, flags=flags):
                    r = subprocess.run([sys.executable, *flags, str(SKILL_DIR / f"{name}.py"), "--help"],
                                       capture_output=True, text=True, cwd="/")
                    self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
