# test_imports.py
print("1. Test base")
from dotenv import load_dotenv
print("2. Dotenv OK")

from supabase import create_client
print("3. Supabase OK")

import pypdf
print("4. Pypdf OK")

from sentence_transformers import SentenceTransformer
print("5. Sentence-transformers OK")

print("\n✅ TUTTI GLI IMPORT OK!")