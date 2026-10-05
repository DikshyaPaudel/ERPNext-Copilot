# Copyright (c) 2025 Dikshya Paudel
# MIT License - see license.txt

import frappe


def has_app_permission():
    """Check if user has permission to access the Erpnext Copilot app."""
    return "System Manager" in frappe.get_roles() or "Erpnext Copilot User" in frappe.get_roles()