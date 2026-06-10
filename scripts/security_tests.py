import requests

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiOTI3ZmQ5ZS0yNGUwLTQ3NDgtOWUyZS04ZDcxZGZlOTE5NmMiLCJyb2xlIjoiYWRtaW4iLCJleHAiOjE3NzYwMDIxOTB9.fPFkPrrw1T9UbzCg8WBdxBHI_cztEpNpvzaA1QjA8pE"
BASE_URL = "http://localhost"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

payloads = [
    # 1. Simple forbidden keyword
    "DROP TABLE {{input}}",
    # 2. Keyword inside a string (should be allowed)
    "SELECT 'drop table' as test FROM {{input}}",
    # 3. Keyword with whitespace/comments (trying to bypass \b)
    "DROP/**/TABLE {{input}}",
    # 4. Case variation (should be blocked by re.IGNORECASE)
    "drOp table {{input}}",
    # 5. Multiple statements (should be blocked by ; check)
    "SELECT * FROM {{input}}; DROP TABLE {{input}}",
    # 6. Complex nested select
    "SELECT * FROM {{input}} WHERE 1=1 AND (SELECT 1 FROM (SELECT COUNT(*), (SELECT 1 FROM (SELECT 1) a) as x FROM (SELECT 1) b GROUP BY x) a)",
]

for i, query in enumerate(payloads):
    yaml_config = f"""
pipeline:
  name: sec_test_{i}
  steps:
    - name: load
      type: load
      file_id: "d304d177-1fa9-47b5-8bd4-fa5d3bc40cb3"
    - name: sql_step
      type: sql
      input: load
      query: "{query}"
"""
    resp = requests.post(f"{BASE_URL}/api/v1/pipelines/validate", headers=HEADERS, json={
        "yaml_config": yaml_config
    })
    print(f"Payload {i}: {query} -> Valid: {resp.json().get('is_valid')}, Errors: {resp.json().get('errors')}")
