# Copyright (c) 2022, omar jaber and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import *

def execute(filters=None):
	if not filters: filters = {}

	if not (filters.month and filters.year):
		msgprint(_("Please select month and year"), raise_exception=1)
	
	columns, data =  get_columns(filters), get_data(filters)
	return columns, data

def get_columns(filters):
	return [
		{
			"label": ("Employee ID"),
			"fieldname": "employee_id",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 120,
		},
		{
			"label": ("Employee Name"),
			"fieldname": "employee_name",
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"label": ("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 120,
		},
		{
			"label": ("Work Permit Salary"),
			"fieldname": "work_permit_salary",
			"fieldtype": "Currency",
			"width": 150,
		},
		{
			"label": ("Base Salary"),
			"fieldname": "base",
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"label": ("Civil ID"),
			"fieldname": "one_fm_civil_id",
			"options": "Journal Entry",
			"width": 140,
		},
		{
			"label": ("Shoon File"),
			"fieldname": "shoon_file",
			"fieldtype": "Link",
			"options": "PAM File",
			"width": 120,
		},
		{
			"label": ("Bank Account"),
			"fieldname": "bank_ac_no",
			"fieldtype": "Link",
			"options": "Bank Account",
			"width": 120,
		},
		{
			"label": ("Start Date"),
			"fieldname": "start_date",
			"fieldtype": "Date",
			"width": 120,
		},
		{
			"label": ("End Date"),
			"fieldname": "end_date",
			"fieldtype": "Date",
			"width": 120,
		},
		{
			"label": ("Day off Type"),
			"fieldname": "day_off_category",
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"label": ("No. of Days Off"),
			"fieldname": "number_of_days_off",
			"fieldtype": "Int",
			"width": 120,
		},
		{
			"label": ("Woring Days (basic)"),
			"fieldname": "working_days",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},

		{
			"label": ("Working Days (OT)"),
			"fieldname": "ot",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		{
			"label": ("Days Off (OT)"),
			"fieldname": "do_ot",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		{
			"label": ("Sick Leave"),
			"fieldname": "sl",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		{
			"label": ("Annual Leave"),
			"fieldname": "al",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		{
			"label": ("Other Leave"),
			"fieldname": "ol",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		{
			"label": ("Total"),
			"fieldname": "total",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		{
			"label": ("No. of Absent"),
			"fieldname": "ab",
			"fieldtype": "Int",
			"default": 0,
			"width": 180,
		},
		
	]
def get_data(filters):
	data = []
	query = frappe.db.sql(f"""
		SELECT DISTINCT e.name as employee_id, e.employee_name, e.project, e.work_permit_salary, e.one_fm_civil_id, e.bank_ac_no,
		e.day_off_category, e.pam_file_number as shoon_file, ssa.base, pe.start_date, pe.end_date,
		COUNT(at.employee) as working_days
		FROM `tabEmployee` e JOIN `tabSalary Structure Assignment` ssa ON e.name=ssa.employee
			JOIN `tabPayroll Employee Detail` ped ON e.name=ped.employee 
			JOIN `tabPayroll Entry` pe ON pe.name=ped.parent
			JOIN `tabAttendance` at ON at.employee=e.name
		WHERE ssa.docstatus=1 AND pe.posting_date LIKE '{filters.year}-{str(filters.month).zfill(2)}%' 
			AND pe.docstatus=1
			AND at.attendance_date BETWEEN pe.start_date AND pe.end_date
			AND at.roster_type='Basic'
		GROUP BY e.name
		ORDER BY e.name ASC
	""", as_dict=1)


	payroll_cycle = get_payroll_cycle(filters)
	employee_list = get_employee_list(query)

	ot_dict = frappe._dict({})
	attendance_by_project = get_attendance(payroll_cycle, employee_list)	

	for i in query:
		if payroll_cycle.get(i.project):
			i.start_date = payroll_cycle.get(i.project)['start_date']
			i.end_date = payroll_cycle.get(i.project)['end_date']
		
		if attendance_by_project.get(i.employee_id):
			att_project = attendance_by_project.get(i.employee_id)
			i.working_days = att_project['working_days']
			i.ot = att_project['ot']
			i.do_ot = att_project['do_ot']	
			i.sl = att_project['sl']
			i.al = att_project['al']
			i.ol = att_project['ol']		
			i.ab = att_project['ab']
			i.number_of_days_off = att_project['number_of_days_off']	
			i.total = i.working_days + i.sl + i.al + i.ol + i.ab

	if not query:
		frappe.msgprint(("No Payroll Submitted this month!"), alert=True, indicator="Blue")
	
	return query

def get_employee_list(query):
	employee = []
	for q in query:
		if q.employee_id not in employee:
			employee.append(q.employee_id)
	return employee


def get_payroll_cycle(filters):
	settings = frappe.get_doc("HR and Payroll Additional Settings").project_payroll_cycle
	default_date = frappe.get_doc("HR and Payroll Additional Settings").payroll_date
	payroll_cycle = {}
	for row in settings:
		if row.payroll_start_day == 'Month Start':
			row.payroll_start_day = 1
		payroll_cycle[row.project] = {
			'start_date':f'{filters.year}-{filters.month}-{row.payroll_start_day}',
			'end_date':add_days(add_months(f'{filters.year}-{filters.month}-{row.payroll_start_day}', 1), -1)
		}
	return payroll_cycle


def get_attendance(projects, employee_list):
	attendance_dict = {}
	present_dict = {}
	ot_dict = {}
	leave_dict = {}
	absent_dict = {}
	day_off_dict = {}
	
	for e in employee_list:
		attendance_dict[e]={'working_days': 0,'number_of_days_off':0, 'ot': 0, 'sl':0, 'al':0, 'ab':0, 'ol':0, 'do_ot':0}

	for key, value in projects.items():
		start_date = projects[key]['start_date']
		end_date = projects[key]['end_date']

		present_list = frappe.db.sql(f"""
			SELECT employee, COUNT(employee) as working_days FROM `tabAttendance` 
			WHERE attendance_date BETWEEN '{start_date}' AND '{end_date}' 
			AND status IN ("Present", "Work From Home") 
			AND roster_type='Basic'
			GROUP BY employee
		""", as_dict=1)

		attendance_list_ot = frappe.db.sql(f"""
			SELECT employee, COUNT(employee) as ot, count(day_off_ot) as do_ot FROM `tabAttendance` 
			WHERE attendance_date BETWEEN '{start_date}' AND '{end_date}' 
			AND status IN ("Present", "Work From Home")
			AND roster_type='Over-Time'
			GROUP BY employee
		""", as_dict=1)

		attendance_leave_details = frappe.db.sql(f"""
			SELECT employee,leave_type, COUNT(leave_type) AS leave_count FROM `tabAttendance` at
				WHERE at.status = "On Leave" 
				AND attendance_date BETWEEN '{start_date}' AND '{end_date}' 
				Group by leave_type;
			""", as_dict=1)
		
		attendance_absent = frappe.db.sql(f"""
			SELECT employee, COUNT(employee) as absent FROM `tabAttendance` at
				WHERE at.status = "Absent" 
				AND attendance_date BETWEEN '{start_date}' AND '{end_date}' 
				Group by employee;
			""", as_dict=1)
		
		day_off_list = frappe.db.sql(f"""
			SELECT employee, COUNT(employee) as number_of_days_off FROM `tabEmployee Schedule` es
				WHERE es.employee_availability = "Day Off" 
				AND date BETWEEN '{start_date}' AND '{end_date}' 
				Group by employee;
			""", as_dict=1)
	for row in present_list:
		if present_dict.get(row.employee):
			present_dict[row.employee] += row.working_days
		else:
			present_dict[row.employee] = row.working_days

	for row in attendance_list_ot:
		if ot_dict.get(row.employee):
			ot_dict[row.employee]['ot'] += row.ot
			ot_dict[row.employee]['do_ot'] += row.do_ot
		else:
			ot_dict[row.employee] = {'ot':row.ot,'do_ot':row.do_ot}
	
	for row in attendance_absent:
		if absent_dict.get(row.employee):
			absent_dict[row.employee] += row.absent
		else:
			absent_dict[row.employee] = row.absent

	for row in day_off_list:
		if day_off_dict.get(row.employee):
			day_off_dict[row.employee] += row.number_of_days_off
		else:
			day_off_dict[row.employee] = row.number_of_days_off
			
	for row in attendance_leave_details:
		if row.leave_type not in ['Sick Leave', 'Annual Leave']:
			row.leave_type = "Other Leave"
		if leave_dict.get(row.employee):
			leave_dict[row.employee]["leave_type"] = row.leave_type
			leave_dict[row.employee]["leave_count"] += row.leave_count
		else:
			leave_dict[row.employee] = {'leave_type' : row.leave_type, 'leave_count':row.leave_count}
		
	for row in attendance_dict:
		if present_dict.get(row):
			attendance_dict[row]['working_days'] += present_dict.get(row)
			
		if ot_dict.get(row):
			attendance_dict[row]['ot'] += ot_dict.get(row)["ot"]
			attendance_dict[row]['do_ot'] += ot_dict.get(row)["do_ot"]
		
		if day_off_dict.get(row):
			attendance_dict[row]['number_of_days_off'] += day_off_dict.get(row)
		
		if leave_dict.get(row):
			if leave_dict.get(row)["leave_type"] == "Sick Leave":
				attendance_dict[row]['sl'] = leave_dict.get(row)["leave_count"]
			if leave_dict.get(row)["leave_type"] == "Annual Leave":
				attendance_dict[row]['al'] = leave_dict.get(row)["leave_count"]
			if leave_dict.get(row)["leave_type"] == "Other Leave":
				attendance_dict[row]['ol'] = leave_dict.get(row)["leave_count"]
		if absent_dict.get(row):
			attendance_dict[row]['ab'] += absent_dict.get(row)

	return attendance_dict

@frappe.whitelist()
def get_attendance_years():
	year_list = frappe.db.sql_list("""select distinct YEAR(attendance_date) from tabAttendance ORDER BY YEAR(attendance_date) DESC""")
	if not year_list:
		year_list = [getdate().year]

	return "\n".join(str(year) for year in year_list)