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


def format_opening_hours(orari: Any) -> str:
    """
    Convert opening hours from dict/JSON to readable string.
    
    Args:
        orari: Can be dict, string, or None
        
    Returns:
        Formatted string like "Lun-Ven: 08:00-18:00"
    """
    if not orari or orari == 'N/D':
        return "Orari non disponibili"
    
    # If already a string, return as is
    if isinstance(orari, str):
        return orari
    
    # If dict, format it
    if isinstance(orari, dict):
        parts = []
        for day, hours in orari.items():
            if hours:
                if isinstance(hours, str):
                    parts.append(f"{day}: {hours}")
                elif isinstance(hours, dict):
                    # Handle nested dict like {"apertura": "08:00", "chiusura": "18:00"}
                    apertura = hours.get('apertura', hours.get('open', ''))
                    chiusura = hours.get('chiusura', hours.get('close', ''))
                    if apertura and chiusura:
                        parts.append(f"{day}: {apertura}-{chiusura}")
                    elif apertura:
                        parts.append(f"{day}: {apertura}")
                else:
                    parts.append(f"{day}: {hours}")
        
        if parts:
            return " | ".join(parts)
        else:
            return "Orari non disponibili"
    
    return str(orari)


def render_facility_card(facility: Dict[str, Any]) -> None:
    """Render facility info using native Streamlit components."""
    icon = get_facility_icon(facility.get('tipologia', ''))
    nome = facility.get('nome', 'N/D')
    tipologia = facility.get('tipologia', 'N/D')
    indirizzo = facility.get('indirizzo', 'N/D')
    comune = facility.get('comune', 'N/D')
    contatti = facility.get('contatti', {})
    telefono = contatti.get('telefono', 'N/D') if isinstance(contatti, dict) else 'N/D'
    orari_raw = facility.get('orari', 'N/D')
    orari = format_opening_hours(orari_raw)
    distance = facility.get('distance_km')
    
    # Use Streamlit container with border
    with st.container(border=True):
        # Header row
        header_col1, header_col2 = st.columns([4, 1])
        with header_col1:
            st.markdown(f"### {icon} {nome}")
            st.caption(f"**{tipologia}**")
        with header_col2:
            if distance:
                st.metric("Distanza", f"{distance:.1f} km", label_visibility="collapsed")
        
        st.divider()
        
        # Details
        st.markdown(f"📍 **Indirizzo:** {indirizzo}, {comune}")
        st.markdown(f"📞 **Telefono:** {telefono}")
        st.markdown(f"🕐 **Orari:** {orari}")


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
        # Fallback: use st.map or Google Maps link
        st.info("💡 Mappa interattiva non disponibile. Usa i link 'Indicazioni' per aprire Google Maps.")
        
        # Try simple map as fallback
        try:
            _render_simple_map(facilities)
        except Exception:
            # Ultimate fallback: show facilities list with Google Maps links
            st.markdown("### 📍 Strutture Trovate")
            for facility in facilities:
                nome = facility.get('nome', 'N/D')
                indirizzo = facility.get('indirizzo', '')
                comune = facility.get('comune', '')
                
                if indirizzo and comune:
                    maps_url = f"https://www.google.com/maps/search/?api=1&query={indirizzo.replace(' ', '+')},+{comune}"
                    st.markdown(f"**{nome}** - [{indirizzo}, {comune}]({maps_url})")
                else:
                    st.markdown(f"**{nome}**")


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
    st.markdown("### 🗺️ Trova Struttura Sanitaria")
    st.caption("Cerca la struttura più vicina per le tue esigenze")
    
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
        
        # Single column layout (full width) for better readability
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
        
        st.markdown("---")
        st.subheader("📋 Dettagli Strutture")
        
        # Display facilities in full-width cards using native Streamlit
        for facility in facilities[:10]:
            render_facility_card(facility)
            
            # Action buttons
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                telefono = facility.get('contatti', {}).get('telefono', '') if isinstance(facility.get('contatti'), dict) else ''
                if telefono:
                    st.link_button("📞 Chiama", f"tel:{telefono}", use_container_width=True)
            with col_b:
                indirizzo = facility.get('indirizzo', '')
                comune = facility.get('comune', '')
                if indirizzo:
                    maps_url = f"https://www.google.com/maps/search/?api=1&query={indirizzo.replace(' ', '+')},+{comune}"
                    st.link_button("🗺️ Indicazioni", maps_url, use_container_width=True)
            with col_c:
                # Show full address for copy
                full_address = f"{indirizzo}, {comune}" if indirizzo and comune else ""
                if full_address:
                    st.caption(f"📍 {full_address}")
            
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
