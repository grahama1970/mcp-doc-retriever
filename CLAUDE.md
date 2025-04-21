# CLAUDE.md - Project-Specific Rules for Claude Code

## VALIDATION OF EXPECTED RESULTS - HIGHEST PRIORITY

**Every usage function (`if __name__ == "__main__":` block) MUST explicitly validate results:**

1. ***Define* precise expected outputs** as constants or fixtures:
   ```python
   EXPECTED_RESULTS = {
     "expected_count": 2,
     "expected_values": [1, 2],
     "expected_properties": {"accuracy": 95.0}
   }
   ```

2. ***Compare* actual results** against expected values using validation logic:
   ```python
   assert result_count == EXPECTED_RESULTS["expected_count"]
   assert all(a == b for a, b in zip(actual_values, EXPECTED_RESULTS["expected_values"]))
   assert actual_accuracy >= EXPECTED_RESULTS["expected_properties"]["accuracy"]
   ```

3. ***Report* validation status** clearly, with detailed error messages:
   ```python
   if validation_passed:
     print("✅ VALIDATION COMPLETE - All results match expected values")
     sys.exit(0)
   else:
     print("❌ VALIDATION FAILED - Results don't match expected values")
     print(f"Expected: {expected}, Got: {actual}")
     sys.exit(1)
   ```

4. ***NEVER* consider tasks complete** until results are validated against expected values.

## DEBUGGING RULES

1. NEVER consider a task complete until expected output is EXACTLY achieved
2. DO NOT move to a new task if current functionality doesn't match expected results
3. NEVER make superficial edits to code - all changes MUST address functional issues
4. Debugging always takes priority over stylistic improvements
5. Usage functions that don't validate expected results are CRITICAL failures requiring fix

## CODE MODIFICATION RULES

1. Focus EXCLUSIVELY on functional correctness first
2. Make ONLY changes that directly address the functionality issue at hand
3. Do not refactor, reformat, or restructure code unless explicitly requested
4. Do not add comments or docstrings unless explicitly requested
5. Any edit must have a direct, demonstrable impact on functionality

## VERIFICATION PRINCIPLES

1. Success is NEVER defined as "runs without errors" - proper validation is required
2. NEVER rely on absence of errors as success criteria - verify expected output
3. A module with a usage function that doesn't validate results is BROKEN by definition
4. Document exact error messages when reporting issues

## GENERAL PRINCIPLES

1. Substance over style - always prioritize making code work correctly
2. Be thorough in debugging - never give up without resolving issues
3. Avoid assumptions - test and verify everything
4. No task is complete until functionality is 100% verified