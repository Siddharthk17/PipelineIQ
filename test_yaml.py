import yaml

yamls = [
    """
pipeline:
  name: test
  steps:
    - name: load
      type: load
      file_id: 'abc'
      contract:
        column: amount
        type: float
""",
    """
pipeline:
  name: test
  steps:
    - name: load
      type: load
      file_id: 'abc'
  contract:
    column: amount
    type: float
"""
]

for i, y in enumerate(yamls):
    try:
        print(f"Test {i}: {yaml.safe_load(y)}")
    except Exception as e:
        print(f"Test {i} failed: {e}")
