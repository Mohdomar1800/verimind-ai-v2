import requests
import json
import os

url = "http://127.0.0.1:8000/ingest/logs"
files = [('files', ('sample_test.log', open('sample_test.log', 'rb')))]
data = {
    "run_pipeline": "true",
    "context_run_id": "run_test_api",
    "feature_run_id": "feat_test_api",
    "isolate_db": "false"  # Default should be false now but let's be explicit
}

resp = requests.post(url, files=files, data=data)
print(f"Status Code: {resp.status_code}")
try:
    print(json.dumps(resp.json(), indent=2))
except Exception:
    print(resp.text)
