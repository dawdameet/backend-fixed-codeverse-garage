"""
Deterministic Bug Verifier
Uses regex and static analysis to verify bug fixes
"""

import re
from typing import Dict, Any, Optional


class DeterministicVerifier:
    """Static code verifier using regex matching"""
    
    def verify_bug_fix(self, bugs_doc: str, bug_diff_json: Dict[str, Any], bug_to_check: str) -> Optional[bool]:
        """
        Verify bug fix using deterministic pattern matching
        
        Args:
            bugs_doc: Full bug documentation string
            bug_diff_json: JSON object with code diff
            bug_to_check: Bug identifier to check
            
        Returns:
            True if fixed, False if not fixed, None if uncertain (fallback to LLM)
        """
        try:
            # Extract the specific bug documentation
            bug_spec = self._extract_bug_spec(bugs_doc, bug_to_check)
            if not bug_spec:
                return None  # Can't find bug spec, use LLM
            
            # Extract solution from bug spec
            solution = self._extract_solution(bug_spec)
            if not solution:
                return None  # No clear solution pattern, use LLM
            
            # Extract participant's code changes from diff
            participant_code = self._extract_code_from_diff(bug_diff_json)
            if not participant_code:
                return None  # No code changes found
            
            # Normalize both solution and participant code
            normalized_solution = self._normalize_code(solution)
            normalized_participant = self._normalize_code(participant_code)
            
            # Check for exact match
            if normalized_solution in normalized_participant:
                return True
            
            # Check for semantic equivalence
            if self._fuzzy_match(normalized_solution, normalized_participant):
                return True
            
            # Check for key patterns
            if self._pattern_match(solution, participant_code):
                return True
            
            # Not found - but this could be a false negative
            # Return None to defer to LLM for complex cases
            return None
            
        except Exception as e:
            print(f"Deterministic verification error: {e}")
            return None  # On error, defer to LLM
    
    def _extract_bug_spec(self, bugs_doc: str, bug_id: str) -> str:
        """Extract specific bug documentation from full doc"""
        # Try to find START "bug_id": and END "bug_id" markers
        pattern = rf'START\s+"{re.escape(bug_id)}":\s*(.*?)\s*END\s+"{re.escape(bug_id)}"'
        match = re.search(pattern, bugs_doc, re.DOTALL | re.IGNORECASE)
        
        if match:
            return match.group(1).strip()
        
        # Alternative: try markdown headers
        pattern = rf'#{1,3}\s*{re.escape(bug_id)}[:\s]+(.*?)(?=#{1,3}|\Z)'
        match = re.search(pattern, bugs_doc, re.DOTALL | re.IGNORECASE)
        
        if match:
            return match.group(1).strip()
        
        return ""
    
    def _extract_solution(self, bug_spec: str) -> str:
        """Extract solution code from bug specification"""
        # Look for SOLUTION: section with code block
        solution_patterns = [
            r'SOLUTION:\s*```(?:python|)?\s*(.*?)\s*```',
            r'## Solution\s*```(?:python|)?\s*(.*?)\s*```',
            r'Solution:?\s*```(?:python|)?\s*(.*?)\s*```',
            r'Fix:?\s*```(?:python|)?\s*(.*?)\s*```',
            r'SOLUTION:\s*([^\n]+)',  # Single line solution
            r'Solution:?\s*`([^`]+)`',  # Inline code
        ]
        
        for pattern in solution_patterns:
            match = re.search(pattern, bug_spec, re.DOTALL | re.IGNORECASE)
            if match:
                solution = match.group(1).strip()
                if solution:
                    return solution
        
        return ""
    
    def _extract_code_from_diff(self, diff_json: Dict[str, Any]) -> str:
        """Extract all code changes from diff JSON"""
        code_parts = []
        
        def extract_recursive(obj):
            if isinstance(obj, dict):
                # Look for common diff keys
                for key in ['added', 'new_code', 'content', 'changes', 'diff', 'code']:
                    if key in obj and isinstance(obj[key], str):
                        code_parts.append(obj[key])
                
                # Recursively check nested objects
                for value in obj.values():
                    extract_recursive(value)
                    
            elif isinstance(obj, list):
                for item in obj:
                    extract_recursive(item)
            elif isinstance(obj, str):
                # If it looks like code, include it
                if any(indicator in obj for indicator in ['def ', 'class ', 'import ', '=', 'if ', 'return']):
                    code_parts.append(obj)
        
        extract_recursive(diff_json)
        return '\n'.join(code_parts)
    
    def _normalize_code(self, code: str) -> str:
        """Normalize code for comparison"""
        # Remove all comments (both # and """ style)
        code = re.sub(r'#.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'""".*?"""', '', code, flags=re.DOTALL)
        code = re.sub(r"'''.*?'''", '', code, flags=re.DOTALL)
        
        # Remove all whitespace (spaces, tabs, newlines)
        code = re.sub(r'\s+', '', code)
        
        # Convert to lowercase for case-insensitive comparison
        code = code.lower()
        
        # Remove common variations
        code = re.sub(r'["\']', '', code)  # Remove quotes
        
        return code
    
    def _fuzzy_match(self, solution: str, code: str) -> bool:
        """Perform fuzzy matching for semantic equivalence"""
        # Extract meaningful tokens (identifiers, operators, keywords)
        def extract_tokens(text):
            # Find all alphanumeric sequences and operators
            tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*|[><=!]+|[+\-*/]', text)
            return [t.lower() for t in tokens if len(t) > 1]
        
        solution_tokens = extract_tokens(solution)
        code_tokens = extract_tokens(code)
        
        if not solution_tokens:
            return False
        
        # Count how many solution tokens appear in the code
        matches = sum(1 for token in solution_tokens if token in code_tokens)
        
        # Require at least 80% of tokens to match
        threshold = len(solution_tokens) * 0.8
        
        return matches >= threshold
    
    def _pattern_match(self, solution: str, code: str) -> bool:
        """Check for key patterns in the solution"""
        # Extract comparison operators and values
        comparisons = re.findall(r'([><=!]+)\s*([0-9.]+)', solution)
        
        for op, value in comparisons:
            # Normalize operator and value
            pattern = rf'{re.escape(op)}\s*{re.escape(value)}'
            if re.search(pattern, code):
                # Found at least one key pattern
                # For simple fixes, this might be enough
                continue
            else:
                return False
        
        # If we found patterns and all matched, return True
        return len(comparisons) > 0


if __name__ == "__main__":
    # Test the verifier
    verifier = DeterministicVerifier()
    
    bugs_doc = """
START "bug1":
Bug 1 - Incorrect VADER Threshold
SOLUTION:
```python
if compound >= 0.05:
```
END "bug1"
"""
    
    diff_json = {
        "files": [{
            "changes": "if compound >= 0.05:\n    return 'positive'"
        }]
    }
    
    result = verifier.verify_bug_fix(bugs_doc, diff_json, "bug1")
    print(f"Result: {result}")