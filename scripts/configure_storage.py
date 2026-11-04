"""Initialize local bucket CORS for the explicit development web origins."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps/api"))
from app.config import get_settings  # noqa: E402
from app.storage.s3 import S3Storage  # noqa: E402

storage = S3Storage.from_settings(get_settings())
storage.ensure_bucket()
storage.client.put_bucket_cors(Bucket=storage.config.bucket, CORSConfiguration={"CORSRules": [{
    "AllowedOrigins": ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8080", "http://localhost:8080"],
    "AllowedMethods": ["PUT"], "AllowedHeaders": ["content-type", "x-amz-server-side-encryption"],
    "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 600}]})
print("Private development bucket and browser PUT CORS configured.")
