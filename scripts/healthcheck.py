import json
import urllib.request

for path in ("/health/live", "/health/ready", "/version"):
    with urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=3) as response:
        print(path, response.status, json.loads(response.read()))
