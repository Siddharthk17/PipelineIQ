import yaml

yaml_str = "pipeline:\n    name: test\nsteps:\n  - name: load"
try:
    print(yaml.safe_load(yaml_str))
    print("Valid!")
except Exception as e:
    print(f"Invalid: {e}")
