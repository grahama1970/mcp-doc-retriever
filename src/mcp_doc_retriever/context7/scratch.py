import re

doc = """
**10. Continuous Improvement**

**10.1 Operating Experience Feedback**

**10.2 Research and Development**

**11. Conclusion**
"""

section_re = re.compile(
    r"^\*\*(?P<number>\d+(?:\.\d+)*)\s+(?P<title>[^\n*]+)\*\*", re.MULTILINE
)

for match in section_re.finditer(doc):
    print(f"Matched: number={match.group('number')}, title={match.group('title')}")
