"""How an unattended run reaches Claude: the first route that answers.

- `token`: ~/.config/taska/claude (chmod 600) holds a `claude setup-token` subscription token or an API key;
- `codemie`: codemie-claude, while its SSO session lasts (about a day; `codemie profile login` renews it);
- `bedrock`: claude on Bedrock, only when TASKA_ALLOW_BEDROCK=1 (end users have no AWS).

A route that cannot run says why, with the command that fixes it; the token itself is never printed."""
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ROUTES = ("token", "codemie", "bedrock")
PROBE = "Reply with the single word: ok"
TIMEOUT = 120
_CLOUD = ("CLAUDE_CODE_USE_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "ANTHROPIC_MODEL")


def token_file(env):
    return Path(env.get("HOME", "~")).expanduser() / ".config" / "taska" / "claude"


def _route(name, env):
    """(argv prefix, env for the call) or (None, why it cannot run)."""
    path = env.get("PATH")
    plain = {k: v for k, v in env.items() if k not in _CLOUD}
    if name == "token":
        f = token_file(env)
        if not f.is_file():
            return None, f"no token: run 'claude setup-token' and save the token in {f} (chmod 600)"
        if stat.S_IMODE(f.stat().st_mode) & 0o077:
            return None, f"{f} can be read by others: chmod 600 it"
        t = "".join(f.read_text().split())
        var = "ANTHROPIC_API_KEY" if t.startswith("sk-ant-api") else "CLAUDE_CODE_OAUTH_TOKEN"
        return [shutil.which("claude", path=path) or "claude"], plain | {var: t}
    if name == "codemie":
        exe, cli = shutil.which("codemie-claude", path=path), shutil.which("codemie", path=path)
        if not exe:
            return None, "CodeMie is not installed"
        st = subprocess.run([cli, "profile", "status"], capture_output=True, text=True, env=env, timeout=60) if cli else None
        said = st.stdout + st.stderr if st else ""
        if st is None or re.search(r"No SSO credentials|Authentication required|expired", said, re.I):
            return None, f"signed out: run '{_login(said)}'"
        return [exe], plain
    if env.get("TASKA_ALLOW_BEDROCK") != "1":
        return None, "off (set TASKA_ALLOW_BEDROCK=1 to allow it)"
    return [shutil.which("claude", path=path) or "claude"], env | {"CLAUDE_CODE_USE_BEDROCK": "1"}


def _login(said):
    """The login command CodeMie itself names (it carries the instance URL), else the plain one."""
    m = re.search(r"codemie profile login(?: --url \S+)?", said)
    return m.group(0).rstrip(".") if m else "codemie profile login"


FIX = {"token": "the token was refused: run 'claude setup-token' again and replace {f}",
       "codemie": "the CodeMie call failed: run 'codemie profile login'",
       "bedrock": "the Bedrock call failed: refresh the AWS credentials"}


def run(args, probe=False, env=None):
    """{"ok", "route", "why": {route: reason}}. A probe makes one tiny call per route until one answers "ok";
    otherwise `args` go to each route in turn, its output to this process's, until one exits 0."""
    env = dict(os.environ if env is None else env)
    why = {}
    for name in ROUTES:
        argv, how = _route(name, env)
        if argv is None:
            why[name] = how
            continue
        try:
            if probe:
                r = subprocess.run(argv + ["-p", PROBE], capture_output=True, text=True, env=how, timeout=TIMEOUT)
                answered = r.returncode == 0 and "ok" in r.stdout.lower()
            else:
                answered = subprocess.run(argv + list(args), env=how).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            answered = False
        if answered:
            return {"ok": True, "route": name, "why": why if not probe else {}}
        why[name] = FIX[name].format(f=token_file(env))
    return {"ok": False, "route": None, "why": why}
