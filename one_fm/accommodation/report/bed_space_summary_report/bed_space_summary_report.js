// Copyright (c) 2016, omar jaber and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["Bed Space Summary Report"] = {
	"filters": [
		{
			"fieldname":"gender",
			"label": __("Gender"),
			"fieldtype": "Link",
			"options": "Gender"
		},
		{
			"fieldname":"bed_space_type",
			"label": __("Bed Space Type"),
			"fieldtype": "Link",
			"options": "Bed Space Type"
		},
	]
};
