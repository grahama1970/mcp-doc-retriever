import re
import hashlib
import json


def hash_string(input_string: str) -> str:
    return hashlib.sha256(input_string.encode("utf-8")).hexdigest()


class SectionHierarchy:
    def __init__(self):
        self.stack = []

    def update(self, section_number: str, section_title: str, section_content: str):
        nums = [int(n) for n in section_number.split(".") if n]
        current_level = len(nums)

        # Pop sections at the same or deeper level
        while self.stack and len(self.stack[-1][0].split(".")) >= current_level:
            self.stack.pop()

        full_title = f"{section_number} {section_title}".strip()
        section_hash = hash_string(full_title + section_content)
        self.stack.append((section_number, full_title, section_hash))
        print(f"Updated stack for {full_title}: {[item[1] for item in self.stack]}")

    def get_titles(self):
        # include current section as well as ancestors
        return [title for _, title, _ in self.stack]

    def get_hashes(self):
        # include current section's hash as well as ancestors'
        return [hash_val for _, _, hash_val in self.stack]


doc = """
**10. Continuous Improvement**

**10.1 Operating Experience Feedback**

*   A system must be in place to collect, analyze, and disseminate operating experience from NPPs around the world.
*   This information should be used to identify potential safety issues and to implement corrective actions.
*   Operating experience feedback should be a key input to the continuous improvement process.

**10.2 Research and Development**

*   Research and development efforts should be supported to improve NPP safety and performance.
*   Areas of research include advanced reactor designs, accident mitigation strategies, and improved materials.

**11. Conclusion**

The safety requirements outlined in this document are essential for ensuring the safe and reliable operation of NPPs. Adherence to these requirements is critical for protecting the public and the environment from potential radiological hazards. The nuclear industry must continue to strive for continuous improvement in safety through the implementation of advanced technologies, robust operating practices, and a strong safety culture. This document provides a solid foundation but should be supplemented with site-specific analysis and continuously reviewed and updated based on new research and operational experience.
"""

# Debug: Print the raw doc to inspect formatting
print("Raw doc content:")
print(repr(doc))

# Normalize doc: Strip leading/trailing whitespace and ensure single newlines
doc = re.sub(r"\n\s*\n+", "\n", doc.strip())

# Regex for section headers (no MULTILINE, applied per line)
section_re = re.compile(
    r"^\*\*(?P<number>\d+(?:\.\d+)*\.?)\s+(?P<title>[^\n*]+?)\s*\*\*$"
)

sections = []
current_section = None
section_content_lines = []

# Create an instance of SectionHierarchy
hierarchy = SectionHierarchy()

lines = doc.splitlines()
for i, line in enumerate(lines):
    line = line.strip()
    if not line:
        continue

    # Check if the line is a section header
    match = section_re.match(line)
    if match:
        # If we were collecting content for a previous section, finalize it
        if current_section:
            section_content = "\n".join(section_content_lines).strip()
            section_number, section_title = current_section
            print(
                f"Detected section: {section_number} {section_title}, content length: {len(section_content)}"
            )
            hierarchy.update(section_number, section_title, section_content)
            sections.append(
                {
                    "section_number": section_number,
                    "section_title": section_title,
                    "section_path": hierarchy.get_titles(),
                    "section_hash_path": hierarchy.get_hashes(),
                }
            )
            section_content_lines = []

        # Start a new section
        section_number = match.group("number").rstrip(".")  # Remove trailing dot
        section_title = match.group("title").strip()
        current_section = (section_number, section_title)
    elif current_section:
        # Collect content for the current section
        section_content_lines.append(line)

# Finalize the last section
if current_section:
    section_content = "\n".join(section_content_lines).strip()
    section_number, section_title = current_section
    print(
        f"Detected section: {section_number} {section_title}, content length: {len(section_content)}"
    )
    hierarchy.update(section_number, section_title, section_content)
    sections.append(
        {
            "section_number": section_number,
            "section_title": section_title,
            "section_path": hierarchy.get_titles(),
            "section_hash_path": hierarchy.get_hashes(),
        }
    )

# Output the result as JSON
print(json.dumps(sections, indent=4))
