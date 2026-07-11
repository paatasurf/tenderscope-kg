"""Generate a deployment readiness summary from CI artifacts."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path


def main() -> None:
    reports = Path("reports")
    reports.mkdir(exist_ok=True)
    junit = reports / "unit-junit.xml"
    tests = failures = errors = 0
    elapsed = 0.0
    if junit.exists():
        root = ET.parse(junit).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        for suite in suites:
            tests += int(suite.attrib.get("tests", 0))
            failures += int(suite.attrib.get("failures", 0))
            errors += int(suite.attrib.get("errors", 0))
            elapsed += float(suite.attrib.get("time", 0) or 0)

    cov = "n/a"
    cxml = reports / "coverage.xml"
    if cxml.exists():
        rate = ET.parse(cxml).getroot().attrib.get("line-rate")
        if rate:
            cov = f"{float(rate) * 100:.1f}%"

    ready = failures == 0 and errors == 0 and tests > 0
    answer = "YES" if ready else "NO"
    text = f"""# Deployment readiness — `tenderscope-kg`

Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}

Ready for deploy: {answer}

- Tests: {tests}
- Failures: {failures}
- Errors: {errors}
- Time: {elapsed:.1f}s
- Coverage (line-rate): {cov}
"""
    (reports / "deployment-readiness.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
