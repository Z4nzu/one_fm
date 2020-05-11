// Copyright (c) 2016, omar jaber and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Supplier Purchase Order"] = {
	"filters": [
		{
			"fieldname":"requested_by",
			"label": __("Requested By"),
			"fieldtype": "Link",
			"options": "Employee"
		},
		{
			"fieldname":"supplier",
			"label": __("Supplier"),
			"fieldtype": "Link",
			"options": "Supplier"
		},
		{
			"fieldname":"from_date",
			"label": __("From"),
			"fieldtype": "Date"
		},
		{
			"fieldname":"to_date",
			"label": __("To"),
			"fieldtype": "Date"
		},
		{
			"fieldname":"project",
			"label": __("Project"),
			"fieldtype": "Link",
			"options": "Project"
		},
		{
			"fieldname":"status",
			"label":__("Status"),
			"fieldtype":"Select",
			"options":["Waiting for finance approval", "Waiting for management approval", "Approved and sent to supplier", "Delivered", "Closed"],
			"default":"Submitted"
		}
	]
}

