"""Export the actual API separately from the unimplemented parts of the design contract."""
import json
from pathlib import Path

from app.main import app

destination = Path(__file__).resolve().parents[1] / "contracts" / "runtime-openapi.json"
destination.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Exported {len(app.openapi()['paths'])} runtime paths.")
