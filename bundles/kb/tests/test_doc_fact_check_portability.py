"""doc-fact-check must run wherever the plugin is installed (Claude Code or Cowork), not only from the project folder."""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest

from test_fact_check_code import HAVE_DOCX
from test_plugin_structure import KB_ROOT, read_text

SKILL_DIR = KB_ROOT / "skills" / "doc-fact-check"
SKILL = read_text(SKILL_DIR / "SKILL.md")
SCRIPTS = sorted(p.stem for p in SKILL_DIR.glob("*.py"))
COMMAND = re.compile(r'python3? +("[^"\n]+?\.py"|\S+\.py)')


class ScriptCommandsUseTheSkillDir(unittest.TestCase):
    def test_skill_dir_is_defined_once(self):
        self.assertEqual(SKILL.count("`<skill dir>` is the directory containing this SKILL.md"), 1)

    def test_every_command_names_the_skill_dir_and_an_existing_script(self):
        commands = COMMAND.findall(SKILL)
        self.assertTrue(commands, "SKILL.md documents no script command")
        for target in commands:
            if "<script>" in target:  # the placeholder in the definition line
                continue
            with self.subTest(target=target):
                self.assertRegex(target, r'^"<skill dir>/[a-z_]+\.py"?$')
                name = re.search(r"([a-z_]+)\.py", target).group(1)
                self.assertIn(name, SCRIPTS)

    def test_no_relative_beside_this_file_wording(self):
        self.assertNotIn("sits beside this file", SKILL)


class DependencyPreCheck(unittest.TestCase):
    def test_step_0_checks_python_docx_before_extraction(self):
        step0 = SKILL[SKILL.index("### 0. Resolve"):SKILL.index("### 1. Extract")]
        self.assertIn("doc-fact-check needs Python with python-docx ≥ 1.2 in this environment", step0)

    def test_impossible_fallback_is_gone(self):
        # step 1 itself needs python-docx, so "run through step 7 without it" cannot work
        self.assertNotIn("run through step 7", SKILL)


class ScriptsAreSelfContained(unittest.TestCase):
    def test_scripts_import_only_stdlib_docx_or_siblings(self):
        allowed = set(sys.stdlib_module_names) | {"docx", "__future__"} | set(SCRIPTS)
        for name in SCRIPTS:
            tree = ast.parse((SKILL_DIR / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for m in mods:
                    with self.subTest(script=name, module=m):
                        self.assertIn(m.split(".")[0], allowed)

    @unittest.skipUnless(HAVE_DOCX, "needs python-docx >= 1.2")
    def test_every_script_help_runs_from_any_working_directory(self):
        for name in SCRIPTS:
            with self.subTest(script=name):
                r = subprocess.run([sys.executable, str(SKILL_DIR / f"{name}.py"), "--help"],
                                   capture_output=True, text=True, cwd="/")
                self.assertEqual(r.returncode, 0, r.stderr)



class DependencyCheckFlag(unittest.TestCase):
    @unittest.skipUnless(HAVE_DOCX, "needs python-docx >= 1.2")
    def test_check_prints_versions_and_exits_zero(self):
        r = subprocess.run([sys.executable, str(SKILL_DIR / "sections.py"), "--check"],
                           capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout.strip(), r"^python \d+\.\d+\.\d+ python-docx \d+\.\d+")

    def test_check_fails_cleanly_without_python_docx(self):
        # run with an empty site so `import docx` fails
        r = subprocess.run([sys.executable, "-S", "-I", str(SKILL_DIR / "sections.py"), "--check"],
                           capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 2)
        self.assertIn("doc-fact-check needs Python with python-docx ≥ 1.2", r.stderr)

    def test_step_0_uses_the_check_flag_not_inline_python(self):
        step0 = SKILL[SKILL.index("### 0. Resolve"):SKILL.index("### 0b.")]
        self.assertIn('python "<skill dir>/sections.py" --check', step0)
        self.assertNotIn("python -c", SKILL)

if __name__ == "__main__":
    unittest.main()
