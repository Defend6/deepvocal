"""Small tracked-file guard; complementary to review, not a complete secret scanner."""
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
errors = []
allowed_roots = {"apps", "infra", "scripts", ".github"}
allowed_files = {
    ".gitignore", ".dockerignore", ".env.example", "README.md", "contracts/runtime-openapi.json",
}
for name in filter(None, paths):
    path = Path(name)
    if path.parts[0] not in allowed_roots and name not in allowed_files:
        errors.append(f"file outside code publication boundary: {name}")
    if path.name == ".env" or name.startswith(("backups/", "uploads/", "datasets/indexes/", "datasets/working/")):
        errors.append(f"private runtime path tracked: {name}")
    source = root / name
    if not source.is_file() or source.stat().st_size > 2_000_000:
        continue
    content = source.read_bytes()
    if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9]{40,}", content):
        errors.append(f"possible credential in {name}")
if errors:
    raise SystemExit("\n".join(errors))
print("Tracked-file path and credential-pattern checks passed (limited heuristic).")
