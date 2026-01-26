"""
SIRAYA Health Navigator - Admin Controller
V1.0: Manages admin/dashboard interactions.

This controller:
- Handles dashboard filters
- Manages data export
- Controls admin functions
"""

import streamlit as st
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from ..core.state_manager import get_state_manager
from ..core.authentication import get_auth_manager
from ..services.analytics_service import get_analytics_service


class AdminController:
    """
    Controller for admin dashboard operations.
    
    Handles filtering, export, and admin actions.
    """
    
    def __init__(self):
        """Initialize controller."""
        self.state = get_state_manager()
        self.auth = get_auth_manager()
        self.analytics = get_analytics_service()
    
    def login(self, password: str) -> bool:
        """
        Attempt admin login.
        
        Args:
            password: Admin password
            
        Returns:
            True if successful
        """
        return self.auth.admin_login(password)
    
    def logout(self) -> None:
        """Log out admin user."""
        self.auth.admin_logout()
    
    def is_authenticated(self) -> bool:
        """
        Check if admin is logged in.
        
        Returns:
            True if authenticated
        """
        return self.auth.is_admin_logged_in()
    
    def get_filtered_logs(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        district: Optional[str] = None,
        urgency_min: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get logs with filters applied.
        
        Args:
            date_from: Start date filter
            date_to: End date filter
            district: District filter
            urgency_min: Minimum urgency filter
            
        Returns:
            Filtered list of logs
        """
        logs = self.analytics.get_all_logs()
        
        filtered = []
        for log in logs:
            # Date filter
            if date_from or date_to:
                try:
                    timestamp_str = log.get("created_at") or log.get("timestamp")
                    if timestamp_str:
                        timestamp = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                        
                        if date_from and timestamp < date_from:
                            continue
                        if date_to and timestamp > date_to:
                            continue
                except:
                    continue
            
            # District filter
            if district and district != "Tutti":
                import json
                try:
                    metadata = json.loads(log.get("metadata", "{}"))
                    log_district = metadata.get("district", "")
                    if district.lower() not in log_district.lower():
                        continue
                except:
                    pass
            
            # Urgency filter
            if urgency_min:
                import json
                try:
                    metadata = json.loads(log.get("metadata", "{}"))
                    urgency = metadata.get("urgency", 3)
                    if urgency < urgency_min:
                        continue
                except:
                    pass
            
            filtered.append(log)
        
        return filtered
    
    def export_to_csv(self, logs: List[Dict]) -> bytes:
        """
        Export logs to CSV format.
        
        Args:
            logs: List of log records
            
        Returns:
            CSV as bytes
        """
        import csv
        import io
        
        output = io.StringIO()
        
        if not logs:
            return b"No data available"
        
        # Get all possible fields
        fields = set()
        for log in logs:
            fields.update(log.keys())
        
        fields = sorted(list(fields))
        
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        
        for log in logs:
            writer.writerow(log)
        
        return output.getvalue().encode('utf-8-sig')
    
    def export_to_excel(self, logs: List[Dict]) -> Optional[bytes]:
        """
        Export logs to Excel format.
        
        Args:
            logs: List of log records
            
        Returns:
            Excel file as bytes, or None if xlsxwriter not available
        """
        try:
            import xlsxwriter
            import io
            
            output = io.BytesIO()
            workbook = xlsxwriter.Workbook(output, {'in_memory': True})
            
            # Create worksheet
            ws = workbook.add_worksheet('Logs')
            
            # Formats
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#4A90E2',
                'font_color': 'white'
            })
            
            if not logs:
                ws.write(0, 0, "No data available")
                workbook.close()
                output.seek(0)
                return output.read()
            
            # Get fields
            fields = sorted(list(set(
                key for log in logs for key in log.keys()
            )))
            
            # Write header
            for col, field in enumerate(fields):
                ws.write(0, col, field, header_format)
            
            # Write data
            for row, log in enumerate(logs, 1):
                for col, field in enumerate(fields):
                    value = log.get(field, "")
                    ws.write(row, col, str(value) if value else "")
            
            workbook.close()
            output.seek(0)
            return output.read()
            
        except ImportError:
            return None
    
    def get_kpi_summary(self) -> Dict[str, Any]:
        """
        Get summary KPIs for dashboard.
        
        Returns:
            Dictionary with KPI values
        """
        logs = self.analytics.get_all_logs()
        
        if not logs:
            return {
                "total_sessions": 0,
                "total_interactions": 0,
                "avg_urgency": 0,
                "completion_rate": 0,
                "critical_cases_24h": 0,
            }
        
        # Calculate KPIs
        sessions = set(log.get("session_id") for log in logs)
        
        urgencies = []
        for log in logs:
            import json
            try:
                metadata = json.loads(log.get("metadata", "{}"))
                urgencies.append(metadata.get("urgency", 3))
            except:
                urgencies.append(3)
        
        # Critical cases in last 24h
        cutoff = datetime.now() - timedelta(hours=24)
        critical_24h = 0
        for log in logs:
            try:
                timestamp_str = log.get("created_at") or log.get("timestamp")
                if timestamp_str:
                    timestamp = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                    if timestamp >= cutoff:
                        import json
                        metadata = json.loads(log.get("metadata", "{}"))
                        if metadata.get("urgency", 3) >= 4:
                            critical_24h += 1
            except:
                continue
        
        return {
            "total_sessions": len(sessions),
            "total_interactions": len(logs),
            "avg_urgency": sum(urgencies) / len(urgencies) if urgencies else 0,
            "completion_rate": 75.0,  # Placeholder
            "critical_cases_24h": critical_24h,
        }
    
    def clear_old_logs(self, days: int = 30) -> int:
        """
        Clear logs older than specified days.
        
        Args:
            days: Number of days to keep
            
        Returns:
            Number of records deleted
        """
        # This would need Supabase delete capability
        # For now, return 0
        return 0


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_admin_controller: Optional[AdminController] = None


def get_admin_controller() -> AdminController:
    """Get singleton admin controller instance."""
    global _admin_controller
    if _admin_controller is None:
        _admin_controller = AdminController()
    return _admin_controller

