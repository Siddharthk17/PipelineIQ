#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

def run_coverage_report():
    result = subprocess.run(
        [
            "pytest", "backend/tests/unit/",
            "--cov=backend",
            "--cov-report=term-missing",
            "--cov-report=html:coverage-report",
            "--cov-report=xml:coverage.xml",
            "-q",
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout[-3000:])
    if result.returncode != 0:
        print(result.stderr[-1000:])
    return result.returncode == 0


def parse_coverage_xml() -> dict:
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse("coverage.xml")
        root = tree.getroot()
        line_rate = float(root.attrib.get("line-rate", 0))
        branch_rate = float(root.attrib.get("branch-rate", 0))
        return {
            "line_coverage": round(line_rate * 100, 1),
            "branch_coverage": round(branch_rate * 100, 1),
        }
    except Exception as e:
        return {"error": str(e)}


def count_tests() -> dict:
    backend_result = subprocess.run(
        ["pytest", "backend/tests/unit/", "--collect-only", "-q"],
        capture_output=True, text=True,
    )
    backend_count = sum(
        1 for line in backend_result.stdout.split('\n')
        if line.strip() and "::" in line
    )

    e2e_count = 0
    try:
        e2e_result = subprocess.run(
            ["npx", "playwright", "test", "--list", "--reporter=list"],
            capture_output=True, text=True, cwd=".",
        )
        e2e_count = sum(
            1 for line in e2e_result.stdout.split('\n')
            if line.strip() and (".spec." in line or ".ts" in line)
        )
    except Exception:
        pass

    return {
        "backend_unit": backend_count,
        "e2e_playwright": e2e_count,
        "k6_scenarios": 3,
        "chaos_scenarios": 7,
        "total": backend_count + e2e_count + 3 + 7,
    }


def main():
    print("=" * 60)
    print("PipelineIQ v3.1.0 — Test Coverage Report")
    print("=" * 60)

    print("\nRunning test coverage analysis...")
    coverage_ok = run_coverage_report()

    coverage = parse_coverage_xml()
    counts = count_tests()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    print(f"\nCode Coverage:")
    print(f"   Line coverage:   {coverage.get('line_coverage', 'N/A')}%")
    print(f"   Branch coverage: {coverage.get('branch_coverage', 'N/A')}%")

    print(f"\nTest Counts:")
    print(f"   Backend unit tests:  {counts['backend_unit']}")
    print(f"   E2E (Playwright):    {counts['e2e_playwright']}")
    print(f"   Load tests (k6):     {counts['k6_scenarios']} scenarios")
    print(f"   Chaos scenarios:     {counts['chaos_scenarios']}")
    print(f"   {'─' * 29}")
    print(f"   TOTAL:               {counts['total']}")

    target_coverage = 80.0
    actual_coverage = coverage.get('line_coverage', 0)
    if isinstance(actual_coverage, str):
        actual_coverage = 0
    coverage_met = actual_coverage >= target_coverage

    print(f"\n{'PASS' if coverage_met else 'FAIL'} Coverage target: "
          f"{actual_coverage}% / {target_coverage}% required")

    if not coverage_ok or not coverage_met:
        print("\nTest suite has failures or insufficient coverage")
        sys.exit(1)

    print("\nAll test quality gates passed")


if __name__ == "__main__":
    main()
