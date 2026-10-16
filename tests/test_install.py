import os
import shutil
import subprocess
import sqlite3
import tempfile
from pathlib import Path


def run_install(env: dict) -> subprocess.CompletedProcess:
    """Run the install script with a given environment mapping.
    Returns the CompletedProcess object.
    """
    script_path = Path(__file__).parents[1] / "scripts" / "install.sh"
    # Ensure script is executable
    os.chmod(script_path, 0o755)
    return subprocess.run(
        [str(script_path)],
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def test_install_idempotent(tmp_path: Path):
    # Setup temporary directories/files to avoid touching real system state.
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    db_path = tmp_path / "daily-briefing.db"
    # Create a minimal SQLite DB file.
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE foo(id INTEGER PRIMARY KEY);")
    conn.commit()
    conn.close()

    venv_dir = tmp_path / "venv"
    systemd_dir = tmp_path / "systemd"
    cron_file = tmp_path / "crontab.txt"

    # Create a dummy requirements file.
    req_file = Path(__file__).parents[1] / "requirements.txt"
    if not req_file.is_file():
        # Fallback – create an empty temporary one.
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("")

    env = {
        "DB_PATH": str(db_path),
        "BACKUP_DIR": str(backup_dir),
        "VENV_DIR": str(venv_dir),
        "SYSTEMD_DIR": str(systemd_dir),
        "CRONTAB_FILE": str(cron_file),
        "REQ_FILE": str(req_file),
    }

    # First run – should succeed and create expected artefacts.
    result1 = run_install(env)
    assert result1.returncode == 0, f"Install failed: {result1.stdout}"

    # Verify backup created.
    backups = list(backup_dir.glob("daily-briefing-*.db"))
    assert len(backups) == 1, "Expected one backup file"

    # Verify virtualenv created.
    assert (venv_dir / "bin" / "python").exists()

    # Verify systemd service file written.
    service_path = systemd_dir / "daily-briefing.service"
    assert service_path.read_text().strip().startswith("[Unit]"), "Service file missing"

    # Verify crontab line added exactly once.
    crontab_lines = cron_file.read_text().splitlines()
    assert len([l for l in crontab_lines if "python -m app.job" in l]) == 1

    # Second run – should be idempotent (no duplicate crontab line, backup count stays 1).
    result2 = run_install(env)
    assert result2.returncode == 0, f"Second install failed: {result2.stdout}"
    # No extra backup beyond the latest one (script keeps only newest 10, but we started with none).
    backups2 = list(backup_dir.glob("daily-briefing-*.db"))
    assert len(backups2) == 2, "Second run should create a new backup"
    # Crontab line still only one.
    crontab_lines2 = cron_file.read_text().splitlines()
    assert len([l for l in crontab_lines2 if "python -m app.job" in l]) == 1

