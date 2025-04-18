import re
import hashlib
import json  # Added for potentially cleaner output viewing if needed


def hash_string(input_string: str) -> str:
    """Generates a SHA256 hash for a given string."""
    return hashlib.sha256(input_string.encode("utf-8")).hexdigest()


class SectionHierarchy:
    """
    Manages a stack representing the current section hierarchy path.
    """

    def __init__(self):
        # Stack stores tuples: (section_number_str, full_title_str, content_hash_str)
        self.stack = []

    def update(self, section_number: str, section_title: str, section_content: str):
        """
        Updates the hierarchy stack based on a newly encountered section.

        Args:
            section_number: The number string (e.g., "5", "5.2", "5.5.1").
            section_title: The title string (e.g., "Operational Safety Requirements").
            section_content: The content associated with this specific section (used for hashing).
        """
        # Parse the current section number into a list of integers
        try:
            # Use floats first to handle potential non-integer parts gracefully if format changes,
            # but treat them primarily as integers for comparison logic.
            # Using strings directly might be safer if non-numeric parts are possible (e.g., 'A.1')
            # Stick with original int conversion as example uses numeric parts.
            nums = [int(n) for n in section_number.split(".")]
            current_level = len(nums)
        except ValueError:
            print(
                f"Warning: Could not parse section number '{section_number}' as numeric parts. Skipping update."
            )
            return

        # --- Core Hierarchy Logic ---
        # Pop elements from the stack that are not ancestors of the current section.
        while self.stack:
            prev_number_str = self.stack[-1][0]
            try:
                prev_nums = [int(n) for n in prev_number_str.split(".")]
                prev_level = len(prev_nums)
            except ValueError:
                # Should not happen if stack only contains valid entries, but safe programming
                print(
                    f"Warning: Could not parse previous section number '{prev_number_str}' on stack. Popping."
                )
                self.stack.pop()
                continue

            # Determine if the previous section (stack top) is an ancestor
            # It IS an ancestor if:
            # 1. It's at a higher level (prev_level < current_level)
            # 2. The current section number starts with the previous section number
            is_ancestor = False
            if prev_level < current_level:
                if nums[:prev_level] == prev_nums:
                    is_ancestor = True

            # If the stack top is NOT an ancestor, pop it.
            # This covers:
            # - Siblings (same level, different numbers, e.g., 5.5 after 5.2)
            # - Moving to a higher level (e.g., 6 after 5.5.1)
            # - Moving to a different branch (e.g., 5.6 after 5.5.1 - pops 5.5.1, then 5.5)
            if not is_ancestor:
                self.stack.pop()
            else:
                # The stack top IS an ancestor, so we stop popping.
                break
        # --- End Core Hierarchy Logic ---

        # Append the current section to the stack
        full_title = (
            f"{section_number} {section_title}".strip()
        )  # Original used '.' - adjusted to match example output
        section_hash = hash_string(
            full_title + section_content
        )  # Hash uses title+content
        self.stack.append((section_number, full_title, section_hash))

    def get_titles(self):
        """Returns the list of full titles currently in the hierarchy stack."""
        return [t for n, t, h in self.stack]

    def get_hashes(self):
        """Returns the list of content hashes currently in the hierarchy stack."""
        return [h for n, t, h in self.stack]

    def get_path(self):
        """Returns the full path data currently on the stack."""
        return list(self.stack)  # Return a copy


# --- Example Usage ---
doc = """
Some introductory text.

**5. Operational Safety Requirements**
Content for section 5.

**5.2 Training**
Content for section 5.2. This section details training procedures.

More text here perhaps.

**5.5 Emergency Preparedness**
Content for 5.5 about emergencies.

**5.5.1 Emergency Plans**
Specific plans content here for 5.5.1.

**6. Physical Protection**
Content for section 6. A new top-level section.

**6.1 Access Control**
Subsection of 6.

**6.1.1 Badging**
Sub-subsection of 6.1.

**5. Operational Safety Requirements**
Revisiting section 5, should reset path.

**5.1 New Subsection**
New subsection under the revisited 5.
"""

# Regex to find section headers like **1.2.3 Title Text**
# It captures the number (group 1) and the title (group 2)
section_re = re.compile(
    r"^\*\*(?P<number>\d+(?:\.\d+)*)\.?\s+(?P<title>[^\n*]+)\*\*", re.MULTILINE
)

hierarchy = SectionHierarchy()

# Simulate processing the document section by section
# In a real scenario, you'd extract content between section headers.
# For this example, we'll use placeholder content based on the title.
current_pos = 0
for match in section_re.finditer(doc):
    section_number = match.group("number")
    section_title = match.group("title").strip()

    # Find content (simplified: text between this match and the next, or end of doc)
    content_start = match.end()
    next_match = section_re.search(doc, content_start)
    content_end = next_match.start() if next_match else len(doc)
    section_content = doc[content_start:content_end].strip()
    # Add placeholder content if extraction is empty (e.g., headers right after each other)
    if not section_content:
        section_content = f"Placeholder content for {section_number} {section_title}"

    print(f"--- Processing: {section_number} {section_title} ---")
    hierarchy.update(section_number, section_title, section_content)

    print("Current section_path (titles):")
    # Using json.dumps for potentially cleaner list printing
    print(json.dumps(hierarchy.get_titles(), indent=2))

    print("\nCurrent section_hash_path:")
    print(json.dumps(hierarchy.get_hashes(), indent=2))
    print("-" * 20 + "\n")

# Final state
print("=== Final Hierarchy State ===")
print("Titles:", json.dumps(hierarchy.get_titles(), indent=2))
print("Hashes:", json.dumps(hierarchy.get_hashes(), indent=2))
print("Full Path Data:", json.dumps(hierarchy.get_path(), indent=2))
