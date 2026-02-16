#!/usr/bin/env python3
"""
Calcola metriche di riduzione codice per refactoring V3.
"""

from pathlib import Path

def count_lines(file_path: Path) -> int:
    """Count non-empty, non-comment lines."""
    count = 0
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith('#'):
                    count += 1
    except:
        pass
    return count

def main():
    files_to_check = {
        "Controller V3 (new)": Path("siraya/controllers/triage_controller_v3.py"),
        "RAG Service": Path("siraya/services/rag_service.py"),
        "Sidebar View": Path("siraya/views/sidebar_view.py"),
        "Chat View": Path("siraya/views/chat_view.py"),
    }
    
    print("CODE METRICS REPORT - V3 Refactoring\n")
    print("=" * 60)
    
    total_after = 0
    
    for name, path in files_to_check.items():
        if path.exists():
            lines = count_lines(path)
            print(f"{name:30} {lines:5} righe")
            total_after += lines
        else:
            print(f"{name:30} {'N/A':5} (file non trovato)")
    
    print("=" * 60)
    
    # Stima controller V2 (se esiste)
    v2_path = Path("siraya/controllers/triage_controller.py")
    if v2_path.exists():
        v2_lines = count_lines(v2_path)
        print(f"{'Controller V2 (legacy):':30} {v2_lines:5} righe")
        print(f"{'Controller V3 (new):':30} {count_lines(Path('siraya/controllers/triage_controller_v3.py')):5} righe")
        
        v3_lines = count_lines(Path("siraya/controllers/triage_controller_v3.py"))
        if v2_lines > 0:
            reduction = ((v2_lines - v3_lines) / v2_lines) * 100
            print(f"{'RIDUZIONE CONTROLLER:':30} {reduction:5.1f}%")
    
    print(f"\nTarget: -40% | Controller V3: {reduction:.1f}% reduction" if v2_path.exists() else "\nController V3 creato")

if __name__ == "__main__":
    main()

