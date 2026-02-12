"""
SIRAYA Health Navigator - Map View
V2.0: Facility finder with DataLoader integration.

This view:
- Shows healthcare facilities on interactive map
- Searches by service type and location
- Displays facility details
- Uses DataLoader.find_facilities_smart()
"""

import streamlit as st
from typing import List, Dict, Any, Optional

from ..core.state_manager import get_state_manager, StateKeys
from ..core.authentication import check_privacy_accepted, render_privacy_consent
from ..services.data_loader import get_data_loader


# ============================================================================
# CONSTANTS
# ============================================================================

# Default map center (Bologna)
DEFAULT_LAT = 44.4949
DEFAULT_LON = 11.3426
DEFAULT_ZOOM = 9

# Facility type icons
FACILITY_ICONS = {
    "Pronto Soccorso": "🏥",
    "CAU": "🩺",
    "Casa della Salute": "🏠",
    "Guardia Medica": "⚕️",
    "Farmacia": "💊",
    "Consultorio": "👶",
    "CUP": "📋",
    "SerD": "🧠",
    "CSM": "🧠",
    "NPIA": "👦",
    "default": "📍"
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_facility_icon(tipologia: str) -> str:
    """Get icon for facility type."""
    for key, icon in FACILITY_ICONS.items():
        if key.lower() in tipologia.lower():
            return icon
    return FACILITY_ICONS["default"]


def format_facility_card(facility: Dict[str, Any]) -> str:
    """Format facility info as HTML card."""
    icon = get_facility_icon(facility.get('tipologia', ''))
    nome = facility.get('nome', 'N/D')
    tipologia = facility.get('tipologia', 'N/D')
    indirizzo = facility.get('indirizzo', 'N/D')
    comune = facility.get('comune', 'N/D')
    contatti = facility.get('contatti', {})
    telefono = contatti.get('telefono', 'N/D') if isinstance(contatti, dict) else 'N/D'
    orari = facility.get('orari', 'N/D')
    distance = facility.get('distance_km')
    
    distance_html = f"<small style='color: #10B981;'>📏 {distance:.1f} km</small>" if distance else ""
    
    return f"""
    <div style="background: white; border-radius: 12px; padding: 16px; 
                margin-bottom: 12px; border-left: 4px solid #4A90E2;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
        <div style="display: flex; justify-content: space-between; align-items: start;">
            <div>
                <h4 style="margin: 0 0 8px 0; color: #1f2937;">
                    {icon} {nome}
                </h4>
                <p style="margin: 0 0 4px 0; color: #4A90E2; font-size: 0.9em;">
                    {tipologia}
                </p>
            </div>
            {distance_html}
        </div>
        <div style="margin-top: 12px; font-size: 0.85em; color: #6b7280;">
            <p style="margin: 4px 0;">📍 {indirizzo}, {comune}</p>
            <p style="margin: 4px 0;">📞 {telefono}</p>
            <p style="margin: 4px 0;">🕐 {orari}</p>
        </div>
    </div>
    """


# ============================================================================
# MAP RENDERING
# ============================================================================

def render_folium_map(
    facilities: List[Dict[str, Any]],
    center_lat: float = DEFAULT_LAT,
    center_lon: float = DEFAULT_LON,
    zoom: int = DEFAULT_ZOOM
) -> None:
    """
    Render interactive map with Folium.
    
    Args:
        facilities: List of facility dicts with lat/lon
        center_lat: Map center latitude
        center_lon: Map center longitude
        zoom: Initial zoom level
    """
    try:
        import folium
        from streamlit_folium import st_folium
        
        # Create base map
        m = folium.Map(
            location=[center_lat, center_lon],
            zoom_start=zoom,
            tiles="cartodbpositron"
        )
        
        # Add markers for facilities
        for facility in facilities:
            lat = facility.get('lat') or facility.get('latitude')
            lon = facility.get('lon') or facility.get('longitude')
            
            if lat and lon:
                icon = get_facility_icon(facility.get('tipologia', ''))
                nome = facility.get('nome', 'Struttura')
                tipologia = facility.get('tipologia', '')
                
                popup_html = f"""
                <div style="min-width: 200px;">
                    <strong>{icon} {nome}</strong><br>
                    <small>{tipologia}</small><br>
                    <hr style="margin: 5px 0;">
                    <small>
                        📍 {facility.get('indirizzo', 'N/D')}<br>
                        📞 {facility.get('contatti', {}).get('telefono', 'N/D') if isinstance(facility.get('contatti'), dict) else 'N/D'}
                    </small>
                </div>
                """
                
                # Color based on type
                if 'pronto soccorso' in tipologia.lower():
                    color = 'red'
                elif 'cau' in tipologia.lower():
                    color = 'orange'
                elif 'farmacia' in tipologia.lower():
                    color = 'green'
                else:
                    color = 'blue'
                
                folium.Marker(
                    location=[lat, lon],
                    popup=folium.Popup(popup_html, max_width=300),
                    tooltip=nome,
                    icon=folium.Icon(color=color, icon='info-sign')
                ).add_to(m)
        
        # Render map
        st_folium(m, width=None, height=500, use_container_width=True)
        
    except ImportError:
        st.warning("⚠️ Folium o streamlit-folium non installato.")
        st.info("Installa con: `pip install folium streamlit-folium`")
        
        # Fallback: use st.map
        _render_simple_map(facilities)


def _render_simple_map(facilities: List[Dict[str, Any]]) -> None:
    """Fallback map rendering using st.map."""
    map_data = []
    
    for f in facilities:
        lat = f.get('lat') or f.get('latitude')
        lon = f.get('lon') or f.get('longitude')
        
        if lat and lon:
            map_data.append({'lat': lat, 'lon': lon})
    
    if map_data:
        st.map(map_data, zoom=9)
    else:
        st.info("Nessuna coordinata disponibile per la mappa.")


# ============================================================================
# MAIN RENDER FUNCTION
# ============================================================================

def render() -> None:
    """
    Render the map and facility finder view.
    
    Main entry point for map view.
    """
    # Check privacy
    if not check_privacy_accepted():
        render_privacy_consent()
        return
    
    # === PRE-WIDGET SESSION STATE TRANSFERS ===
    # Quick-link buttons set a temp key; transfer it BEFORE widget creation
    if "_map_quick_service" in st.session_state:
        st.session_state["map_service"] = st.session_state.pop("_map_quick_service")
    
    # === HEADER ===
    st.markdown("""
    <div style="text-align: center; padding: 10px 0;">
        <h1 style="color: #4A90E2; font-weight: 300; margin: 0;">
            🗺️ Trova Struttura Sanitaria
        </h1>
        <p style="color: #6b7280; font-size: 0.9em;">
            Cerca la struttura più vicina per le tue esigenze
        </p>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # === GET DATA LOADER ===
    data_loader = get_data_loader()
    state = get_state_manager()
    
    # === SEARCH FILTERS ===
    col1, col2 = st.columns(2)
    
    with col1:
        # Service type selection
        available_services = data_loader.get_all_available_services()
        service_options = ["Tutti i servizi"] + available_services
        
        # Initialize widget key before creation
        if "map_service" not in st.session_state:
            st.session_state["map_service"] = "Tutti i servizi"
        
        selected_service = st.selectbox(
            "🩺 Tipo di Servizio",
            service_options,
            key="map_service"
        )
    
    with col2:
        # Location input (pre-filled from triage if available)
        patient_location = state.get(StateKeys.PATIENT_LOCATION, "")
        
        location = st.text_input(
            "📍 Comune di riferimento",
            value=patient_location,
            placeholder="Es: Bologna, Modena, Parma...",
            key="map_location"
        )
    
    # === SEARCH BUTTON ===
    search_clicked = st.button("🔍 Cerca Strutture", use_container_width=True, type="primary")
    
    st.markdown("---")
    
    # === PERFORM SEARCH ===
    facilities = []
    
    if search_clicked or (location and selected_service != "Tutti i servizi"):
        with st.spinner("🔍 Ricerca in corso..."):
            if selected_service == "Tutti i servizi":
                # Search by location only
                facilities = data_loader.find_facilities_by_location(location)
            else:
                # Smart search by service and location
                facilities = data_loader.find_facilities_smart(
                    query_service=selected_service,
                    query_comune=location if location else "Bologna",
                    limit=10
                )
    
    # === DISPLAY RESULTS ===
    if facilities:
        st.success(f"✅ Trovate **{len(facilities)}** strutture")
        
        # Two columns: Map and List
        col_map, col_list = st.columns([2, 1])
        
        with col_map:
            st.subheader("📍 Mappa")
            
            # Get center from patient location or first facility
            if location:
                coords = data_loader.get_comune_coordinates(location)
                if isinstance(coords, tuple):
                    center_lat, center_lon = coords
                elif isinstance(coords, dict):
                    center_lat = coords.get('lat', DEFAULT_LAT)
                    center_lon = coords.get('lon', DEFAULT_LON)
                else:
                    center_lat, center_lon = DEFAULT_LAT, DEFAULT_LON
            elif facilities:
                first = facilities[0]
                center_lat = first.get('lat') or first.get('latitude') or DEFAULT_LAT
                center_lon = first.get('lon') or first.get('longitude') or DEFAULT_LON
            else:
                center_lat, center_lon = DEFAULT_LAT, DEFAULT_LON
            
            render_folium_map(facilities, center_lat, center_lon)
        
        with col_list:
            st.subheader("📋 Risultati")
            
            for facility in facilities[:10]:
                st.markdown(format_facility_card(facility), unsafe_allow_html=True)
                
                # Action buttons
                col_a, col_b = st.columns(2)
                with col_a:
                    telefono = facility.get('contatti', {}).get('telefono', '') if isinstance(facility.get('contatti'), dict) else ''
                    if telefono:
                        st.markdown(f"[📞 Chiama]({telefono})")
                with col_b:
                    indirizzo = facility.get('indirizzo', '')
                    comune = facility.get('comune', '')
                    if indirizzo:
                        maps_url = f"https://www.google.com/maps/search/?api=1&query={indirizzo.replace(' ', '+')},+{comune}"
                        st.markdown(f"[🗺️ Indicazioni]({maps_url})")
                
                st.markdown("---")
    
    elif search_clicked:
        st.warning("⚠️ Nessuna struttura trovata. Prova a modificare i criteri di ricerca.")
    
    else:
        # Show default facilities or prompt
        st.info("👆 Seleziona un servizio e/o un comune per iniziare la ricerca.")
        
        # Show quick links
        st.markdown("### 🚀 Ricerche Rapide")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            if st.button("🏥 Pronto Soccorso", use_container_width=True):
                st.session_state["_map_quick_service"] = "Pronto Soccorso"
                st.rerun()
        
        with col2:
            if st.button("🩺 CAU", use_container_width=True):
                st.session_state["_map_quick_service"] = "CAU"
                st.rerun()
        
        with col3:
            if st.button("💊 Farmacia", use_container_width=True):
                st.session_state["_map_quick_service"] = "Farmacia"
                st.rerun()
    
    # === BACK BUTTON ===
    st.markdown("---")
    if st.button("← Torna al Chatbot", use_container_width=True):
        from ..core.navigation import switch_to, PageName
        switch_to(PageName.CHAT)


# ============================================================================
# EMERGENCY FACILITIES QUICK VIEW
# ============================================================================

def render_emergency_facilities(location: str) -> None:
    """
    Quick view for emergency: show nearest PS and CAU.
    
    Args:
        location: Patient's location
    """
    data_loader = get_data_loader()
    
    st.markdown("### 🚨 Strutture di Emergenza più vicine")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("#### 🏥 Pronto Soccorso")
        ps_facilities = data_loader.find_facilities_smart("Pronto Soccorso", location, limit=3)
        
        if ps_facilities:
            for ps in ps_facilities:
                st.markdown(f"""
                **{ps.get('nome', 'N/D')}**  
                📍 {ps.get('comune', 'N/D')}  
                📞 {ps.get('contatti', {}).get('telefono', 'N/D') if isinstance(ps.get('contatti'), dict) else 'N/D'}
                """)
                st.divider()
        else:
            st.info("Nessun PS trovato")
    
    with col2:
        st.markdown("#### 🩺 CAU (Centro Assistenza Urgenze)")
        cau_facilities = data_loader.find_facilities_smart("CAU", location, limit=3)
        
        if cau_facilities:
            for cau in cau_facilities:
                st.markdown(f"""
                **{cau.get('nome', 'N/D')}**  
                📍 {cau.get('comune', 'N/D')}  
                📞 {cau.get('contatti', {}).get('telefono', 'N/D') if isinstance(cau.get('contatti'), dict) else 'N/D'}
                """)
                st.divider()
        else:
            st.info("Nessun CAU trovato")
