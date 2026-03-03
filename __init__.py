"""Utils package - id_manager opzionale (legacy)"""
try:
    from .id_manager import get_new_session_id, IDManager
except ImportError:
    get_new_session_id = None
    IDManager = None

__all__ = ['get_new_session_id', 'IDManager']
