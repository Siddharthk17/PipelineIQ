import yaml

test_cases = [
    "pipeline:\n  name: test",
    "  pipeline:\n  name: test",
    "\n\npipeline:\n  name: test",
    "--- \npipeline:\n  name: test",
    "pipeline:\n  name: test\n---",
    "pipeline: \n  name: test\n  steps: \n    - name: load",
    "   pipeline:\n    name: test",
    "pipeline:\n  name: test\n  steps:\n    - name: load\n      type: load",
]

for i, tc in enumerate(test_cases):
    try:
        yaml.safe_load(tc)
        print(f"Case {i}: OK")
    except Exception as e:
        print(f"Case {i}: FAILED: {e}")

# Try to force the specific error
try:
    # This is a common way to trigger this error: combining a scalar and a mapping
    # without proper indentation or markers.
    yaml.safe_load("scalar\npipeline: test")
except Exception as e:
    print(f"Forced Case: FAILED: {e}")

try:
    # Another case: invalid indentation on the first line
    yaml.safe_load("  pipeline:\nname: test")
except Exception as e:
    print(f"Forced Case 2: FAILED: {e}")
