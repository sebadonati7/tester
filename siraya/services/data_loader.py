"""
SIRAYA Health Navigator - Data Loader Service
V2.0: Supabase-Native with JSON Fallback

This service:
- Primary: Queries Supabase for facilities
- Fallback: Reads from local JSON files
- Provides facility search with fuzzy matching
- Calculates geographic distances
- Caches heavy queries with TTL
"""

import json
import difflib
import streamlit as st
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
from functools import lru_cache

from ..config.settings import (
    PATHS, SupabaseConfig, haversine_distance, ClinicalMappings
)


# ============================================================================
# SUPABASE CLIENT
# ============================================================================

@st.cache_resource
def get_supabase_client():
    """
    Get Supabase client with connection pooling.
    
    Returns:
        Supabase client or None
    """
    if not SupabaseConfig.is_configured():
        return None
    
    try:
        from supabase import create_client
        
        client = create_client(
            SupabaseConfig.get_url(),
            SupabaseConfig.get_key()
        )
        return client
    except ImportError:
        print("⚠️ supabase library not installed")
        return None
    except Exception as e:
        print(f"❌ Supabase connection error: {e}")
        return None


# ============================================================================
# LOCAL JSON LOADERS (FALLBACK)
# ============================================================================

@st.cache_data(ttl=3600)
def _load_local_master_kb() -> Dict[str, Any]:
    """Load master KB from local JSON file."""
    filepath = PATHS.MASTER_KB
    
    if not filepath.exists():
        return {"facilities": [], "metadata": {}}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading master KB: {e}")
        return {"facilities": [], "metadata": {}}


@st.cache_data(ttl=3600)
def _load_local_districts() -> Dict[str, Any]:
    """Load districts from local JSON file."""
    filepath = PATHS.DISTRICTS
    
    if not filepath.exists():
        return {"health_districts": [], "comune_to_district_mapping": {}}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading districts: {e}")
        return {"health_districts": [], "comune_to_district_mapping": {}}


@st.cache_data(ttl=3600)
def _load_local_map_data() -> Dict[str, Any]:
    """Load map data from local JSON file."""
    filepath = PATHS.MAP_DATA
    
    if not filepath.exists():
        return {"objects": {}}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading map data: {e}")
        return {"objects": {}}


# ============================================================================
# DATA LOADER CLASS
# ============================================================================

class DataLoader:
    """
    Central data access service.
    
    Features:
    - Supabase-first with JSON fallback
    - Geographic calculations
    - Fuzzy matching for searches
    - Service catalog building
    """
    
    def __init__(self):
        """Initialize data loader with Supabase client."""
        self._client = get_supabase_client()
        self._use_supabase = self._client is not None
        
        # Cache for loaded data
        self._facilities_cache: Optional[List[Dict]] = None
        self._comuni_cache: Optional[Set[str]] = None
        self._districts_cache: Optional[Dict] = None
    
    # ========================================================================
    # FACILITIES ACCESS
    # ========================================================================
    
    def get_all_facilities(self) -> List[Dict[str, Any]]:
        """
        Get all healthcare facilities.
        
        Returns:
            List of facility dictionaries
        """
        if self._facilities_cache is not None:
            return self._facilities_cache
        
        # Try Supabase first
        if self._use_supabase:
            try:
                response = self._client.table(SupabaseConfig.TABLE_FACILITIES).select("*").execute()
                if response.data:
                    self._facilities_cache = response.data
                    return self._facilities_cache
            except Exception as e:
                print(f"Supabase facilities query failed: {e}")
        
        # Fallback to local JSON
        kb = _load_local_master_kb()
        self._facilities_cache = kb.get("facilities", [])
        return self._facilities_cache
    
    def get_facilities_by_type(self, facility_type: str) -> List[Dict[str, Any]]:
        """
        Get facilities filtered by type.
        
        Args:
            facility_type: Type to filter (e.g., "CAU", "Pronto Soccorso")
            
        Returns:
            Filtered list of facilities
        """
        # Try Supabase first
        if self._use_supabase:
            try:
                response = (
                    self._client.table(SupabaseConfig.TABLE_FACILITIES)
                    .select("*")
                    .ilike("tipologia", f"%{facility_type}%")
                    .execute()
                )
                if response.data:
                    return response.data
            except Exception as e:
                print(f"Supabase type filter failed: {e}")
        
        # Fallback to local filtering
        facilities = self.get_all_facilities()
        type_lower = facility_type.lower()
        
        return [
            f for f in facilities
            if type_lower in f.get("tipologia", "").lower()
        ]
    
    def get_facilities_by_comune(self, comune: str) -> List[Dict[str, Any]]:
        """
        Get facilities in a specific municipality.
        
        Args:
            comune: Municipality name
            
        Returns:
            List of facilities in that municipality
        """
        # Try Supabase first
        if self._use_supabase:
            try:
                response = (
                    self._client.table(SupabaseConfig.TABLE_FACILITIES)
                    .select("*")
                    .ilike("comune", comune)
                    .execute()
                )
                if response.data:
                    return response.data
            except Exception as e:
                print(f"Supabase comune filter failed: {e}")
        
        # Fallback to local filtering
        facilities = self.get_all_facilities()
        comune_lower = comune.lower().strip()
        
        return [
            f for f in facilities
            if comune_lower == f.get("comune", "").lower().strip()
        ]
    
    # ========================================================================
    # SMART FACILITY SEARCH (from frontend.py)
    # ========================================================================
    
    def find_facilities_smart(
        self,
        query_service: str,
        query_comune: str,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Smart hierarchical search with proximity scoring.
        
        Score system:
        - 3: Same municipality
        - 2: Same district
        - 1: Same province
        
        Supports substring matching (e.g., "visita" in "visita ginecologica").
        
        Args:
            query_service: Service or facility type to search
            query_comune: User's municipality
            limit: Maximum results
            
        Returns:
            Sorted list of facilities by proximity
        """
        facilities = self.get_all_facilities()
        districts = self.load_districts()
        
        results = []
        qs = query_service.lower()
        qc = query_comune.lower().strip()
        
        # Get district for user's comune
        comune_district = districts.get("comune_to_district_mapping", {}).get(qc, "")
        
        for item in facilities:
            # Match service/type (fuzzy substring matching)
            servizi = [s.lower() for s in item.get('servizi_disponibili', [])]
            tipo = item.get('tipologia', '').lower()
            
            match_servizio = qs in tipo or any(qs in s for s in servizi)
            
            if match_servizio:
                # Calculate proximity score
                score = 0
                item_comune = item.get('comune', '').lower()
                item_dist = item.get('distretto', '').lower()
                item_prov = item.get('provincia', '').lower()
                
                if qc == item_comune:
                    score = 3
                elif comune_district and comune_district.lower() in item_dist:
                    score = 2
                elif qc in item_prov or item_prov in qc:
                    score = 1
                
                if score > 0:
                    results.append({"data": item, "score": score})
        
        # Sort by score descending
        results.sort(key=lambda x: x['score'], reverse=True)
        return [r['data'] for r in results[:limit]]
    
    def find_nearest_facilities_geo(
        self,
        lat: float,
        lon: float,
        facility_type: Optional[str] = None,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Find nearest facilities using geographic coordinates.
        
        Args:
            lat: User latitude
            lon: User longitude
            facility_type: Optional type filter
            limit: Maximum results
            
        Returns:
            List of facilities sorted by distance
        """
        facilities = self.get_all_facilities()
        map_data = _load_local_map_data()
        
        # Get comune coordinates mapping
        comune_coords = {}
        geometries = map_data.get("objects", {}).get("comuni", {}).get("geometries", [])
        for geom in geometries:
            props = geom.get("properties", {})
            name = props.get("name", "").lower()
            if "lat" in props and "lon" in props:
                comune_coords[name] = (props["lat"], props["lon"])
        
        results = []
        
        for facility in facilities:
            # Filter by type if specified
            if facility_type:
                if facility_type.lower() not in facility.get("tipologia", "").lower():
                    continue
            
            # Get facility coordinates from its comune
            facility_comune = facility.get("comune", "").lower()
            if facility_comune in comune_coords:
                f_lat, f_lon = comune_coords[facility_comune]
                distance = haversine_distance(lat, lon, f_lat, f_lon)
                
                results.append({
                    "facility": facility,
                    "distance_km": round(distance, 2)
                })
        
        # Sort by distance
        results.sort(key=lambda x: x["distance_km"])
        
        return [
            {**r["facility"], "distance_km": r["distance_km"]}
            for r in results[:limit]
        ]
    
    # ========================================================================
    # DISTRICTS & COMUNI
    # ========================================================================
    
    def load_districts(self) -> Dict[str, Any]:
        """
        Load health districts mapping.
        
        Returns:
            Districts data with comune mapping
        """
        if self._districts_cache is not None:
            return self._districts_cache
        
        self._districts_cache = _load_local_districts()
        return self._districts_cache
    
    def get_all_comuni(self) -> List[str]:
        """
        Get list of all known municipalities.
        
        Returns:
            Sorted list of comune names
        """
        if self._comuni_cache is not None:
            return sorted([c.title() for c in self._comuni_cache])
        
        # Try to load from map data
        map_data = _load_local_map_data()
        geometries = map_data.get("objects", {}).get("comuni", {}).get("geometries", [])
        
        self._comuni_cache = {
            g["properties"]["name"].lower().strip()
            for g in geometries
            if "name" in g.get("properties", {})
        }
        
        # Fallback if empty
        if not self._comuni_cache:
            self._comuni_cache = {
                "bologna", "modena", "parma", "reggio emilia", "ferrara",
                "ravenna", "rimini", "forlì", "piacenza", "cesena"
            }
        
        return sorted([c.title() for c in self._comuni_cache])
    
    def is_valid_comune_er(self, comune: str) -> bool:
        """
        Validate if a comune is in Emilia-Romagna.
        
        Uses fuzzy matching for typos/accents.
        
        Args:
            comune: Municipality name to validate
            
        Returns:
            True if valid ER comune
        """
        if not comune or not isinstance(comune, str):
            return False
        
        nome = comune.lower().strip()
        comuni = self.get_all_comuni()
        comuni_lower = {c.lower() for c in comuni}
        
        # Exact match
        if nome in comuni_lower:
            return True
        
        # Fuzzy match for typos
        matches = difflib.get_close_matches(nome, list(comuni_lower), n=1, cutoff=0.8)
        return len(matches) > 0
    
    def get_comune_coordinates(self, comune: str) -> Optional[Tuple[float, float]]:
        """
        Get coordinates for a municipality.
        
        Args:
            comune: Municipality name
            
        Returns:
            Tuple of (lat, lon) or None
        """
        map_data = _load_local_map_data()
        geometries = map_data.get("objects", {}).get("comuni", {}).get("geometries", [])
        
        comune_lower = comune.lower().strip()
        
        for geom in geometries:
            props = geom.get("properties", {})
            if props.get("name", "").lower() == comune_lower:
                if "lat" in props and "lon" in props:
                    return (props["lat"], props["lon"])
        
        return None
    
    def get_district_for_comune(self, comune: str) -> Optional[str]:
        """
        Get district code for a municipality.
        
        Args:
            comune: Municipality name
            
        Returns:
            District code or None
        """
        districts = self.load_districts()
        mapping = districts.get("comune_to_district_mapping", {})
        return mapping.get(comune.lower().strip())
    
    def get_ausl_list(self) -> List[str]:
        """
        Get list of all AUSL names.
        
        Returns:
            List of AUSL names
        """
        districts = self.load_districts()
        return [d.get("ausl", "") for d in districts.get("health_districts", [])]
    
    # ========================================================================
    # SERVICE CATALOG
    # ========================================================================
    
    def get_all_available_services(self) -> List[str]:
        """
        Build catalog of all available services and facility types.
        
        Returns:
            Sorted list of unique service types
        """
        facilities = self.get_all_facilities()
        catalog = set()
        
        for facility in facilities:
            # Add facility type
            if facility.get("tipologia"):
                catalog.add(facility["tipologia"])
            
            # Add individual services
            for service in facility.get("servizi_disponibili", []):
                if service:
                    catalog.add(service)
        
        return sorted([s for s in catalog if s])


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_data_loader: Optional[DataLoader] = None


def get_data_loader() -> DataLoader:
    """Get singleton DataLoader instance."""
    global _data_loader
    if _data_loader is None:
        _data_loader = DataLoader()
    return _data_loader


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def load_master_kb() -> Dict[str, Any]:
    """Load master knowledge base."""
    return _load_local_master_kb()


def load_districts() -> Dict[str, Any]:
    """Load health districts."""
    return _load_local_districts()


def load_map_data() -> Dict[str, Any]:
    """Load geographic map data."""
    return _load_local_map_data()


def get_all_facilities() -> List[Dict[str, Any]]:
    """Get all facilities via DataLoader."""
    return get_data_loader().get_all_facilities()


def find_nearest_facilities(
    comune: str,
    facility_type: Optional[str] = None,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """Find nearest facilities via DataLoader."""
    return get_data_loader().find_facilities_smart(
        facility_type or "",
        comune,
        limit
    )


def get_all_comuni() -> List[str]:
    """Get all comuni via DataLoader."""
    return get_data_loader().get_all_comuni()
