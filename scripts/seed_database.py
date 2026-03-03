#!/usr/bin/env python3
"""
SIRAYA Health Navigator - Database Seeding Script
=================================================

This script migrates local JSON data files to Supabase.

Tables created/populated:
- facilities: Healthcare facilities from master_kb.json
- health_districts: District definitions from distretti_sanitari_er.json

Usage:
    python scripts/seed_database.py

Requirements:
    - SUPABASE_URL and SUPABASE_KEY must be set as environment variables
      OR be present in .streamlit/secrets.toml

Note: This script uses UPSERT to handle duplicates gracefully.
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# ============================================================================
# CONFIGURATION
# ============================================================================

# File paths (relative to project root)
PROJECT_ROOT = Path(__file__).parent.parent
MASTER_KB_PATH = PROJECT_ROOT / "master_kb.json"
DISTRICTS_PATH = PROJECT_ROOT / "distretti_sanitari_er.json"
MAP_DATA_PATH = PROJECT_ROOT / "mappa_er.json"

# Supabase table names
TABLE_FACILITIES = "facilities"
TABLE_DISTRICTS = "health_districts"

# ============================================================================
# SUPABASE CLIENT
# ============================================================================

def get_supabase_client():
    """
    Initialize Supabase client from environment or secrets.
    
    Returns:
        Supabase client instance or None if credentials not found
    """
    try:
        from supabase import create_client, Client
    except ImportError:
        print("❌ ERROR: supabase-py not installed.")
        print("   Run: pip install supabase")
        return None
    
    # Try to get credentials from environment
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    
    # If not in environment, try to load from .streamlit/secrets.toml
    if not url or not key:
        secrets_path = PROJECT_ROOT / ".streamlit" / "secrets.toml"
        if secrets_path.exists():
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                try:
                    import tomli as tomllib  # Fallback
                except ImportError:
                    print("⚠️  Cannot parse TOML secrets (install tomli for Python <3.11)")
                    tomllib = None
            
            if tomllib:
                try:
                    with open(secrets_path, "rb") as f:
                        secrets = tomllib.load(f)
                        url = secrets.get("SUPABASE_URL", url)
                        key = secrets.get("SUPABASE_KEY", key)
                except Exception as e:
                    print(f"⚠️  Error reading secrets: {e}")
    
    if not url or not key:
        print("❌ ERROR: Supabase credentials not found!")
        print("   Set SUPABASE_URL and SUPABASE_KEY as environment variables")
        print("   OR add them to .streamlit/secrets.toml")
        return None
    
    try:
        client: Client = create_client(url, key)
        print("✅ Supabase client initialized successfully")
        return client
    except Exception as e:
        print(f"❌ ERROR: Failed to create Supabase client: {e}")
        return None


# ============================================================================
# DATA LOADERS
# ============================================================================

def load_master_kb() -> Dict[str, Any]:
    """Load master_kb.json file."""
    if not MASTER_KB_PATH.exists():
        print(f"❌ ERROR: {MASTER_KB_PATH} not found!")
        return {}
    
    try:
        with open(MASTER_KB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"✅ Loaded master_kb.json: {len(data.get('facilities', []))} facilities")
            return data
    except Exception as e:
        print(f"❌ ERROR loading master_kb.json: {e}")
        return {}


def load_districts() -> Dict[str, Any]:
    """Load distretti_sanitari_er.json file."""
    if not DISTRICTS_PATH.exists():
        print(f"❌ ERROR: {DISTRICTS_PATH} not found!")
        return {}
    
    try:
        with open(DISTRICTS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            hd_count = len(data.get('health_districts', []))
            mapping_count = len(data.get('comune_to_district_mapping', {}))
            print(f"✅ Loaded distretti_sanitari_er.json: {hd_count} AUSLs, {mapping_count} comuni mappings")
            return data
    except Exception as e:
        print(f"❌ ERROR loading distretti_sanitari_er.json: {e}")
        return {}


# ============================================================================
# SEED FUNCTIONS
# ============================================================================

def seed_facilities(client, facilities: List[Dict]) -> int:
    """
    Seed facilities table.
    
    Args:
        client: Supabase client
        facilities: List of facility records
        
    Returns:
        Number of records upserted
    """
    if not facilities:
        print("⚠️  No facilities to seed")
        return 0
    
    print(f"\n📦 Seeding {len(facilities)} facilities...")
    
    # Transform data for Supabase (flatten nested objects to JSON strings)
    records = []
    for f in facilities:
        record = {
            "id": f.get("id"),
            "tipologia": f.get("tipologia"),
            "nome": f.get("nome"),
            "provincia": f.get("provincia"),
            "distretto": f.get("distretto"),
            "comune": f.get("comune"),
            "frazione": f.get("frazione"),
            "indirizzo": f.get("indirizzo"),
            "contatti": json.dumps(f.get("contatti", {}), ensure_ascii=False),
            "orari": json.dumps(f.get("orari", {}), ensure_ascii=False),
            "servizi_disponibili": f.get("servizi_disponibili", []),
            "updated_at": datetime.utcnow().isoformat()
        }
        records.append(record)
    
    # Batch upsert (Supabase limit is typically 1000 per request)
    batch_size = 500
    total_upserted = 0
    
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        try:
            response = client.table(TABLE_FACILITIES).upsert(
                batch,
                on_conflict="id"
            ).execute()
            
            count = len(response.data) if response.data else 0
            total_upserted += count
            print(f"   ✓ Batch {i // batch_size + 1}: {count} records")
            
        except Exception as e:
            print(f"   ❌ Batch {i // batch_size + 1} failed: {e}")
    
    print(f"   Total upserted: {total_upserted} facilities")
    return total_upserted


def seed_districts(client, districts_data: Dict) -> int:
    """
    Seed health_districts table.
    
    Args:
        client: Supabase client
        districts_data: District data dict with health_districts and comune_to_district_mapping
        
    Returns:
        Number of records upserted
    """
    health_districts = districts_data.get("health_districts", [])
    comune_mapping = districts_data.get("comune_to_district_mapping", {})
    
    if not health_districts:
        print("⚠️  No districts to seed")
        return 0
    
    print(f"\n📦 Seeding {len(health_districts)} AUSLs with districts...")
    
    # Transform data for Supabase
    records = []
    for hd in health_districts:
        ausl_name = hd.get("ausl", "")
        districts = hd.get("districts", [])
        
        # Create one record per AUSL with embedded district array
        record = {
            "id": ausl_name.lower().replace(" ", "_"),  # e.g., "ausl_romagna"
            "ausl": ausl_name,
            "districts": json.dumps(districts, ensure_ascii=False),
            "updated_at": datetime.utcnow().isoformat()
        }
        records.append(record)
    
    # Also store comune-to-district mapping as a separate record or in a dedicated table
    # For simplicity, we'll include it as a single meta-record
    if comune_mapping:
        meta_record = {
            "id": "_comune_mapping",
            "ausl": "META_MAPPING",
            "districts": json.dumps(comune_mapping, ensure_ascii=False),
            "updated_at": datetime.utcnow().isoformat()
        }
        records.append(meta_record)
    
    total_upserted = 0
    
    try:
        response = client.table(TABLE_DISTRICTS).upsert(
            records,
            on_conflict="id"
        ).execute()
        
        total_upserted = len(response.data) if response.data else 0
        print(f"   ✓ Upserted {total_upserted} district records")
        
    except Exception as e:
        print(f"   ❌ District seeding failed: {e}")
    
    return total_upserted


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main seeding workflow."""
    print("=" * 60)
    print("SIRAYA Health Navigator - Database Seeder")
    print("=" * 60)
    print(f"Started at: {datetime.now().isoformat()}")
    print()
    
    # Initialize Supabase client
    client = get_supabase_client()
    if not client:
        print("\n❌ ABORTED: Could not connect to Supabase")
        sys.exit(1)
    
    # Load JSON files
    print("\n📂 Loading JSON files...")
    master_kb = load_master_kb()
    districts_data = load_districts()
    
    if not master_kb and not districts_data:
        print("\n❌ ABORTED: No data to seed")
        sys.exit(1)
    
    # Seed facilities
    facilities_count = 0
    if master_kb.get("facilities"):
        facilities_count = seed_facilities(client, master_kb["facilities"])
    
    # Seed districts
    districts_count = 0
    if districts_data:
        districts_count = seed_districts(client, districts_data)
    
    # Summary
    print("\n" + "=" * 60)
    print("SEEDING COMPLETE")
    print("=" * 60)
    print(f"Facilities seeded:  {facilities_count}")
    print(f"Districts seeded:   {districts_count}")
    print(f"Completed at:       {datetime.now().isoformat()}")
    print()
    print("✅ You can now archive the local JSON files:")
    print("   - master_kb.json")
    print("   - distretti_sanitari_er.json")
    print("   - mappa_er.json")
    print()
    print("   Move them to _legacy_backup/ to complete the cleanup.")
    print("=" * 60)


if __name__ == "__main__":
    main()

