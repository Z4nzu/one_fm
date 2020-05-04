// Copyright (c) 2020, omar jaber and contributors
// For license information, please see license.txt

frappe.ui.form.on('ERF Request', {
	refresh: function(frm) {
    if(frm.doc.docstatus == 1 && frm.doc.status == 'Accepted' && frappe.user.name == frm.doc.department_manager){
      frm.add_custom_button(__('Create ERF'), function() {
        create_erf(frm);
      } );
    }
	}
});

var create_erf = function(frm) {
  frappe.route_options = {
		"erf_request": frm.doc.name
	};
	frappe.new_doc("ERF");
};
