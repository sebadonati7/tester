"""
SIRAYA Config Package
Contains application settings and styles.
"""
from .settings import (
    Settings, 
    APIConfig, 
    SupabaseConfig, 
    UITheme, 
    TriageConfig, 
    RAGConfig, 
    PATHS, 
    EMERGENCY_RULES, 
    ClinicalMappings
)

# Backward compatibility
API_CONFIG = APIConfig
UI_THEME = UITheme
TRIAGE_CONFIG = TriageConfig
