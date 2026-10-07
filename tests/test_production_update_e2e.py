"""Production-update failure cases, specified before deployment changes.

Exercise the real Bash script and SQLite online backup across a simulated Docker
boundary. No Docker daemon, deployment .env, upstream API or Telegram is used.
Check a live WAL database, interrupted Git/config/build/settings/backup phases,
first installation, and obsolete alert states retained in an existing database.
"""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests.helpers import login, valid_env
from tests.test_redesign_e2e import make_system


ROOT = Path(__file__).resolve().parents[1]
BASH = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
HARNESS = r"""
git() {
  printf 'git %s\n' "$*" >> "$TEST_CALLS"
  [ "$FAIL_PHASE" != git ]
}
docker() {
  if [ "$1" = compose ] && [ "${2-}" = --progress ]; then
    shift 3
    set -- compose "$@"
  fi
  printf 'docker %s\n' "$*" >> "$TEST_CALLS"
  case "$1 $2 ${3-}" in
    'compose ps --services')
      if [ "$SERVICE_RUNNING" = yes ]; then printf 'cloudways-monitor\n'; fi
      ;;
    'compose config --quiet') [ "$FAIL_PHASE" != config ] ;;
    'compose build cloudways-monitor') [ "$FAIL_PHASE" != build ] ;;
    'compose run --rm')
      shift 7
      "$TEST_PYTHON" "$@"
      ;;
    'compose exec -T')
      shift 5
      if [ "$1" != - ]; then return 2; fi
      "$TEST_PYTHON" - "$TEST_SNAPSHOT"
      ;;
    'compose cp cloudways-monitor:/tmp/cloudways-monitor-deploy-backup.sqlite3')
      cp "$TEST_SNAPSHOT" "$4"
      ;;
    'compose up -d'|'compose ps ')
      ;;
    *) printf 'Unexpected Docker command: %s\n' "$*" >&2; return 2 ;;
  esac
}
source ./deploy.sh
"""


def run_deploy(tmp_path, *, phase="ok", running=True):
    if not BASH or not Path(BASH).is_file():
        pytest.skip("Bash is required for deployment-script E2E")
    (tmp_path / ".git").mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("LOCAL_TEST_SENTINEL=unchanged\n", encoding="utf-8")
    (tmp_path / "deploy.sh").write_text(
        (ROOT / "deploy.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
        newline="\n",
    )
    calls_file = tmp_path / "calls.log"
    values = valid_env(SQLITE_PATH=str(tmp_path / "running.sqlite3"))
    if phase == "settings":
        values["CLOUDWAYS_ACCESS_TOKEN"] = ""
    env = dict(
        os.environ,
        **values,
        PYTHONPATH=str(ROOT),
        TEST_PYTHON=sys.executable.replace("\\", "/"),
        TEST_CALLS=calls_file.as_posix(),
        TEST_SNAPSHOT=(tmp_path / "container-snapshot.sqlite3").as_posix(),
        BACKUP_DIR=(tmp_path / "backups").as_posix(),
        FAIL_PHASE=phase,
        SERVICE_RUNNING="yes" if running else "no",
    )
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c", HARNESS, "deploy.sh"],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
    calls = calls_file.read_text(encoding="utf-8").splitlines()
    assert env_file.read_text(encoding="utf-8") == "LOCAL_TEST_SENTINEL=unchanged\n"
    return result, calls


def evidence(name, payload):
    folder = ROOT / ".tmp" / "e2e"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"production-update-{name}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def test_deployment_snapshots_live_wal_before_recreating_service(tmp_path):
    database = tmp_path / "running.sqlite3"
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE history (value TEXT)")
        writer.execute("INSERT INTO history VALUES ('committed')")
        writer.commit()
        writer.execute("INSERT INTO history VALUES ('uncommitted')")
        assert database.with_name(database.name + "-wal").is_file()
        result, calls = run_deploy(tmp_path)
        assert result.returncode == 0, result.stderr
        backup_files = list((tmp_path / "backups").glob("*.sqlite3"))
        assert len(backup_files) == 1
        with sqlite3.connect(backup_files[0]) as backup:
            assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert backup.execute("SELECT * FROM history").fetchall() == [
                ("committed",)
            ]
        writer.rollback()
    phases = [line.split()[2] for line in calls if line.startswith("docker ")]
    assert phases == ["config", "build", "run", "ps", "exec", "cp", "up", "ps"]
    assert any("up -d --force-recreate" in line for line in calls)
    evidence("wal", dict(status="passed", phases=phases, integrity="ok", rows=1))


@pytest.mark.parametrize("phase", ["git", "config", "build", "settings", "backup"])
def test_failed_preparation_keeps_existing_service_and_env(tmp_path, phase):
    database = tmp_path / "running.sqlite3"
    database.write_bytes(b"invalid SQLite" if phase == "backup" else b"")
    result, calls = run_deploy(tmp_path, phase=phase)
    assert result.returncode != 0
    assert not any("compose up " in line for line in calls)
    assert not any("compose cp " in line for line in calls)
    evidence(phase, dict(status="passed", service_recreated=False, env_unchanged=True))


def test_first_deployment_builds_without_attempting_backup(tmp_path):
    result, calls = run_deploy(tmp_path, running=False)
    assert result.returncode == 0, result.stderr
    assert not any("compose exec " in line or "compose cp " in line for line in calls)
    assert any("up -d --force-recreate" in line for line in calls)
    evidence("first-install", dict(status="passed", backup_attempted=False))


def test_current_alerts_exclude_removed_rules_and_preserve_history(tmp_path):
    client, _, _, storage, _, telegram = make_system(tmp_path)
    now = datetime.now(UTC)
    server = storage.upsert_resource(
        provider_id="1",
        resource_type="server",
        name="Existing production server",
        parent_provider_id=None,
        raw={"id": "1", "cloud": "do"},
        discovered_at=now,
    )
    for rule, status in [
        ("cpu_percent", "active"),
        ("ram_percent", "pending"),
        ("disk_percent", "active"),
        ("Idle CPU", "active"),
        ("Free memory", "pending"),
    ]:
        storage.save_alert_state(
            resource_id=server,
            rule_key=rule,
            status=status,
            severity="warning",
            consecutive_breaches=3,
            opened_at=now,
            resolved_at=None,
            last_notification_at=None,
        )
    storage.insert_alert_event(
        resource_id=server,
        rule_key="cpu_percent",
        event_type="opened",
        severity="warning",
        message="Historical alert",
        created_at=now,
    )
    login(client)
    overview = client.get("/api/overview").json()
    assert overview["attention"]["active_alert_count"] == 1
    assert {a["rule_key"] for a in overview["active_alerts"]} == {"Idle CPU"}
    assert {a["rule_key"] for a in overview["servers"][0]["alerts"]} == {
        "Idle CPU",
        "Free memory",
    }
    assert {a["rule_key"] for a in client.get("/api/alerts").json()["alerts"]} == {
        "Idle CPU",
        "Free memory",
    }
    assert len(client.get("/api/alerts?status=active").json()["alerts"]) == 1
    assert client.get("/api/alerts/events").json()["events"][0]["rule_key"] == (
        "cpu_percent"
    )
    assert len(storage.list_alert_states()) == 5
    assert telegram == []
    evidence("alerts", dict(status="passed", active_alerts=1, retained_states=5))
