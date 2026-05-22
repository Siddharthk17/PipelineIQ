import yaml
from pydantic import BaseModel

class ValidatePipelineRequest(BaseModel):
    yaml_config: str

# Example of what might cause the error:
# Leading spaces on line 1 or 2, then a mapping on line 3.
# Or something that looks like a mapping but isn't.

test_yamls = [
    "pipeline:\n  name: test\n  steps:\n    - name: load\n      type: load",
    "\n\npipeline:\n  name: test\n  steps:\n    - name: load\n      type: load",
    "   \n   \npipeline:\n  name: test\n  steps:\n    - name: load\n      type: load",
    "--- \n\npipeline:\n  name: test\n  steps:\n    - name: load\n      type: load",
]

for i, y in enumerate(test_yamls):
    try:
        print(f"Testing YAML {i}:")
        print(repr(y))
        yaml.safe_load(y)
        print("SUCCESS")
    except Exception as e:
        print(f"FAILED: {e}")
    print("-" * 20)
