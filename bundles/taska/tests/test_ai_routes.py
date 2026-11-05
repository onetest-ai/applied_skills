"""`taska ai`: how an unattended run reaches Claude. The first route that answers: a stored subscription token or API
key (~/.config/taska/claude, chmod 600), then CodeMie while its SSO session lasts, then Bedrock only when
TASKA_ALLOW_BEDROCK=1, since end users have no AWS. Stub programs stand in for claude and codemie and record which
credential each call carried, never its value. Invented data only."""
import json

from taska_lib import ai

TOKEN = "sk-ant-oat01-" + "x" * 40
KEY = "sk-ant-api03-" + "y" * 40


def _stub(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _env(tmp_path, *, claude_rc=0, codemie=None, token=None, mode=0o600, bedrock=False):
    """codemie: None (not installed), "in" (signed in) or "out" (signed out)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir(parents=True)
    log = tmp_path / "calls.log"
    _stub(bin_ / "claude", f'echo "claude oauth=${{CLAUDE_CODE_OAUTH_TOKEN:+set}} key=${{ANTHROPIC_API_KEY:+set}} '
                           f'bedrock=${{CLAUDE_CODE_USE_BEDROCK:-}} aws=${{AWS_ACCESS_KEY_ID:+set}}" >> {log}\necho ok\nexit {claude_rc}\n')
    if codemie:
        _stub(bin_ / "codemie-claude", f'echo "codemie-claude $*" >> {log}\necho ok\n')
        _stub(bin_ / "codemie", "echo 'Profile ok'\n" if codemie == "in" else
              "echo 'No SSO credentials found for https://codemie.example. Please run: codemie profile login'\n")
    home = tmp_path / "home"
    (home / ".config" / "taska").mkdir(parents=True)
    if token:
        f = home / ".config" / "taska" / "claude"
        f.write_text(token + "\n")
        f.chmod(mode)
    env = {"HOME": str(home), "PATH": f"{bin_}:/usr/bin:/bin", "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_ACCESS_KEY_ID": "stale"}
    if bedrock:
        env["TASKA_ALLOW_BEDROCK"] = "1"
    return env, log


def _calls(log):
    return log.read_text().splitlines() if log.exists() else []


def test_a_subscription_token_is_used_first_without_aws(tmp_path):
    env, log = _env(tmp_path, token=TOKEN, codemie="in")
    r = ai.run(["-p", "hi"], env=env)
    assert r["ok"] and r["route"] == "token" and _calls(log) == ["claude oauth=set key= bedrock= aws="] * 2   # probe, then the job


def test_an_api_key_goes_in_as_an_api_key(tmp_path):
    env, log = _env(tmp_path, token=KEY)
    assert ai.run(["-p", "hi"], env=env)["ok"] and _calls(log) == ["claude oauth= key=set bedrock= aws="] * 2


def test_without_a_token_codemie_answers_while_signed_in(tmp_path):
    env, log = _env(tmp_path, codemie="in")
    r = ai.run(["-p", "hi"], env=env)
    assert r["route"] == "codemie" and _calls(log) == [f"codemie-claude -p {ai.PROBE}", "codemie-claude -p hi"]


def test_a_refused_token_falls_back_to_codemie(tmp_path):
    env, log = _env(tmp_path, token=TOKEN, claude_rc=1, codemie="in")
    r = ai.run(["-p", "hi"], env=env)
    assert r["route"] == "codemie" and "setup-token" in r["why"]["token"]


def test_a_signed_out_codemie_is_not_called(tmp_path):
    env, log = _env(tmp_path, codemie="out")
    r = ai.run(["-p", "hi"], env=env)
    assert not r["ok"] and _calls(log) == [] and "codemie profile login" in r["why"]["codemie"]


def test_bedrock_only_when_a_project_turns_it_on(tmp_path):
    env, log = _env(tmp_path)
    assert not ai.run(["-p", "hi"], env=env)["ok"] and _calls(log) == []
    env, log = _env(tmp_path / "on", bedrock=True)
    r = ai.run(["-p", "hi"], env=env)
    assert r["route"] == "bedrock" and _calls(log) == ["claude oauth= key= bedrock=1 aws=set"] * 2


def test_a_token_others_can_read_is_refused(tmp_path):
    env, log = _env(tmp_path, token=TOKEN, mode=0o644)
    r = ai.run(["-p", "hi"], env=env)
    assert not r["ok"] and _calls(log) == [] and "chmod 600" in r["why"]["token"]


def test_the_probe_says_which_route_answered_or_how_to_fix_each(tmp_path):
    env, _ = _env(tmp_path, token=TOKEN)
    assert ai.run([], probe=True, env=env) == {"ok": True, "route": "token", "why": {}}
    env, _ = _env(tmp_path / "none", codemie="out")
    r = ai.run([], probe=True, env=env)
    assert not r["ok"] and set(r["why"]) == {"token", "codemie", "bedrock"} and "TASKA_ALLOW_BEDROCK" in r["why"]["bedrock"]


def test_the_token_never_appears_in_any_result(tmp_path):
    for token in (TOKEN, KEY):
        env, _ = _env(tmp_path / token[:10], token=token, claude_rc=1)
        assert token not in json.dumps(ai.run([], probe=True, env=env))


def test_the_schedule_runs_through_taska_ai(project):
    from conftest import run_cli
    line = run_cli(project, "schedule")["line"]
    assert " ai -- -p " in line and "/taska:run --due" in line and " claude -p" not in line


def test_a_job_that_fails_after_it_started_is_never_run_again_on_another_route(tmp_path):
    # the token answers its probe, then the paid job itself fails: rerunning it on CodeMie would pay for it twice
    env, log = _env(tmp_path, token=TOKEN, codemie="in")
    _stub(tmp_path / "bin" / "claude", f'echo "claude $*" >> {log}\ncase "$*" in *"{ai.PROBE}"*) echo ok; exit 0;; esac\nexit 3\n')
    r = ai.run(["-p", "the whole nightly pass"], env=env)
    assert not r["ok"] and r["route"] == "token" and "exit 3" in r["why"]["token"] and "not retried" in r["why"]["token"]
    assert not any(c.startswith("codemie-claude") for c in _calls(log))


def test_a_test_that_reaches_a_real_model_is_trapped(tmp_path):
    import os
    import shutil
    import subprocess
    from pathlib import Path
    log = Path(os.environ["TASKA_MODEL_TRAP_LOG"])   # set by conftest for every test, before anything is called
    r = subprocess.run([shutil.which("claude"), "-p", "hi"], capture_output=True, text=True)
    assert r.returncode == 99 and "a test reached a real model" in r.stderr
    home = tmp_path / "home"
    (home / ".config" / "taska").mkdir(parents=True)
    (home / ".config" / "taska" / "claude").write_text(TOKEN)
    (home / ".config" / "taska" / "claude").chmod(0o600)
    trap = os.environ["PATH"].split(os.pathsep)[0]
    env = dict(os.environ, HOME=str(home), PATH=f"{trap}{os.pathsep}/usr/bin{os.pathsep}/bin")
    assert ai.run(["-p", "x"], probe=True, env=env)["ok"] is False
    assert "claude -p hi" in log.read_text()
    log.unlink()   # this test reached the trap on purpose
