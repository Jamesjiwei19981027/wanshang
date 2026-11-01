"""Verify production-role bootstrap in a disposable, network-isolated PostgreSQL."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid

IMAGE = "postgres:16.15-alpine@sha256:f4610795587492cd4fb887c2dc3de301b813278bb6f616992061141b5f15ac36"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/production-operations-2026-10-25"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    nonce = uuid.uuid4().hex[:12]
    name = "axle-ops-role-check-" + nonce
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "image": IMAGE,
              "temporary_container": name, "business_data_touched": False, "network": "none", "passed": False}
    created = False
    volumes = []
    admin_password = secrets.token_urlsafe(36)
    app_password = "test-only-'\\:/# " + secrets.token_urlsafe(36)

    def run(arguments, *, check=True):
        completed = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=45)
        if check and completed.returncode:
            raise RuntimeError("Docker operation failed: " + arguments[0])
        return completed

    def sql(command, *, app=False, database=None, check=True):
        user = '"$APP_DB_USER"' if app else '"$POSTGRES_USER"'
        password = '"$APP_DB_PASSWORD"' if app else '"$POSTGRES_PASSWORD"'
        db = database or '"$POSTGRES_DB"'
        return run(["exec", name, "sh", "-ceu", "export PGPASSWORD=" + password + "; exec psql -X -q -At -v ON_ERROR_STOP=1 -h 127.0.0.1 -U " + user + " -d " + db + " -c '" + command + "'"], check=check)

    try:
        with tempfile.TemporaryDirectory(prefix="role-fixture-", dir=output) as directory:
            fixture = Path(directory).resolve()
            if not fixture.is_relative_to(output):
                raise RuntimeError("Temporary credential directory escaped its output root")
            environment = fixture / "test.env"
            environment.write_text("\n".join(("POSTGRES_DB=axle_ops_test", "POSTGRES_USER=axle_init_admin", "POSTGRES_PASSWORD=" + admin_password,
                                               "APP_DB_USER=axle_test_app", "APP_DB_PASSWORD=" + app_password)) + "\n", encoding="utf-8")
            os.chmod(environment, 0o600)
            hook = (root / "deploy/postgres/init-app-role.sh").resolve()
            run(["run", "--detach", "--rm", "--name", name, "--label", "axle.ops.test=" + nonce, "--network", "none", "--env-file", str(environment),
                 "--mount", "type=bind,source=" + str(hook) + ",target=/docker-entrypoint-initdb.d/20-app-role.sh,readonly", IMAGE])
            created = True
            for _ in range(60):
                ready = sql("SELECT 1", check=False)
                if ready.returncode == 0 and ready.stdout.strip() == "1":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("PostgreSQL fixture did not become ready")
            inspection = json.loads(run(["inspect", name]).stdout)[0]
            volumes = [mount["Name"] for mount in inspection["Mounts"] if mount["Type"] == "volume"]
            result["no_host_ports"] = not any((inspection["NetworkSettings"].get("Ports") or {}).values())
            result["application_role_restricted"] = sql("SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user", app=True).stdout.strip() == "t"
            result["app_ddl_and_dml_work"] = sql("CREATE TABLE public.role_acceptance(id integer PRIMARY KEY); INSERT INTO public.role_acceptance VALUES(1); SELECT count(*) FROM public.role_acceptance", app=True).stdout.strip() == "1"
            result["quoted_password_authentication"] = True
            result["other_database_connection_denied"] = sql("SELECT 1", app=True, database="postgres", check=False).returncode != 0
            result["create_database_denied"] = sql("CREATE DATABASE axle_should_not_exist", app=True, check=False).returncode != 0
            result["create_role_denied"] = sql("CREATE ROLE axle_should_not_exist", app=True, check=False).returncode != 0
            dump = subprocess.run(["docker", "exec", name, "sh", "-ceu", 'export PGPASSWORD="$APP_DB_PASSWORD"; exec pg_dump --format=custom --no-owner --no-privileges -h 127.0.0.1 -U "$APP_DB_USER" -d "$POSTGRES_DB"'], capture_output=True, timeout=45)
            if dump.returncode or not dump.stdout.startswith(b"PGDMP"):
                raise RuntimeError("Application-role fixture dump failed")
            sql("DROP TABLE public.role_acceptance", app=True)
            restored = subprocess.run(["docker", "exec", "--interactive", name, "sh", "-ceu", 'export PGPASSWORD="$APP_DB_PASSWORD"; exec pg_restore --exit-on-error --no-owner --no-privileges --role "$APP_DB_USER" -h 127.0.0.1 -U "$APP_DB_USER" -d "$POSTGRES_DB"'], input=dump.stdout, capture_output=True, timeout=45)
            result["custom_dump_restored_as_business_role"] = restored.returncode == 0 and sql("SELECT count(*) FROM public.role_acceptance", app=True).stdout.strip() == "1"
            result["passed"] = all(result[key] for key in ("no_host_ports", "application_role_restricted", "app_ddl_and_dml_work", "quoted_password_authentication", "other_database_connection_denied", "create_database_denied", "create_role_denied", "custom_dump_restored_as_business_role"))
    except Exception as error:
        result["error_type"] = type(error).__name__
        result["error"] = str(error).replace(admin_password, "[REDACTED]").replace(app_password, "[REDACTED]")
        if created:
            logs = run(["logs", name], check=False)
            sanitized = (logs.stdout + logs.stderr).replace(admin_password, "[REDACTED]").replace(app_password, "[REDACTED]")
            (output / "postgres-bootstrap-failure.log").write_text(sanitized, encoding="utf-8")
    finally:
        if created:
            inspection = run(["inspect", name], check=False)
            if inspection.returncode == 0:
                metadata = json.loads(inspection.stdout)[0]
                if metadata["Config"].get("Labels", {}).get("axle.ops.test") != nonce:
                    raise RuntimeError("Refusing to clean a container without the exact test ownership label")
                volumes = [mount["Name"] for mount in metadata["Mounts"] if mount["Type"] == "volume"]
                run(["stop", "--time", "3", name])
            result["container_removed"] = run(["inspect", name], check=False).returncode != 0
            result["anonymous_volumes_removed"] = all(run(["volume", "inspect", volume], check=False).returncode != 0 for volume in volumes)
            result["passed"] = result["passed"] and result["container_removed"] and result["anonymous_volumes_removed"]
        (output / "postgres-role-check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
