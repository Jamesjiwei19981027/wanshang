"""Read-only compatibility check of operations against the complete Compose stack."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("output/production-operations-2026-10-25"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    deploy = root / "deploy"
    compose = deploy / "compose.production.yml"
    values = {}
    for raw in (deploy / "env.production.example").read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    values.update({"DEPLOY_PROJECT_NAME": "axle-ops-interface-check", "SITE_DOMAIN": "axle.wan-shang.com",
                   "BACKEND_IMAGE": "axle-api:interface-fixture", "FRONTEND_IMAGE": "axle-web:interface-fixture", "NGINX_IMAGE": "axle-nginx:interface-fixture",
                   "DJANGO_SECRET_KEY": "synthetic-interface-only-" + "x" * 64,
                   "POSTGRES_PASSWORD": "synthetic-admin-only-" + "a" * 40, "APP_DB_PASSWORD": "synthetic-app-only-" + "b" * 40,
                   "REDIS_PASSWORD": "synthetic-cache-only-" + "c" * 40, "EMAIL_HOST": "smtp.example.com", "EMAIL_HOST_USER": "test@example.com",
                   "EMAIL_HOST_PASSWORD": "synthetic-interface-only", "DEFAULT_FROM_EMAIL": "test@example.com", "SALES_NOTIFICATION_EMAIL": "test@example.com",
                   "TURNSTILE_SECRET_KEY": "synthetic-interface-only", "NEXT_PUBLIC_TURNSTILE_SITE_KEY": "synthetic-public-interface-only"})
    # Compose interpolation never inherits real values for referenced fields.
    environment = os.environ.copy()
    for key in set(values) | set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)", compose.read_text())):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="compose-fixture-", dir=output) as directory:
        fixture = Path(directory).resolve()
        if not fixture.is_relative_to(output):
            raise RuntimeError("Fixture path escaped output directory")
        environment_file = fixture / "synthetic.env"
        environment_file.write_text("\n".join(key + "=" + value for key, value in values.items()) + "\n")
        environment_file.chmod(0o600)
        command = ["docker", "compose", "--project-name", values["DEPLOY_PROJECT_NAME"], "--project-directory", str(deploy), "--env-file", str(environment_file),
                   "-f", str(compose), "--profile", "ops", "config", "--format", "json"]
        completed = subprocess.run(command, env=environment, text=True, capture_output=True, timeout=30)
        if completed.returncode:
            raise SystemExit("Synthetic Compose config did not validate")
        config = json.loads(completed.stdout)
    services = config["services"]
    manage_mounts = {mount["target"]: mount for mount in services["manage"]["volumes"]}
    checks = {
        "all_operations_services_present": all(name in services for name in ("postgres", "redis", "clamav", "volume-init", "manage", "api", "celery", "frontend", "nginx")),
        "manage_uses_backend_image": services["manage"]["image"] == services["api"]["image"],
        "manage_python_entrypoint": services["manage"]["entrypoint"] == ["/app/.venv/bin/python", "manage.py"],
        "manage_profile_is_ops": services["manage"]["profiles"] == ["ops"],
        "manage_media_and_static_volumes_writable": all(target in manage_mounts and not manage_mounts[target].get("read_only", False) for target in ("/app/media", "/app/private_media", "/app/staticfiles")),
        "application_role_components_consistent": services["api"]["environment"]["DJANGO_DB_USER"] == services["postgres"]["environment"]["APP_DB_USER"] and services["api"]["environment"]["DJANGO_DB_PASSWORD"] == services["postgres"]["environment"]["APP_DB_PASSWORD"],
        "infrastructure_not_published": all(not services[name].get("ports") for name in ("postgres", "redis", "clamav")),
        "named_volumes_project_scoped": all(volume.get("name", "").startswith(values["DEPLOY_PROJECT_NAME"] + "_") and not volume.get("external") for volume in config["volumes"].values()),
        "volume_initializer_is_network_isolated": services["volume-init"]["network_mode"] == "none",
        "ingress_has_no_private_media_mount": all(mount["target"] not in ("/app/private_media", "/srv/private_media") and mount.get("source") != "private_media" for mount in services["nginx"]["volumes"]),
        "postgres_bootstrap_hook_readonly": any(mount["target"] == "/docker-entrypoint-initdb.d/20-app-role.sh" and mount.get("read_only") for mount in services["postgres"]["volumes"]),
        "nginx_local_build_included": "nginx" in services and services["nginx"].get("build", {}).get("dockerfile") == "Dockerfile",
    }
    result = {"passed": all(checks.values()), "checks": checks, "services": list(services),
              "synthetic_credentials_only": True, "mode": "compose config only; no containers or servers started"}
    (output / "compose-operations-interface.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
