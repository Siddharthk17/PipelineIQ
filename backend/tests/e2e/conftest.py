"""E2E test configuration - sets environment variables before any backend imports."""
import os
import sys

# CRITICAL: Set local storage BEFORE any backend imports to avoid S3/MinIO connection
os.environ["STORAGE_TYPE"] = "local"
os.environ["UPLOAD_DIR"] = "/tmp/pipelineiq-test-uploads"
os.makedirs("/tmp/pipelineiq-test-uploads", exist_ok=True)

# Ensure backend is on path
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "..")))
