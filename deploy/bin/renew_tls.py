"""Renew the public certificate in Docker, export it, and reload Nginx."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import tempfile
import time

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
from configure import certificate_covers, read_env, resolve_deploy_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    values = read_env(args.env_file)
    project = values["DEPLOY_PROJECT_NAME"]
    if not re.fullmatch(r"axle-[a-z0-9][a-z0-9_-]{1,48}", project):
        raise SystemExit("Invalid deployment project name")
    state = args.state_dir.resolve(strict=True)
    image = (state / "certbot-image").read_text().strip()
    if not re.fullmatch(r"(?:docker.io/)?certbot/certbot@sha256:[0-9a-f]{64}", image):
        raise SystemExit("Use a pinned official Certbot image in state-dir/certbot-image")
    lock_dir = DEPLOY / ".locks"
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    with (lock_dir / (project + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        command = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"]
        for source, target in (("letsencrypt", "/etc/letsencrypt"),
                               ("letsencrypt-work", "/var/lib/letsencrypt"),
                               ("letsencrypt-logs", "/var/log/letsencrypt")):
            command += ["--volume", f"{state / source}:{target}"]
        webroot = resolve_deploy_path(values.get("ACME_WEBROOT", "./acme-webroot")).resolve(strict=True)
        command += ["--volume", f"{webroot}:/var/www/acme", image, "renew",
                    "--cert-name", values["SITE_DOMAIN"], "--webroot",
                    "--webroot-path", "/var/www/acme", "--non-interactive"]
        if args.dry_run:
            command.append("--dry-run")
        subprocess.run(command, check=True, timeout=600)
        if args.dry_run:
            print(json.dumps({"renewal_dry_run": "passed", "production_certificate_changed": False}))
            return
        source = state / "letsencrypt" / "live" / values["SITE_DOMAIN"]
        cert, key = source / "fullchain.pem", source / "privkey.pem"
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
        metadata = ssl._ssl._test_decode_cert(str(cert))
        names = [name for kind, name in metadata.get("subjectAltName", ()) if kind == "DNS"]
        if not certificate_covers(values["SITE_DOMAIN"], names):
            raise SystemExit("Renewed certificate does not cover the deployment hostname")
        if not ssl.cert_time_to_seconds(metadata["notBefore"]) <= time.time() < ssl.cert_time_to_seconds(metadata["notAfter"]):
            raise SystemExit("Renewed certificate is not currently valid")
        destination = resolve_deploy_path(values["TLS_CERT_DIR"]).resolve(strict=True)
        unchanged = all((destination / name).is_file() and
                        hashlib.sha256((destination / name).read_bytes()).digest() ==
                        hashlib.sha256((source / name).read_bytes()).digest()
                        for name in ("fullchain.pem", "privkey.pem"))
        reload_marker = state / "tls-reload-required"
        if not unchanged:
            reload_marker.touch(mode=0o600)
            for name in ("privkey.pem", "fullchain.pem"):
                with tempfile.NamedTemporaryFile(dir=destination, prefix=".renew-", delete=False) as temporary:
                    temporary.write((source / name).read_bytes())
                    temporary_path = Path(temporary.name)
                try:
                    temporary_path.chmod(0o600 if name == "privkey.pem" else 0o644)
                    temporary_path.replace(destination / name)
                finally:
                    temporary_path.unlink(missing_ok=True)
        if reload_marker.exists():
            compose = ["docker", "compose", "--project-name", project, "--project-directory", str(DEPLOY),
                       "--env-file", str(args.env_file.resolve()), "-f", str(DEPLOY / "compose.production.yml"),
                       "exec", "-T", "nginx", "nginx"]
            subprocess.run(compose + ["-t"], check=True, timeout=30)
            subprocess.run(compose + ["-s", "reload"], check=True, timeout=30)
            reload_marker.unlink()
        print(json.dumps({"renewal_check": "passed", "certificate_updated": not unchanged,
                          "certificate_expires": metadata["notAfter"]}))


if __name__ == "__main__":
    main()
