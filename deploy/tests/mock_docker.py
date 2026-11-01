#!/usr/bin/env python3
"""Fixture Docker command; no real containers/process servers are created."""
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["AXLE_TEST_STATE"])
log_path = Path(os.environ["AXLE_TEST_LOG"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]


def save(call):
    with log_path.open("a") as output:
        output.write(json.dumps(call) + "\n")
    state_path.write_text(json.dumps(state))


if args[0] == "info":
    print("linux")
    raise SystemExit(0)
if args[0] == "inspect":
    print(json.dumps({"container": "/fixture-api", "service": "api", "image_reference": "fixture:release", "image_id": "sha256:controlled-fixture", "status": "running"}))
    raise SystemExit(0)
if args[0] in ("ps", "volume", "network"):
    save({"query": args[0]})
    if "label=com.docker.compose.oneoff=True" in args:
        raise SystemExit(0)
    if state["existing"]:
        print("existing-fixture-resource")
    raise SystemExit(0)
if args[0] != "compose":
    raise SystemExit("Unexpected fake Docker operation")
args = args[1:]
while args and args[0].startswith("-"):
    if args[0] not in ("--project-name", "--project-directory", "--env-file", "-f", "--profile"):
        raise SystemExit("Unexpected fake compose option")
    args = args[2:]
command, args = args[0], args[1:]
call = {"command": command}
if command == "version":
    print("Docker Compose fixture")
elif command == "ps":
    if "--services" in args:
        print("\n".join(state["running"]))
    elif state["existing"]:
        print("fixture-postgres")
elif command in ("start", "stop"):
    services = [arg for arg in args if arg in ("postgres", "redis", "clamav", "nginx", "frontend", "api", "celery")]
    call["services"] = services
    state["running"] = sorted(set(state["running"]) | set(services) if command == "start" else set(state["running"]) - set(services))
elif command == "build":
    call["services"] = args
elif command == "up":
    services = [arg for arg in args if arg in ("postgres", "redis", "clamav", "nginx", "frontend", "api", "celery")]
    call["services"] = services
    state["existing"] = True
    state["running"] = sorted(set(state["running"]) | set(services))
    if "redis" in services and "clamav" in services and os.environ.get("AXLE_TEST_FAIL_INFRASTRUCTURE"):
        save(call)
        raise SystemExit(45)
elif command == "exec":
    text = args[-1]
    if "pg_dump" in text:
        call["operation"] = "pg_dump"
        save(call)
        if os.environ.get("AXLE_TEST_FAIL_DUMP"):
            raise SystemExit(42)
        sys.stdout.buffer.write(b"PGDMP-controlled-test-fixture")
        raise SystemExit(0)
    if "pg_isready" in text:
        pass
    elif "SELECT count(*) FROM pg_class" in text:
        print(state["db_tables"])
    elif "pg_restore" in text:
        call["operation"] = "pg_restore"
        assert sys.stdin.buffer.read().startswith(b"PGDMP")
        state["restored"] = True
    else:
        raise SystemExit("Unexpected fake SQL command")
elif command == "run":
    if "-c" in args:
        operation = args[args.index("-c") + 2:]
        if operation and operation[0] in ("dump", "restore", "check-empty"):
            sys.path.insert(0, os.environ["AXLE_TEST_BIN"])
            import media_archive
            roots = {"public_media": Path(state["public"]), "private_media": Path(state["private"])}
            call["operation"] = "media-" + operation[0]
            save(call)
            if operation[0] == "dump":
                media_archive.dump(sys.stdout.buffer, roots)
            elif operation[0] == "check-empty":
                media_archive.check_roots(roots, empty=True)
            else:
                print(json.dumps(media_archive.restore(sys.stdin.buffer, roots)))
            raise SystemExit(0)
        call["operation"] = "migration_guard"
        if os.environ.get("AXLE_TEST_FAIL_GUARD"):
            save(call)
            raise SystemExit(44)
        print('{"migration_history_known": true}')
    elif "migrate" in args:
        call["operation"] = "migrate"
        call["writers_running"] = sorted(set(state["running"]) & {"nginx", "frontend", "api", "celery"})
        save(call)
        if os.environ.get("AXLE_TEST_FAIL_MIGRATE"):
            raise SystemExit(43)
        state["migrated"] = True
        state_path.write_text(json.dumps(state))
        raise SystemExit(0)
    else:
        call["operation"] = "manage-check-or-collectstatic"
else:
    raise SystemExit("Unexpected fake Compose operation")
save(call)
