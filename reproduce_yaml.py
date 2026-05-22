import yaml
try:
    yaml_text = """
pipeline:
  name: test
  steps:
    - name: load
      type: load
"""
    print(yaml.safe_load(yaml_text))
except Exception as e:
    print(e)
