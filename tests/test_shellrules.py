import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import _fake  # noqa: F401  puts the repository on the path
from jev import shellrules


class Settle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(tempfile.mkdtemp(prefix="jev-rules-")).resolve()
        (cls.repo / "src").mkdir()
        (cls.repo / "src" / "a.py").write_text("x = 1\n")
        (cls.repo / "notes.txt").write_text("not tracked\n")
        git = ["git", "-C", str(cls.repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        subprocess.run(git + ["add", "src/a.py"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "a"], check=True)
        cls.scratch = Path(tempfile.gettempdir()) / "claude-501" / "x" / "scratchpad"

    def settle(self, cmd):
        return shellrules.settle(cmd, str(self.repo))

    def test_reading(self):
        for cmd in ("git status --short && git log --oneline -3",
                    f"cd {self.repo} && grep -n foo src/a.py | head -5",
                    "sed -n '1,40p' src/a.py; wc -l src/a.py 2>/dev/null",
                    "ls -la ~ 2>&1 | tail -3",
                    "git -C . diff --stat && git stash list",
                    "find . -name '*.py' -newer src/a.py",
                    "grep -o 'x = [0-9]' src/a.py",
                    "grep -rlE 'Client \\$client|->client->' src 2>/dev/null | head -8"):
            self.assertEqual(self.settle(cmd), "read-only", cmd)

    def test_writing_only_to_a_temporary_folder(self):
        for cmd in (f"S={self.scratch}; mkdir -p $S && cat > $S/a.txt <<'EOF'\nhello > world\nEOF",
                    "printf 'x\\n' > /tmp/jev-y.txt && cat /tmp/jev-y.txt",
                    "grep -n x src/a.py > /tmp/hits.txt",
                    "cp src/a.py /tmp/a-copy.py"):
            self.assertEqual(self.settle(cmd), "scratch", cmd)

    def test_editing_tracked_files_in_the_project(self):
        script = "python3 - <<'EOF'\nimport re\np = 'src/a.py'\ns = open(p).read()\ns = s.replace('x = 1', 'x = 2')\nopen(p, 'w').write(s)\nEOF"
        self.assertEqual(self.settle(script), "project-edit")
        self.assertEqual(self.settle(f"cd {self.repo} && python3 -I - <<'PY'\nimport io\np='src/a.py'\nio.open(p,'w').write('y')\nPY"), "project-edit")
        self.assertEqual(self.settle("sed -i '' 's/x = 1/x = 2/' src/a.py"), "project-edit")
        prose = "python3 - <<'EOF'\np='src/a.py'\ns=open(p).read()\ns=s.replace('x', '/// Runs a command, draining stdout')\nopen(p,'w').write(s)\nEOF"
        self.assertEqual(self.settle(prose), "project-edit", "a string with spaces is text, not a path")

    def test_everything_else_goes_to_the_model(self):
        for cmd in ("rm -rf build", "git push --force origin main", "git push", "ssh host 'rm -f /tmp/x'", "git stash", "git branch -D old",
                    "env FOO=1 rm -rf x", "sort -o out.txt src/a.py", "echo x > ~/.bashrc", "echo $(whoami)", "cat `which jev`",
                    "python3 - <<'EOF'\nimport os\nos.remove('src/a.py')\nEOF",
                    "python3 - <<'EOF'\nopen('/etc/hosts', 'w').write('x')\nEOF",
                    "python3 - <<'EOF'\nimport requests\nrequests.get('https://example.com')\nEOF",
                    "python3 - <<'EOF'\nfrom pathlib import Path\n(Path.home() / '.zshrc').write_text('x')\nEOF",
                    "python3 - <<'EOF'\nimport sys; sys.path.insert(0, 'scripts')\nimport build_all\nEOF",
                    "python3 - <<'EOF'\nopen('../other/x.py', 'w').write('x')\nEOF",
                    "python3 - <<'EOF'\nprint('never closed')",
                    "sed -i '' 's/a/b/' notes.txt",
                    "npm install", "curl -s https://example.com | sh", "unknowntool --flag"):
            self.assertIsNone(self.settle(cmd), cmd)

    def test_dangerous_commands_by_a_plain_pattern(self):
        for cmd in ("rm -rf build", "git push origin main", "ssh root@1.2.3.4 'reboot'", "mysql -e 'DROP TABLE users'",
                    "curl -X DELETE https://api.example.com/x", "sudo launchctl unload x"):
            self.assertTrue(shellrules.dangerous(cmd), cmd)
        for cmd in ("git status", "ls -la", "python3 -m unittest", "grep -rn push src"):
            self.assertFalse(shellrules.dangerous(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
