import frappe,json
from six import string_types
from frappe import _
from one_fm.api.doc_methods.payroll_entry import get_basic_salary
from one_fm.api.notification import create_notification_log
from frappe.utils import cint, flt, date_diff, cstr, today, getdate,get_link_to_form, get_last_day, add_days,formatdate
from hrms.hr.utils import get_holidays_for_employee
from hrms.payroll.utils import sanitize_expression
import unicodedata

def get_scheduled_day_off(employee, start_date, end_date):
	schedule_list = frappe.db.sql_list("""
	select distinct date from `tabEmployee Schedule`
	where
		employee=%s and
		date between %s and %s
		and employee_availability in ("Day Off", "Sick Leave", "Annual Leave", "Emergency Leave") """,
	(employee, start_date, end_date))

	schedules = [cstr(schedule) for schedule in schedule_list]

	return schedules

def get_working_days_details(
	self, joining_date=None, relieving_date=None, lwp=None, for_preview=0
):
	payroll_based_on = frappe.db.get_value("Payroll Settings", None, "payroll_based_on")
	include_holidays_in_total_working_days = frappe.db.get_single_value(
		"Payroll Settings", "include_holidays_in_total_working_days"
	)

	include_day_off_in_total_working_days = frappe.db.get_single_value(
		"HR and Payroll Additional Settings", "include_day_off_in_total_working_days"
	)

	if not (joining_date and relieving_date):
			joining_date, relieving_date = self.get_joining_and_relieving_dates()
	start_date = getdate(self.start_date) if getdate(joining_date) < getdate(self.start_date) else getdate(joining_date)
	end_date = getdate(self.end_date) if getdate(relieving_date) < getdate(self.end_date) else getdate(relieving_date)

	if self.payroll_type == 'Basic':
		working_days = date_diff(end_date, start_date) + 1
	else:
		working_days = get_ot_days(self.employee, start_date, end_date)

	if for_preview: 
		self.total_working_days = working_days
		self.payment_days = working_days
		return
	holidays = get_holiday_date_for_employee(self, start_date, end_date)

	working_days_list = [
		add_days(getdate(start_date), days=day) for day in range(0, working_days)
	]

	day_off_dates = get_scheduled_day_off(self.employee, start_date, end_date)

	if not cint(include_holidays_in_total_working_days):
		working_days_list = [i for i in working_days_list if i not in holidays]

		working_days -= len(holidays)
		if working_days < 0:
			frappe.throw(_("There are more holidays than working days this month."))

	day_off_dates = get_scheduled_day_off(self.employee, self.start_date, self.end_date)

	if not payroll_based_on:
		frappe.throw(_("Please set Payroll based on in Payroll settings"))

	if payroll_based_on == "Attendance":
		actual_lwp, absent = calculate_lwp_ppl_and_absent_days_based_on_attendance(self,holidays, relieving_date)
		self.absent_days = absent
	else:
		actual_lwp = self.calculate_lwp_or_ppl_based_on_leave_application(
			holidays, working_days_list, relieving_date
		)
	
	if not lwp:
		lwp = actual_lwp
	elif lwp != actual_lwp:
		frappe.msgprint(
			_("Leave Without Pay does not match with approved {} records").format(payroll_based_on)
		)

	self.leave_without_pay = lwp
	self.total_working_days = working_days
	
	# Set Payment days by considerring include_holidays_in_total_working_days
	payment_days = get_payment_days(self,
		start_date, end_date
	)
	# Payment days by considerring include_day_off_in_total_working_days
	if not cint(include_day_off_in_total_working_days):
		payment_days -= len(day_off_dates)

	if flt(payment_days) > flt(lwp):
		self.payment_days = flt(payment_days) - flt(lwp)

		if payroll_based_on == "Attendance":
			self.payment_days -= flt(absent)

		consider_unmarked_attendance_as = (
			frappe.db.get_value("Payroll Settings", None, "consider_unmarked_attendance_as") or "Present"
		)

		if payroll_based_on == "Attendance" and consider_unmarked_attendance_as == "Absent":
			unmarked_days = self.get_unmarked_days(include_holidays_in_total_working_days)
			self.absent_days += unmarked_days  # will be treated as absent
			self.payment_days -= unmarked_days
	else:
		self.payment_days = 0

@frappe.whitelist()
def get_ot_days(employee, start_date, end_date):
	ot_schedule = frappe.get_all(
		"Employee Schedule",
		filters={
			"date": ["between", [start_date, end_date]],
			"employee": employee,
			"roster_type":"Over-Time"
		},
		fields=["COUNT(*) as working_ot_days"],
	)[0].working_ot_days
	return ot_schedule

def get_unmarked_days(self, include_holidays_in_total_working_days):
	unmarked_days = self.total_working_days
	joining_date, relieving_date = frappe.get_cached_value(
		"Employee", self.employee, ["date_of_joining", "relieving_date"]
	)
	start_date = self.start_date
	end_date = self.end_date

	if joining_date and (getdate(self.start_date) < joining_date <= getdate(self.end_date)):
		start_date = joining_date
		unmarked_days = self.get_unmarked_days_based_on_doj_or_relieving(
			unmarked_days,
			include_holidays_in_total_working_days,
			self.start_date,
			add_days(joining_date, -1),
		)

	if relieving_date and (getdate(self.start_date) <= relieving_date < getdate(self.end_date)):
		end_date = relieving_date
		unmarked_days = self.get_unmarked_days_based_on_doj_or_relieving(
			unmarked_days,
			include_holidays_in_total_working_days,
			add_days(relieving_date, 1),
			self.end_date,
		)
	if (getdate(self.posting_date) < getdate(self.end_date)):
		end_date = self.posting_date
		unmarked_days = self.get_unmarked_days_based_on_doj_or_relieving(
			unmarked_days,
			include_holidays_in_total_working_days,
			self.posting_date,
			self.end_date,
		)
	# exclude days for which attendance has been marked
	unmarked_days -= frappe.get_all(
		"Attendance",
		filters={
			"attendance_date": ["between", [start_date, end_date]],
			"employee": self.employee,
			"docstatus": 1,
			"roster_type":self.payroll_type
		},
		fields=["COUNT(*) as marked_days"],
	)[0].marked_days
	return unmarked_days

def calculate_lwp_ppl_and_absent_days_based_on_attendance(self, holidays, relieving_date):
	lwp = 0
	absent = 0

	end_date = self.end_date
	if relieving_date and getdate(self.start_date) <= relieving_date < getdate(self.end_date):
		end_date = relieving_date

	payroll_settings = frappe.get_cached_value(
		"Payroll Settings",
		None,
		[
			"daily_wages_fraction_for_half_day",
			"include_holidays_in_total_working_days",
			"consider_marked_attendance_on_holidays",
		],
		as_dict=True,
	)

	consider_marked_attendance_on_holidays = (
		payroll_settings.include_holidays_in_total_working_days
		and payroll_settings.consider_marked_attendance_on_holidays
	)

	daily_wages_fraction_for_half_day = (
		flt(payroll_settings.daily_wages_fraction_for_half_day) or 0.5
	)

	leave_types = frappe.get_all(
		"Leave Type",
		or_filters=[["is_ppl", "=", 1], ["is_lwp", "=", 1]],
		fields=["name", "is_lwp", "is_ppl", "fraction_of_daily_salary_per_leave", "include_holiday"],
	)

	leave_type_map = {}
	for leave_type in leave_types:
		leave_type_map[leave_type.name] = leave_type

	attendance = frappe.qb.DocType("Attendance")

	attendances = (
		frappe.qb.from_(attendance)
		.select(attendance.attendance_date, attendance.status, attendance.leave_type)
		.where(
			(attendance.status.isin(["Absent", "Half Day", "On Leave"]))
			& (attendance.employee == self.employee)
			& (attendance.docstatus == 1)
			& (attendance.attendance_date.between(self.start_date, end_date))
			& (attendance.roster_type == self.payroll_type)
		)
	).run(as_dict=1)

	for d in attendances:
		if (
			d.status in ("Half Day", "On Leave")
			and d.leave_type
			and d.leave_type not in leave_type_map.keys()
		):
			continue

		if (
			not consider_marked_attendance_on_holidays
			and formatdate(d.attendance_date, "yyyy-mm-dd") in holidays
		):
			if d.status == "Absent" or (
				d.leave_type
				and d.leave_type in leave_type_map.keys()
				and not leave_type_map[d.leave_type]["include_holiday"]
			):
				continue

		if d.leave_type:
			fraction_of_daily_salary_per_leave = leave_type_map[d.leave_type][
				"fraction_of_daily_salary_per_leave"
			]

		if d.status == "Half Day":
			equivalent_lwp = 1 - daily_wages_fraction_for_half_day

			if d.leave_type in leave_type_map.keys() and leave_type_map[d.leave_type]["is_ppl"]:
				equivalent_lwp *= (
					fraction_of_daily_salary_per_leave if fraction_of_daily_salary_per_leave else 1
				)
			lwp += equivalent_lwp
		elif d.status == "On Leave" and d.leave_type and d.leave_type in leave_type_map.keys():
			equivalent_lwp = 1
			if leave_type_map[d.leave_type]["is_ppl"]:
				equivalent_lwp *= (
					fraction_of_daily_salary_per_leave if fraction_of_daily_salary_per_leave else 1
				)
			lwp += equivalent_lwp
		elif d.status == "Absent":
			absent += 1
	return lwp, absent

def get_unmarked_days_based_on_doj_or_relieving(
		self, unmarked_days, include_holidays_in_total_working_days, start_date, end_date
	):
	"""
	Exclude days before DOJ or after
	Relieving Date from unmarked days
	"""
	from erpnext.setup.doctype.employee.employee import is_holiday
	include_day_off_in_total_working_days = frappe.db.get_single_value(
		"HR and Payroll Additional Settings", "include_day_off_in_total_working_days"
	)
	# if include_holidays_in_total_working_days and include_day_off_in_total_working_days:
	# 	unmarked_days = date_diff(end_date, start_date) + 1
	# el
	if include_holidays_in_total_working_days:
		unmarked_days -= date_diff(end_date, start_date) + 1
		# for days in range(date_diff(end_date, start_date) + 1):
		# 	date = add_days(end_date, -days)
		# 	# include if not day off
		# 	if not include_day_off_in_total_working_days and not is_day_off(self.employee, date):
		# 		unmarked_days -= 1
	else:
		for days in range(date_diff(end_date, start_date) + 1):
			date = add_days(end_date, -days)
			# exclude if not holidays
			if not is_holiday(self.employee, date):
				unmarked_days -= 1
			# exclude if not day off
			# if not include_day_off_in_total_working_days and not is_day_off(self.employee, date):
			# 	unmarked_days -= 1
	return unmarked_days

def is_day_off(employee, date=None):
	"""
	Returns True if given Employee has an holiday on the given date
		:param employee: Employee `name`
		:param date: Date to check. Will check for today if None
	"""

	if not date:
		date = today()

	day_off_dates = get_scheduled_day_off(employee, date, date)

	return len(day_off_dates) > 0

def calculate_lwp_ppl_and_absent_days(employee, start_date, end_date, holidays):
	lwp = 0
	absent = 0

	daily_wages_fraction_for_half_day = (
		flt(frappe.db.get_value("Payroll Settings", None, "daily_wages_fraction_for_half_day")) or 0.5
	)

	leave_types = frappe.get_all(
		"Leave Type",
		or_filters=[["is_ppl", "=", 1], ["is_lwp", "=", 1]],
		fields=["name", "is_lwp", "is_ppl", "fraction_of_daily_salary_per_leave", "include_holiday"],
	)

	leave_type_map = {}
	for leave_type in leave_types:
		leave_type_map[leave_type.name] = leave_type

	attendances = frappe.db.sql(
		"""
		SELECT attendance_date, status, leave_type
		FROM `tabAttendance`
		WHERE
			status in ("Absent", "Half Day", "On leave")
			AND employee = %s
			AND docstatus = 1
			AND attendance_date between %s and %s
	""",
		values=(employee, start_date, end_date),
		as_dict=1,
	)

	for d in attendances:
		if (
			d.status in ("Half Day", "On Leave")
			and d.leave_type
			and d.leave_type not in leave_type_map.keys()
		):
			continue

		if formatdate(d.attendance_date, "yyyy-mm-dd") in holidays:
			if d.status == "Absent" or (
				d.leave_type
				and d.leave_type in leave_type_map.keys()
				and not leave_type_map[d.leave_type]["include_holiday"]
			):
				continue

		if d.leave_type:
			fraction_of_daily_salary_per_leave = leave_type_map[d.leave_type][
				"fraction_of_daily_salary_per_leave"
			]

		if d.status == "Half Day":
			equivalent_lwp = 1 - daily_wages_fraction_for_half_day

			if d.leave_type in leave_type_map.keys() and leave_type_map[d.leave_type]["is_ppl"]:
				equivalent_lwp *= (
					fraction_of_daily_salary_per_leave if fraction_of_daily_salary_per_leave else 1
				)
			lwp += equivalent_lwp
		elif d.status == "On Leave" and d.leave_type and d.leave_type in leave_type_map.keys():
			equivalent_lwp = 1
			if leave_type_map[d.leave_type]["is_ppl"]:
				equivalent_lwp *= (
					fraction_of_daily_salary_per_leave if fraction_of_daily_salary_per_leave else 1
				)
			lwp += equivalent_lwp
		elif d.status == "Absent":
			absent += 1
	return lwp, absent

def salary_slip_before_submit(doc, method):
	basic_salary = get_basic_salary(doc.employee)

	# As per Kuwaiti law, 90% of WP salary has to be paid to an employee after factoring in deductions
	minimum_payable_salary = 0.9 * basic_salary

	if doc.net_pay < minimum_payable_salary:
		notify_payroll(doc)
		frappe.throw(_("Total deduction amount exceeds the permissible limit in Salary Slip for {}".format(doc.employee+":"+doc.employee_name)))


def notify_payroll(doc):
	email = frappe.get_value("HR Settings", "HR Settings", "payroll_notifications_email")
	subject = _("Urgent Attention Needed: Issues with maximum deduction in Payroll")
	message = _("Total deduction amount exceeds the permissible limit in Salary Slip for {}".format(doc.employee+":"+doc.employee_name))
	create_notification_log(subject,message,[email], doc)

def set_earnings_and_deduction_with_respect_to_payroll_cycle(doc, method):
	last_salary_slip = get_last_salary_slip(doc.employee)
	if last_salary_slip and len(last_salary_slip) > 0:
		payroll_based_on = frappe.db.get_value("Payroll Settings", None, "payroll_based_on")
		last_salary_slip = last_salary_slip[0]
		if getdate(doc.posting_date) < getdate(doc.end_date):
			# if paid for last payroll end date to yesterday
			if getdate(last_salary_slip.start_date) <= getdate(last_salary_slip.posting_date) and \
				getdate(last_salary_slip.posting_date) <= getdate(last_salary_slip.end_date):
				# deduct salary for last payroll absent/lwp days => find absents and actual_lwp in last payroll end date to yesterday
				deduct_days = 0
				holidays = get_holiday_date_for_employee(doc, last_salary_slip.posting_date, last_salary_slip.end_date)
				actual_lwp, absent = calculate_lwp_ppl_and_absent_days(doc.employee, last_salary_slip.posting_date, last_salary_slip.end_date, holidays)
				if payroll_based_on == "Attendance":
					deduct_days += absent
					deduct_days += actual_lwp
				deduct_from_salary(doc, last_salary_slip, deduct_days)
			else:
				# set earnings for last payroll payment days => find payment days in last payroll end date to yesterday
				payment_days = get_payment_days(doc, last_salary_slip.end_date, get_last_day(last_salary_slip.end_date))
				last_salary_slip_daily_wage = last_salary_slip.net_pay / last_salary_slip.total_working_days
				deduction = doc.append("earnings")
				deduction.salary_component = "Not Included in Last Salary"
				deduction.amount = last_salary_slip_daily_wage * payment_days
		# if paid for last payroll end date to yesterday
		if getdate(doc.posting_date) > getdate(doc.end_date) and \
			is_last_salary_slip_overlap_with_current_salary_slip(last_salary_slip, doc.start_date, doc.end_date):
			deduct_from_salary(doc, last_salary_slip)
			# find payment days in last payroll end date to yesterday
			payment_days = get_payment_days(doc, last_salary_slip.end_date, get_last_day(last_salary_slip.end_date))
			# deduct salary for paid payment_days
			deduct_from_salary(doc, last_salary_slip, payment_days)

def deduct_from_salary(doc, last_salary_slip, deduct_days):
	last_salary_slip_daily_wage = last_salary_slip.net_pay / last_salary_slip.total_working_days
	deduction = doc.append("deductions")
	deduction.salary_component = "Last Salary Deduct"
	deduction.amount = last_salary_slip_daily_wage * deduct_days

def get_payment_days(doc, start_date, end_date):
	payment_days = doc.total_working_days
	include_holidays_in_total_working_days = frappe.db.get_single_value(
		"Payroll Settings", "include_holidays_in_total_working_days"
	)

	include_day_off_in_total_working_days = frappe.db.get_single_value(
		"HR and Payroll Additional Settings", "include_day_off_in_total_working_days"
	)

	holidays = get_holiday_date_for_employee(doc, start_date, end_date)

	if not cint(include_holidays_in_total_working_days):
		payment_days -= len(holidays)

	if not cint(include_day_off_in_total_working_days):
		day_off_dates = get_scheduled_day_off(doc.employee, start_date, end_date)
		payment_days -= len(day_off_dates)

	return payment_days

def is_last_salary_slip_overlap_with_current_salary_slip(last_salary_slip, start_date, end_date):
	if getdate(start_date) <= getdate(last_salary_slip.end_date) and getdate(last_salary_slip.end_date) <= getdate(end_date):
		return True # Last salary overlap current salary
	return False # Last salary not overlap current salary

def get_last_salary_slip(employee):
	last_salary_slip = frappe.get_list(
		"Salary Slip",
		fields=["*"],
		filters={"employee":employee, "docstatus": 1},
		order_by='start_date',
		limit_page_length=1
	)
	if last_salary_slip and len(last_salary_slip) > 0:
		return last_salary_slip
	return False

def get_holiday_date_for_employee(doc, start_date, end_date ):
	holidays = get_holidays_for_employee(doc.employee, start_date, end_date, only_non_weekly=True)

	return [cstr(h.holiday_date) for h in holidays]

@frappe.whitelist()
def get_data_for_eval(doc):
	"""Returns data for evaluating formula"""
	data = frappe._dict()
	employee = frappe.get_doc("Employee", doc.employee).as_dict()

	start_date = getdate(doc.start_date)
	date_to_validate = (
		employee.date_of_joining if employee.date_of_joining > start_date else start_date
	)

	salary_structure_assignment = frappe.get_value(
		"Salary Structure Assignment",
		{
			"employee": doc.employee,
			"salary_structure": doc.salary_structure,
			"docstatus": 1,
		},
		"*",
		order_by="from_date desc",
		as_dict=True,
	)

	if not salary_structure_assignment:
		frappe.throw(
			_(
				"Please assign a Salary Structure for Employee {0} " "applicable from or before {1} first"
			).format(
				frappe.bold(doc.employee_name),
				frappe.bold(formatdate(date_to_validate)),
			)
		)

	data.update(salary_structure_assignment)
	data.update(employee)
	data.update(doc.as_dict())

	# set values for components
	salary_components = frappe.get_all("Salary Component", fields=["salary_component_abbr"])
	for sc in salary_components:
		data.setdefault(sc.salary_component_abbr, 0)

	# shallow copy of data to store default amounts (without payment days) for tax calculation
	default_data = data.copy()

	for key in ("earnings", "deductions"):
		for d in doc.get(key):
			default_data[d.abbr] = d.default_amount or 0
			data[d.abbr] = d.amount or 0

	return data, default_data



@frappe.whitelist()
def validate_multi_structure_slip(doc,return_doc = 0):
	"""Validate if the payroll period in the salary slip or payroll entry has multiple salary structures assigned, 
 		It will return either a boolean or a frappe object based on the arguments received """
	if isinstance(doc,string_types):
		return_doc = True
		doc = frappe.get_doc(json.loads(doc))
	
	all_structures = frappe.get_all("Salary Structure Assignment",{'from_date':['between',[doc.start_date,doc.end_date]],'docstatus':1,'employee':doc.employee},['*'])
	all_structures += frappe.get_all("Salary Structure Assignment",{'from_date':['<',doc.start_date],'docstatus':1,'employee':doc.employee},['*'],order_by="from_date desc",limit_page_length = 1)
	if len(all_structures)>1:
		doc.has_multiple_salary_structure = 1
		doc = generate_split_payroll(doc,all_structures)
		return doc
			# return{'structures':len(all_structures),'has_multiple':0 if len(all_structures)<=1 else 1}


def generate_split_payroll(doc,structure_dict):
    doc.custom_salary_component_detail = []
    "Handle all the requirements to change a salary slip from single to multiple salary structures"
    #Set Salary Structure
    #Set Components in Structure
    #Set Dates for Structure
    #Set Amount for Components per date
    #Transfer to real table	
    strucs = tuple([i.salary_structure for i in structure_dict])
    components = frappe.db.sql(f"""Select sd.*,sc.type from `tabSalary Detail` sd JOIN `tabSalary Component` sc
                               ON sd.salary_component = sc.name WHERE sd.parent IN {strucs}  """,as_dict = 1)
    if components:
        active_dates = get_salary_detail(structure_dict,doc,components)
        for each in active_dates:
            doc.append('custom_salary_component_detail',each)
    return doc
def get_date_before_start_date(date_field, start_date):
    if date_field > start_date:
        return date_field
    else:
        return start_date


def get_salary_detail(structure_dict,doc,components):
	"""Get the Start and End dates of a salary structure in a multi structure salary slip

	Args:
		structure_dict (_type_): list of salary structure assignments
		doc (_type_): salary slip doc
		components: list of salary components
	"""
	try:
		valid_dates = [getdate(i.from_date) for i in structure_dict]
		valid_dates.sort()
		last_item = len(valid_dates) - 1
		detail_dict = []
		for each in structure_dict:
			each.payroll_start_date = getdate(doc.start_date) if getdate(each.from_date) < getdate(doc.start_date) else getdate(each.from_date)
			if valid_dates.index(getdate(each.from_date)) !=last_item:
				payroll_end_date_index = valid_dates.index(getdate(each.from_date)) + 1
				each.payroll_end_date = add_days(valid_dates[payroll_end_date_index],-1)
			else:
				each.payroll_end_date = getdate(doc.end_date)
			for one in components:
				if one.parent  == each.salary_structure:
					detail_dict.append({
					'salary_structure':one.parent,
					'component_type':one.type,
					'salary_component':one.salary_component,
					'start_date':each.payroll_start_date,
					'end_date':each.payroll_end_date,
					'period':f'{each.payroll_start_date.strftime("%Y-%m-%d")} to {each.payroll_end_date.strftime("%Y-%m-%d")}',
					})
		return detail_dict
	except:
		frappe.log_error("Error Generating Salary",frappe.get_traceback())
		frappe.throw("An error ocurred while generating salary, Kindly review error Logs")
		


   
	
	
	