# Mini Task Plan: Ensure `.roorules` Compliance for `pdf_extractor` Modules

**Goal:** Verify and/or implement `.roorules` compliance for each core Python module in the `src/mcp_doc_retriever/context7/pdf_extractor/` directory. The primary focus is ensuring the standalone usage function (`if __name__ == "__main__":`) works correctly and verifies core functionality when executed with `uv run` from the project root.

**Scope:** Core Python modules within `src/mcp_doc_retriever/context7/pdf_extractor/`. Excludes `api.py` and `cli.py` for now, as their entry points are handled by their respective frameworks (FastAPI/Typer).

**Process:** Each file will be handled iteratively by the assigned implementation role (e.g., Debugger). After each file is successfully verified, the role will switch to Boomerang mode to report completion before moving to the next file.

**CRITICAL DEBUGGING REQUIREMENT:** 
- NEVER move to the next file until the current file produces the EXACT expected results
- Maintain a rigorous "debug until correct" approach for each module
- If a usage function isn't working correctly, this is a FAILURE - not a minor issue
- THOROUGH debugging is required until the expected and actual results ALIGN COMPLETELY
- Any discrepancy between expected and actual output indicates the task is NOT complete
- Success criteria is FUNCTIONAL CODE, not just code that compiles or runs without errors

**Proposed Order of Files:**

1.  `types.py`
2.  `config.py`
3.  `utils.py`
4.  `table_extractor.py`
5.  `marker_processor.py`
6.  `markdown_extractor.py`
7.  `qwen_processor.py`
8.  `parsers.py`
9.  `pdf_to_json_converter.py` (Final integration check)

**Workflow for Each File (`<filename.py>`):**

1.  **Task:** Verify/Implement `.roorules` compliance for `<filename.py>`.
2.  **Read:** Use `read_file` to get the current content of `<filename.py>`.
3.  **Analyze & Fix:**
    *   Ensure module-level docstring exists and meets requirements (description, links if applicable, example usage).
    *   Ensure an `if __name__ == "__main__":` block exists.
    *   Implement/update the `__main__` block to provide a minimal, *independent* test of the module's core functionality. This test should ideally be self-contained (e.g., using simple data structures or functions defined within the block). *Assumption: Use self-contained tests or existing `input/` files; avoid creating new test data directories unless necessary.*
    *   Fix critical import errors necessary for standalone execution (use absolute imports from project root `mcp_doc_retriever...` or relative imports `.module`).
    *   Ensure the file is under 500 lines.
    *   Address other `.roorules` violations only if they prevent standalone execution. Runtime correctness is the priority.
4.  **Verify Standalone Execution:**
    *   From the project root (`/Users/robert/Documents/dev/workspace/experiments/mcp-doc-retriver`), execute the script using:
        ```bash
        env PYTHONPATH=./src uv run python src/mcp_doc_retriever/context7/pdf_extractor/<filename.py>
        ```
    *   CRITICAL: Every `if __name__ == "__main__":` block MUST include:
        - Explicitly defined expected results as constants (e.g., `EXPECTED_OUTPUT = {...}`)
        - Strict validation logic that compares actual results with these expected values
        - Clear success/failure messaging based on validation, not just execution
        - Non-zero exit codes (`sys.exit(1)`) when validation fails
        - Comprehensive validation beyond just "it ran without errors"
    *   CRITICAL: The output must EXACTLY match the expected output - no exceptions!
    *   If execution fails or produces unexpected results, CONTINUE DEBUGGING until fixed.
    *   Use print statements, logging, step-by-step debugging techniques as needed.
    *   NEVER consider a module "done" until it produces the EXACT expected output AND validates it.
5.  **Report & Switch (Handled by the implementation role):** ONLY after complete verification succeeds with the EXACT expected output, switch to Boomerang mode and report completion for `<filename.py>`.
6.  **Debugging Reminder:** If you encounter persistent issues with a module:
    *   DO NOT move on to the next file
    *   DO NOT declare partial success
    *   DO debug systematically until ALL issues are resolved
    *   DO verify that ALL expected functionality works correctly

**Diagram (Simplified Flow):**

```mermaid
graph TD
    A[Start: Architect Plan] --> B{File: types.py};
    B --> C[Implementer: Fix & Verify];
    C -- Success --> D{File: config.py};
    C -- Failure --> C;
    D --> E[Implementer: Fix & Verify];
    E -- Success --> F{File: utils.py};
    E -- Failure --> E;
    F --> G[... Continue for each file ...];
    G --> H{File: pdf_to_json_converter.py};
    H --> I[Implementer: Fix & Verify];
    I -- Success --> J[End: All Files Compliant];
    I -- Failure --> I;

    subgraph Implementer Role (e.g., Debugger)
        C
        E
        G
        I
    end
```

**Assumptions based on Pending Clarifications:**

*   `api.py` and `cli.py` will **not** have `__main__` blocks added for basic verification at this stage. Their framework entry points are considered sufficient for now.
*   For standalone tests in `__main__` blocks, the implementation role should prioritize **self-contained examples** or use **existing files** in the main `input/` directory. Creating new test data subdirectories should be avoided unless strictly necessary.

**FINAL REMINDER - CRITICAL SUCCESS CRITERIA:**

1. The primary measure of success is FUNCTIONALITY with RIGOROUS VALIDATION, not code appearance or minor style issues
2. EVERY module must include a proper usage function that:
   - Defines explicit expected results/outputs (as constants or test fixtures)
   - Includes validation logic that compares actual to expected results
   - Fails clearly and loudly when expected and actual results don't match exactly
   - Uses sys.exit(1) on validation failure to signal errors to calling processes
   - Only declares "verification complete" when ALL validations pass
3. NO EXCEPTIONS to the "validate expected results" rule - success is NEVER defined as "runs without errors"
4. A module with a usage function that doesn't validate results is BROKEN by definition
5. If a module works in the larger application but fails standalone validation, the module is BROKEN and needs fixing
6. Persistence in debugging is REQUIRED - never give up and move on until the module produces AND VALIDATES expected results
7. Document any troubleshooting steps thoroughly to help with similar issues in other modules
