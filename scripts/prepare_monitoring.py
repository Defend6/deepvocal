"""Render a local monitoring credential file, never put its value in the repository."""
import os
from pathlib import Path
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = {**dotenv_values(root / ".env"), **os.environ}
token = config.get("METRICS_TOKEN", "")
if len(token) < 32:
    raise SystemExit("Set METRICS_TOKEN to a private value of at least 32 characters")
destination = root / "runtime/monitoring"
destination.mkdir(parents=True, exist_ok=True)
(destination / "token").write_text(token, encoding="utf-8")
(destination / "prometheus.yml").write_text("""global:
  scrape_interval: 15s
rule_files: [/etc/prometheus/alerts.yml]
scrape_configs:
  - job_name: deepvocal
    metrics_path: /internal/metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/metrics_token
    static_configs:
      - targets: ['api:8000']
""", encoding="utf-8")
print("Monitoring configuration prepared in runtime/monitoring")
