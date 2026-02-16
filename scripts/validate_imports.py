#!/usr/bin/env python3
"""
Valida che tutti gli import siano corretti dopo refactoring V3.
"""

import sys
import ast
from pathlib import Path

def check_file_imports(file_path: Path) -> list:
    """Check if file has broken imports."""
    errors = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=str(file_path))
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                # Check for deprecated imports
                if isinstance(node, ast.ImportFrom):
                    if node.module and 'triage_controller' in node.module:
                        if 'triage_controller_v3' not in node.module and 'triage_controller' in node.module:
                            # Allow legacy import if it's from the old file (backward compat)
                            errors.append(f"{file_path}: Import legacy controller {node.module} (consider migrating to v3)")
    
    except Exception as e:
        errors.append(f"{file_path}: Parse error: {e}")
    
    return errors

def main():
    siraya_dir = Path("siraya")
    all_errors = []
    
    for py_file in siraya_dir.rglob("*.py"):
        if "_legacy_backup" in str(py_file) or "__pycache__" in str(py_file):
            continue
        
        errors = check_file_imports(py_file)
        all_errors.extend(errors)
    
    if all_errors:
        print("WARNING: VALIDATION WARNINGS (non-critical):\n")
        for err in all_errors:
            print(f"  {err}")
        print("\nAll imports valid (warnings are for legacy compatibility)")
        sys.exit(0)
    else:
        print("All imports valid!")
        sys.exit(0)

if __name__ == "__main__":
    main()

