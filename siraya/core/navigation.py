"""
SIRAYA Health Navigator - Navigation
V1.0: Il "Navigatore" - Handles page routing and transitions.

This module provides:
- Page constants
- Navigation functions
- Transition logic with st.rerun()
"""

import streamlit as st
from typing import Optional, Callable
from enum import Enum


# ============================================================================
# PAGE DEFINITIONS
# ============================================================================

class PageName(str, Enum):
    """
    Available pages in the application.
    
    Using Enum ensures type safety and IDE autocompletion.
    """
    LANDING = "LANDING"
    CHAT = "CHAT"
    MAP = "MAP"
    REPORT = "REPORT"
    DASHBOARD = "DASHBOARD"
    ADMIN = "ADMIN"


# Page metadata for UI rendering
PAGE_CONFIG = {
    PageName.LANDING: {
        "title": "Benvenuto",
        "icon": "🏠",
        "requires_privacy": False,
        "requires_admin": False,
    },
    PageName.CHAT: {
        "title": "Chatbot Triage",
        "icon": "💬",
        "requires_privacy": True,
        "requires_admin": False,
    },
    PageName.MAP: {
        "title": "Mappa Strutture",
        "icon": "🗺️",
        "requires_privacy": True,
        "requires_admin": False,
    },
    PageName.REPORT: {
        "title": "Report SBAR",
        "icon": "📋",
        "requires_privacy": True,
        "requires_admin": False,
    },
    PageName.DASHBOARD: {
        "title": "Analytics Dashboard",
        "icon": "📊",
        "requires_privacy": False,
        "requires_admin": True,
    },
    PageName.ADMIN: {
        "title": "Admin Panel",
        "icon": "⚙️",
        "requires_privacy": False,
        "requires_admin": True,
    },
}


# ============================================================================
# NAVIGATION CLASS
# ============================================================================

class Navigation:
    """
    Handles all page navigation logic.
    
    Features:
    - Clean page transitions
    - History tracking
    - Permission checks
    
    Usage:
        from core.navigation import Navigation
        
        nav = Navigation()
        nav.go_to(PageName.CHAT)
    """
    
    STATE_KEY_CURRENT = "current_page"
    STATE_KEY_PREVIOUS = "previous_page"
    
    def __init__(self):
        """Initialize navigation with default page."""
        self._ensure_state()
    
    def _ensure_state(self) -> None:
        """Ensure navigation state keys exist."""
        if self.STATE_KEY_CURRENT not in st.session_state:
            st.session_state[self.STATE_KEY_CURRENT] = PageName.CHAT.value
        if self.STATE_KEY_PREVIOUS not in st.session_state:
            st.session_state[self.STATE_KEY_PREVIOUS] = None
    
    @property
    def current_page(self) -> str:
        """Get current page name."""
        self._ensure_state()
        return st.session_state.get(self.STATE_KEY_CURRENT, PageName.CHAT.value)
    
    @property
    def previous_page(self) -> Optional[str]:
        """Get previous page name (for back navigation)."""
        return st.session_state.get(self.STATE_KEY_PREVIOUS)
    
    def go_to(self, page: PageName, rerun: bool = True) -> None:
        """
        Navigate to a specific page.
        
        Args:
            page: Target page (use PageName enum)
            rerun: Whether to call st.rerun() after navigation
        """
        self._ensure_state()
        
        # Store current as previous
        st.session_state[self.STATE_KEY_PREVIOUS] = st.session_state[self.STATE_KEY_CURRENT]
        
        # Set new page
        st.session_state[self.STATE_KEY_CURRENT] = page.value if isinstance(page, PageName) else page
        
        if rerun:
            st.rerun()
    
    def go_back(self, default: PageName = PageName.CHAT) -> None:
        """
        Navigate to previous page.
        
        Args:
            default: Page to go to if no previous page exists
        """
        previous = self.previous_page
        if previous:
            self.go_to(PageName(previous))
        else:
            self.go_to(default)
    
    def is_current(self, page: PageName) -> bool:
        """
        Check if given page is current.
        
        Args:
            page: Page to check
            
        Returns:
            True if page is current
        """
        current = self.current_page
        target = page.value if isinstance(page, PageName) else page
        return current == target
    
    def get_page_config(self, page: Optional[PageName] = None) -> dict:
        """
        Get configuration for a page.
        
        Args:
            page: Page to get config for (default: current page)
            
        Returns:
            Page configuration dictionary
        """
        if page is None:
            page = PageName(self.current_page)
        return PAGE_CONFIG.get(page, PAGE_CONFIG[PageName.CHAT])


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

_navigation: Optional[Navigation] = None


def get_navigation() -> Navigation:
    """Get singleton Navigation instance."""
    global _navigation
    if _navigation is None:
        _navigation = Navigation()
    return _navigation


def switch_to(page: PageName) -> None:
    """
    Switch to a page (pure function).
    
    Args:
        page: Target page
    """
    nav = get_navigation()
    nav.go_to(page)


def get_current_page() -> str:
    """Get current page name."""
    nav = get_navigation()
    return nav.current_page


def go_to_chat() -> None:
    """Navigate to chat page."""
    switch_to(PageName.CHAT)


def go_to_dashboard() -> None:
    """Navigate to dashboard page."""
    switch_to(PageName.DASHBOARD)


def go_to_map() -> None:
    """Navigate to map page."""
    switch_to(PageName.MAP)


def go_to_report() -> None:
    """Navigate to report page."""
    switch_to(PageName.REPORT)


def go_back() -> None:
    """Navigate to previous page."""
    nav = get_navigation()
    nav.go_back()

