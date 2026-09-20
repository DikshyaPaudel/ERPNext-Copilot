import frappe
from frappe.model.document import Document


class CopilotToolCall(Document):
	def validate(self):
		"""Once a tool call is logged, what was actually requested (tool_name,
		arguments) must never change — only the outcome (status, result,
		completed_at) is allowed to update as the call resolves. This is what
		makes the log trustworthy as an audit trail rather than just another
		mutable record: even the user who triggered the call can't quietly
		edit what it says they asked for after the fact.
		"""
		if self.is_new():
			return

		before = self.get_doc_before_save()
		if not before:
			return

		if self.tool_name != before.tool_name:
			frappe.throw("tool_name cannot be changed after a tool call has been logged.")
		if self.arguments != before.arguments:
			frappe.throw("arguments cannot be changed after a tool call has been logged.")