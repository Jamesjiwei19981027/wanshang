"""Prepare private production configuration without changing development settings.

Only init --import-existing reads the existing local mail/Turnstile settings.
It never copies development database credentials or the example Django secret.
check starts no service and prints no credential values.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = HERE / "env.production.example"
DEFAULT_ENV = HERE / ".env.production"
PRIVATE_FIELDS = {"DJANGO_SECRET_KEY", "POSTGRES_PASSWORD", "APP_DB_PASSWORD", "REDIS_PASSWORD", "EMAIL_HOST_PASSWORD", "TURNSTILE_SECRET_KEY"}
IMPORT_FIELDS = {"EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "EMAIL_USE_TLS", "EMAIL_USE_SSL", "EMAIL_TIMEOUT", "DEFAULT_FROM_EMAIL", "SALES_NOTIFICATION_EMAIL", "TURNSTILE_SECRET_KEY", "NEXT_PUBLIC_TURNSTILE_SITE_KEY"}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("An environment entry is missing its equals sign.")
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in values:
            raise ValueError("Invalid or duplicate environment name.")
        if value.startswith("'"):
            if not value.endswith("'") or len(value) < 2:
                raise ValueError("An environment value has unbalanced quotes.")
            value = value[1:-1].replace("\\'", "'")
        elif value.startswith('"'):
            if not value.endswith('"') or len(value) < 2:
                raise ValueError("An environment value has unbalanced quotes.")
            value = value[1:-1]
        if any(character in value for character in ("\x00", "\r", "\n")):
            raise ValueError("Multiline environment values are not supported.")
        values[name] = value
    return values


def public_hostname(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return False
    except ValueError:
        pass
    labels = value.split(".")
    return (
        len(value) <= 253 and len(labels) >= 2
        and not re.fullmatch(r"(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*", value, re.I)
        and labels[-1].lower() not in {"localhost", "local", "internal", "test", "invalid"}
        and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    )


def quoted(value: str) -> str:
    if any(character in value for character in ("\x00", "\r", "\n")) or value.endswith("\\"):
        raise ValueError("Environment values must be single-line and cannot end in a backslash.")
    return "'" + value.replace("'", "\\'") + "'"


def init_config(path: Path, domain: str, import_existing: bool = False) -> dict:
    domain = domain.lower().strip()
    if not public_hostname(domain):
        raise ValueError("Use one public hostname without a scheme, wildcard, port or path.")
    if path.exists() or path.is_symlink():
        raise ValueError("Configuration already exists; initialization never overwrites it.")
    values = read_env(TEMPLATE)
    release = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    values.update({
        "SITE_DOMAIN": domain,
        "BACKEND_IMAGE": "axle-api:" + release,
        "FRONTEND_IMAGE": "axle-web:" + release,
        "NGINX_IMAGE": "axle-nginx:" + release,
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "APP_DB_PASSWORD": secrets.token_hex(32),
        "REDIS_PASSWORD": secrets.token_hex(32),
    })
    imported: set[str] = set()
    if import_existing:
        for source in (ROOT / ".env", ROOT / ".env.production", ROOT / "frontend/.env.production.local"):
            if not source.is_file():
                continue
            for name, value in read_env(source).items():
                if name in IMPORT_FIELDS and value:
                    values[name] = value
                    imported.add(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "# Private production configuration. Keep mode 0600 and never commit.\n"
    body += "# Paths resolve relative to the repository deploy/ directory.\n"
    body += "\n".join(name + "=" + quoted(value) for name, value in values.items()) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
        target.write(body)
    return {"created": str(path), "imported_setting_names": sorted(imported), "secret_values_printed": False}


def resolve_deploy_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else HERE / path


def certificate_covers(domain: str, names: list[str]) -> bool:
    return any(name.lower() == domain or (name.startswith("*.") and domain.partition(".")[2] == name[2:].lower()) for name in names)


def validate_config(values: dict[str, str], check_tls: bool = True) -> list[str]:
    errors: list[str] = []
    required = ("DEPLOY_PROJECT_NAME", "SITE_DOMAIN", "BACKEND_IMAGE", "FRONTEND_IMAGE", "CLAMAV_IMAGE", "NGINX_IMAGE", "NGINX_BASE_IMAGE", "POSTGRES_DB", "POSTGRES_USER", "APP_DB_USER", "NEXT_PUBLIC_TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY", "EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL", "SALES_NOTIFICATION_EMAIL", "TLS_CERT_DIR", "CLOUDFLARE_REALIP_CONFIG")
    for name in required:
        if not values.get(name, "").strip():
            errors.append(name + " is required.")
    domain = values.get("SITE_DOMAIN", "")
    if not public_hostname(domain):
        errors.append("SITE_DOMAIN must be a public hostname.")
    if not re.fullmatch(r"axle-[a-z0-9][a-z0-9_-]{1,48}", values.get("DEPLOY_PROJECT_NAME", "")):
        errors.append("DEPLOY_PROJECT_NAME must use the dedicated axle-* namespace.")
    for name in ("POSTGRES_DB", "POSTGRES_USER", "APP_DB_USER"):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", values.get(name, "")):
            errors.append(name + " must be a safe PostgreSQL identifier.")
    for name in ("POSTGRES_USER", "APP_DB_USER"):
        if values.get(name, "").startswith("pg_"):
            errors.append(name + " must not use PostgreSQL's reserved pg_ role prefix.")
    if values.get("POSTGRES_USER") == values.get("APP_DB_USER"):
        errors.append("Initialization and application database roles must differ.")
    for name, length in (("DJANGO_SECRET_KEY", 50), ("POSTGRES_PASSWORD", 32), ("APP_DB_PASSWORD", 32), ("REDIS_PASSWORD", 32)):
        value = values.get(name, "")
        if len(value) < length or any(marker in value.lower() for marker in ("change-me", "replace-this", "example", "placeholder")):
            errors.append(name + " requires an independently generated production secret.")
    if len({values.get(name) for name in ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "REDIS_PASSWORD")}) != 3:
        errors.append("Database and Redis credentials must be distinct.")
    for name in ("CLAMAV_IMAGE", "NGINX_BASE_IMAGE"):
        if not re.fullmatch(r"[a-zA-Z0-9._/:+-]+@sha256:[0-9a-f]{64}", values.get(name, "")):
            errors.append(name + " must be pinned to an image digest.")
    for name in ("BACKEND_IMAGE", "FRONTEND_IMAGE", "NGINX_IMAGE"):
        image = values.get(name, "")
        tagged_name = image.rsplit("/", 1)[-1]
        if (not re.fullmatch(r"[a-zA-Z0-9._/:+-]+", image)
                or not re.fullmatch(r"[^:]+:[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", tagged_name)
                or tagged_name.endswith(":latest")):
            errors.append(name + " requires an explicit release image reference.")
    for name in ("EMAIL_PORT", "HTTP_PORT", "HTTPS_PORT"):
        raw = values.get(name, "")
        if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 65535:
            errors.append(name + " must be a port from 1 to 65535.")
    if values.get("HTTP_PORT") == values.get("HTTPS_PORT"):
        errors.append("HTTP_PORT and HTTPS_PORT must differ.")
    modes = [values.get(name, "").lower() for name in ("EMAIL_USE_SSL", "EMAIL_USE_TLS")]
    if any(mode not in {"true", "false"} for mode in modes) or modes.count("true") != 1:
        errors.append("SMTP requires exactly one of EMAIL_USE_SSL and EMAIL_USE_TLS.")
    try:
        timeout = float(values.get("EMAIL_TIMEOUT", ""))
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError
    except ValueError:
        errors.append("EMAIL_TIMEOUT must be finite, positive and at most 120 seconds.")
    for name in ("DEFAULT_FROM_EMAIL", "SALES_NOTIFICATION_EMAIL", "EMAIL_HOST_USER"):
        if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", values.get(name, "")):
            errors.append(name + " must be a complete public email address.")
    if values.get("TURNSTILE_SECRET_KEY", "").startswith(("1x000000", "2x000000", "3x000000")) or values.get("NEXT_PUBLIC_TURNSTILE_SITE_KEY", "").startswith(("1x000000", "2x000000", "3x000000")):
        errors.append("Production Turnstile credentials must not use public test keys.")
    for name in ("API_WORKERS", "API_CONCURRENCY", "CELERY_CONCURRENCY"):
        raw = values.get(name, "")
        if not raw.isascii() or not raw.isdecimal() or not 1 <= int(raw) <= 64:
            errors.append(name + " must be an integer from 1 to 64.")
    trust = resolve_deploy_path(values.get("CLOUDFLARE_REALIP_CONFIG", ""))
    if not trust.is_file():
        errors.append("CLOUDFLARE_REALIP_CONFIG is missing.")
    if check_tls:
        tls = resolve_deploy_path(values.get("TLS_CERT_DIR", ""))
        cert, key = tls / "fullchain.pem", tls / "privkey.pem"
        if not cert.is_file() or not key.is_file() or cert.is_symlink() or key.is_symlink():
            errors.append("TLS_CERT_DIR must contain regular fullchain.pem and privkey.pem files.")
        else:
            if os.name == "posix" and stat.S_IMODE(key.stat().st_mode) & 0o027:
                errors.append("The TLS private key must not be group-writable or accessible to other users.")
            try:
                ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
                metadata = ssl._ssl._test_decode_cert(str(cert))
                names = [name for kind, name in metadata.get("subjectAltName", ()) if kind == "DNS"]
                if not certificate_covers(domain, names):
                    errors.append("The TLS certificate does not cover SITE_DOMAIN.")
                now = datetime.now(timezone.utc).timestamp()
                if not ssl.cert_time_to_seconds(metadata["notBefore"]) <= now < ssl.cert_time_to_seconds(metadata["notAfter"]):
                    errors.append("The TLS certificate is not currently valid.")
            except (OSError, ValueError, ssl.SSLError, KeyError):
                errors.append("TLS certificate/key validation failed.")
    return errors


def docker_check(path: Path, values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.OSType}} {{.Architecture}}"], capture_output=True, text=True, timeout=20)
        if info.returncode or info.stdout.strip().split() not in (["linux", "x86_64"], ["linux", "amd64"]):
            return ["This pinned scanner baseline requires a Linux x86_64/amd64 Docker engine."]
        command = ["docker", "compose", "--project-name", values["DEPLOY_PROJECT_NAME"], "--project-directory", str(HERE), "--env-file", str(path), "-f", str(HERE / "compose.production.yml"), "--profile", "ops", "config", "--format", "json"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if result.returncode:
            return ["Compose configuration validation failed; credential-bearing output was suppressed."]
        services = json.loads(result.stdout)["services"]
        for name in ("postgres", "redis", "clamav", "api", "celery", "frontend"):
            if services[name].get("ports"):
                errors.append(name + " must not publish a host port.")
        environment = services["api"]["environment"]
        if str(environment.get("DJANGO_ENV")) != "production" or str(environment.get("DJANGO_DEBUG")).lower() != "false":
            errors.append("The rendered API must run in production with DEBUG disabled.")
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        errors.append("Docker/Compose preflight could not be completed.")
    return errors


def refresh_proxies() -> dict:
    request = Request("https://api.cloudflare.com/client/v4/ips", headers={"User-Agent": "Axle-Deployment-Config/1.0", "Accept": "application/json"})
    with urlopen(request, timeout=20) as response:
        payload = response.read(131073)
    if len(payload) > 131072:
        raise ValueError("The official IP response exceeds its size bound.")
    response = json.loads(payload)
    if response.get("success") is not True:
        raise ValueError("Cloudflare did not return a successful public IP list.")
    networks = []
    for family in (4, 6):
        records = response["result"]["ipv" + str(family) + "_cidrs"]
        if not 2 <= len(records) <= 1024:
            raise ValueError("The official IP list has an unexpected record count.")
        for value in records:
            network = ipaddress.ip_network(value, strict=True)
            if network.version != family or not network.is_global or network.prefixlen < (8 if family == 4 else 16):
                raise ValueError("The official IP list contains an unsafe network range.")
            networks.append(network)
    target = HERE / "nginx/cloudflare-realip.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "# Official Cloudflare IP ranges; refreshed " + datetime.now(timezone.utc).isoformat() + "\n"
    content += "# Source: https://api.cloudflare.com/client/v4/ips\n"
    content += "".join("set_real_ip_from " + str(network) + ";\n" for network in networks)
    content += "real_ip_header CF-Connecting-IP;\nreal_ip_recursive off;\n"
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=".realip-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"official_cloudflare_ranges_saved": len(networks), "path": str(target)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init", help="Create a new private deployment file; never overwrite.")
    initialize.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    initialize.add_argument("--domain", default="axle.wan-shang.com")
    initialize.add_argument("--import-existing", action="store_true")
    check = sub.add_parser("check", help="Read-only production preflight; never starts services.")
    check.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    check.add_argument("--skip-tls", action="store_true", help="Preparation only; deployment scripts do not use this flag.")
    check.add_argument("--no-docker", action="store_true", help="Offline configuration tests only.")
    sub.add_parser("refresh-proxies", help="Atomically refresh trusted ranges from the official Cloudflare API.")
    args = parser.parse_args()
    try:
        if args.command == "init":
            print(json.dumps(init_config(args.env_file, args.domain, args.import_existing)))
        elif args.command == "refresh-proxies":
            print(json.dumps(refresh_proxies()))
        else:
            path = args.env_file
            if not path.is_file() or path.is_symlink():
                raise ValueError("The deployment environment file is missing or is a symbolic link.")
            values = read_env(path)
            errors = validate_config(values, not args.skip_tls)
            if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                errors.append("The deployment environment file must have mode 0600 or stricter.")
            docker_checked = False
            if not args.no_docker and not errors:
                docker_checked = True
                errors.extend(docker_check(path.resolve(), values))
            print(json.dumps({"passed": not errors, "tls_checked": not args.skip_tls, "docker_checked": docker_checked, "errors": errors, "secret_values_printed": False}))
            return 1 if errors else 0
    except (OSError, ValueError, KeyError) as error:
        print("Configuration operation failed: " + (str(error) if isinstance(error, ValueError) else type(error).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
