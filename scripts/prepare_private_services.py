"""Generate local service credentials without logging them or changing model billing."""
import re
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
content = path.read_text(encoding="utf-8-sig") if path.exists() else (root / ".env.example").read_text(encoding="utf-8")
for key in ("SINGING_SERVICE_TOKEN", "METRICS_TOKEN", "IDENTITY_ADMIN_PASSWORD"):
    pattern = rf"(?m)^{key}=(.*)$"
    match = re.search(pattern, content)
    if match and match.group(1).strip():
        continue
    line = f"{key}={secrets.token_urlsafe(48)}"
    content = re.sub(pattern, lambda _: line, content) if match else content + "\n" + line + "\n"
path.write_text(content, encoding="utf-8")
print("Private local service credentials prepared in the ignored .env file.")
