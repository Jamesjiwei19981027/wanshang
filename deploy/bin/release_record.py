"""Record image identities and backup association without inspecting environment secrets."""
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile


def images(project):
    identifiers = subprocess.check_output(["docker", "ps", "--all", "--quiet", "--filter", "label=com.docker.compose.project=" + project], text=True).split()
    if not identifiers:
        return []
    template = '{"container":{{json .Name}},"service":{{json (index .Config.Labels "com.docker.compose.service")}},"image_reference":{{json .Config.Image}},"image_id":{{json .Image}},"status":{{json .State.Status}}}'
    output = subprocess.check_output(["docker", "inspect", "--format", template, *identifiers], text=True)
    return [json.loads(line) for line in output.splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepared", "healthy", "failed-before-migrate", "failed-after-infrastructure", "failed-after-migrate"), required=True)
    parser.add_argument("--backup", default="")
    args = parser.parse_args()
    destination = args.output
    release_root = Path(__file__).resolve().parents[1] / "releases"
    if destination.is_symlink() or not destination.parent.resolve().is_relative_to(release_root.resolve()):
        raise SystemExit("Release records must remain inside deploy/releases")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(release_root, 0o700)
    os.chmod(destination.parent, 0o700)
    if destination.exists():
        info = destination.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SystemExit("Release record must be a regular file without links")
        record = json.loads(destination.read_text())
        if record.get("format") != "axle-deployment-record-v1" or record.get("project") != args.project:
            raise SystemExit("Refusing to replace an unrelated release record")
    else:
        record = {"format": "axle-deployment-record-v1", "project": args.project, "events": []}
    try:
        revision = subprocess.check_output(["git", "-C", str(args.repository), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "-C", str(args.repository), "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = "unavailable", None
    record["events"].append({"at_utc": datetime.now(timezone.utc).isoformat(), "phase": args.phase,
                             "repository_revision": revision, "worktree_dirty": dirty,
                             "backup_directory": args.backup or None, "container_images": images(args.project)})
    record["status"] = args.phase
    with tempfile.NamedTemporaryFile("w", dir=destination.parent, prefix=".release-", encoding="utf-8", delete=False) as output:
        temporary = Path(output.name)
        json.dump(record, output, indent=2)
        output.write("\n")
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
