"""
SIRAYA Protocol Ingestion Script
Indexes all PDF protocols into ChromaDB vector store.

Usage:
    python scripts/ingest_protocols.py
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from siraya.services.rag_service import RAGService
from siraya.config.settings import RAGConfig

def main():
    """Main ingestion routine."""
    print("🚀 SIRAYA Protocol Ingestion")
    print("=" * 80)
    
    # Check protocols directory
    protocols_dir = RAGConfig.PROTOCOLS_DIR
    if not protocols_dir.exists():
        print(f"❌ ERROR: Protocols directory not found: {protocols_dir}")
        print(f"📂 Please create {protocols_dir} and move PDF files there")
        return 1
    
    pdf_files = list(protocols_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"❌ ERROR: No PDF files found in {protocols_dir}")
        return 1
    
    print(f"📚 Found {len(pdf_files)} PDF files:")
    for pdf in sorted(pdf_files):
        priority = RAGConfig.PROTOCOL_PRIORITIES.get(pdf.name, 99)
        print(f"   - {pdf.name} (priority: {priority})")
    
    print("\n🧠 Initializing RAG service...")
    rag = RAGService()
    
    print("\n📄 Starting ingestion...")
    results = rag.ingest_all_protocols()
    
    print("\n" + "=" * 80)
    print("✅ INGESTION COMPLETE")
    print("=" * 80)
    
    total_chunks = sum(results.values())
    print(f"📊 Total chunks indexed: {total_chunks}")
    print(f"💾 ChromaDB location: {rag.persist_dir}")
    
    print("\n📑 Breakdown by file:")
    for filename, count in sorted(results.items(), key=lambda x: -x[1]):
        print(f"   {filename}: {count} chunks")
    
    # Verify
    stats = rag.get_stats()
    print(f"\n🔍 Vector store stats:")
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    print("\n✅ Ready to use! The RAG service will now retrieve clinical protocols.")
    return 0

if __name__ == "__main__":
    exit(main())
