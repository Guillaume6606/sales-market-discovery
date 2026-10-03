"""Exercise deployment scripts without contacting Docker or a remote host."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DeploySandbox = tuple[Path, dict[str, str]]


@pytest.fixture
def deploy_sandbox(tmp_path: Path) -> DeploySandbox:
    commands = tmp_path / "bin"
    commands.mkdir()
    remote = tmp_path / "remote"
    remote.mkdir()
    executable = commands / "fake-command"
    executable.write_text(
        f"#!{sys.executable}\n"
        """
import json
import os
import subprocess
import sys
import time
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['DEPLOY_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
failure = os.environ.get('DEPLOY_TEST_FAILURE')
if name == 'docker':
    action = args[5:]
    if action[0] == 'build' and failure == 'build':
        sys.exit(1)
    if 'pg_isready' in ' '.join(action) and failure == 'db':
        sys.exit(1)
    if 'pg_dump' in ' '.join(action):
        if failure == 'backup':
            sys.exit(1)
        if failure != 'empty_backup':
            sys.stdout.write('PGDMP backup fixture')
    if 'alembic' in action and failure == 'migration':
        sys.exit(1)
elif name == 'curl':
    if failure == 'ready':
        sys.stdout.write('503')
        sys.exit(22)
    sys.stdout.write('200')
elif name == 'sleep':
    time.sleep(0.003)
elif name == 'rsync':
    filtered = []
    skip = False
    for arg in args[:-1]:
        if skip:
            skip = False
        elif arg == '-e':
            skip = True
        else:
            filtered.append(arg)
    subprocess.run([os.environ['DEPLOY_TEST_RSYNC'], *filtered,
                    os.environ['DEPLOY_TEST_REMOTE'] + '/'], check=True)
"""
    )
    executable.chmod(0o755)
    for name in ("docker", "curl", "sleep", "ssh", "rsync"):
        (commands / name).symlink_to(executable)
    env = {
        **os.environ,
        "PATH": f"{commands}:{os.environ['PATH']}",
        "DEPLOY_TEST_LOG": str(tmp_path / "commands.jsonl"),
        "DEPLOY_TEST_RSYNC": shutil.which("rsync") or "/usr/bin/rsync",
        "DEPLOY_TEST_REMOTE": str(remote),
        "SSH_HOST": "example.invalid",
        "SSH_QUICK": "0",
    }
    return tmp_path, env


def run_deploy(
    sandbox: DeploySandbox,
    *,
    failure: str = "",
    quick: str = "0",
    local: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    directory, env = sandbox
    script = "deploy.sh" if local else "remote-deploy.sh"
    try:
        result = subprocess.run(
            ["bash", str(ROOT / "infra" / script), quick],
            cwd=directory,
            env={**env, "DEPLOY_TEST_FAILURE": failure, "SSH_QUICK": quick},
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Deployment did not stop after repeated readiness failures")
    log = Path(env["DEPLOY_TEST_LOG"])
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result, calls


def docker_actions(calls: list[list[str]]) -> list[list[str]]:
    return [call[6:] for call in calls if call[0] == "docker"]


def test_deploy_backs_up_before_migration_and_only_recreates_apps(deploy_sandbox: DeploySandbox) -> None:
    result, calls = run_deploy(deploy_sandbox)
    assert result.returncode == 0, result.stderr
    actions = docker_actions(calls)
    build = next(i for i, call in enumerate(actions) if call[0] == "build")
    stop = next(i for i, call in enumerate(actions) if call[0] == "stop")
    backup = next(i for i, call in enumerate(actions) if "pg_dump" in " ".join(call))
    migrate = next(i for i, call in enumerate(actions) if "alembic" in call)
    restart = next(i for i, call in enumerate(actions) if "--force-recreate" in call)
    assert build < stop < backup < migrate < restart
    assert {"backend", "ingestion", "ui"}.issubset(actions[stop])
    assert {"backend", "ingestion", "ui", "caddy"}.issubset(actions[restart])
    assert "--no-deps" in actions[restart]
    assert "db" not in actions[restart] and "redis" not in actions[restart]
    infrastructure = [call for call in actions if call[0] == "up" and "db" in call]
    assert infrastructure and all("--no-recreate" in call for call in infrastructure)
    backups = list((deploy_sandbox[0] / "backups").glob("*.dump"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"PGDMP backup fixture"
    assert backups[0].stat().st_mode & 0o777 == 0o600
    assert str(backups[0]) in result.stdout


@pytest.mark.parametrize("local", [False, True])
def test_quick_deploy_rejected_before_any_external_changes(deploy_sandbox: DeploySandbox, local: bool) -> None:
    result, calls = run_deploy(deploy_sandbox, quick="1", local=local)
    assert result.returncode != 0
    assert calls == []
    assert "rebuild" in (result.stdout + result.stderr).lower()


def test_build_failure_does_not_stop_running_services(deploy_sandbox: DeploySandbox) -> None:
    result, calls = run_deploy(deploy_sandbox, failure="build")
    assert result.returncode != 0
    assert all(call[0] == "build" for call in docker_actions(calls))


def test_database_wait_is_bounded_and_does_not_stop_apps(deploy_sandbox: DeploySandbox) -> None:
    result, calls = run_deploy(deploy_sandbox, failure="db")
    assert result.returncode != 0
    actions = docker_actions(calls)
    assert 1 < sum("pg_isready" in " ".join(call) for call in actions) <= 30
    assert not any(call[0] in ("stop", "run") for call in actions)
    assert "database" in (result.stdout + result.stderr).lower()


@pytest.mark.parametrize("failure", ["backup", "empty_backup"])
def test_failed_backup_prevents_migration_and_restart(deploy_sandbox: DeploySandbox, failure: str) -> None:
    result, calls = run_deploy(deploy_sandbox, failure=failure)
    assert result.returncode != 0
    actions = docker_actions(calls)
    assert not any("alembic" in call or "--force-recreate" in call for call in actions)
    assert "backup" in (result.stdout + result.stderr).lower()


def test_failed_migration_keeps_backup_and_does_not_restart_apps(deploy_sandbox: DeploySandbox) -> None:
    result, calls = run_deploy(deploy_sandbox, failure="migration")
    assert result.returncode != 0
    assert not any("--force-recreate" in call or "downgrade" in call for call in calls)
    backups = list((deploy_sandbox[0] / "backups").glob("*.dump"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0
    assert str(backups[0]) in result.stdout + result.stderr


def test_failed_readiness_is_bounded_without_dumping_logs(deploy_sandbox: DeploySandbox) -> None:
    result, calls = run_deploy(deploy_sandbox, failure="ready")
    assert result.returncode != 0
    probes = [call for call in calls if call[0] == "curl"]
    assert 1 < len(probes) <= 20
    assert all("--max-time" in call for call in probes)
    assert not any("logs" in call for call in docker_actions(calls))
    backups = list((deploy_sandbox[0] / "backups").glob("*.dump"))
    assert backups and str(backups[0]) in result.stdout + result.stderr


def test_sync_protects_remote_secrets_backups_and_local_artifacts(deploy_sandbox: DeploySandbox) -> None:
    directory, env = deploy_sandbox
    (directory / "application.py").write_text("new code")
    (directory / ".env.local").write_text("local secret")
    (directory / "credentials.json").write_text("local credential")
    (directory / ".codex").mkdir()
    (directory / ".codex" / "session").write_text("local context")
    # Keep the receiving directory outside the source rsync tree.
    remote = directory.parent / f"{directory.name}-destination"
    remote.mkdir()
    env["DEPLOY_TEST_REMOTE"] = str(remote)
    (remote / ".env.prod").write_text("production secret")
    (remote / "backups").mkdir()
    (remote / "backups" / "prior.dump").write_text("prior backup")
    (remote / "obsolete.py").write_text("old code")
    result, _ = run_deploy(deploy_sandbox, local=True)
    assert result.returncode == 0, result.stderr
    assert (remote / ".env.prod").read_text() == "production secret"
    assert (remote / "backups" / "prior.dump").read_text() == "prior backup"
    assert (remote / "application.py").read_text() == "new code"
    assert not (remote / "obsolete.py").exists()
    assert not any(
        (remote / name).exists() for name in (".env.local", ".codex", "credentials.json")
    )
