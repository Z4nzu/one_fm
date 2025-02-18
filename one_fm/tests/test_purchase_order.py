import frappe,json
from frappe.tests.utils import FrappeTestCase
import unittest


class TestPurchaseOrder_(FrappeTestCase):
    def test_validate_custom_fields(self):
        doctype_meta = frappe.get_meta("Purchase Order")
        field_order = json.loads(doctype_meta.field_order)
        self.assertIn('custom_place_of_delivery', field_order)
        self.assertIn('custom_terms_of_shipment', field_order)
        