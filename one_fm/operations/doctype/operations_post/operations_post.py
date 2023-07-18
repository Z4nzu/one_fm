# -*- coding: utf-8 -*-
# Copyright (c) 2020, omar jaber and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import datetime
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.rename_doc import rename_doc
from frappe.utils import cstr, getdate, add_to_date, date_diff, now
import pandas as pd
from one_fm.operations.doctype.contracts.contracts import get_active_contracts_for_project
from frappe.model.naming import NamingSeries
from one_fm.api.v1.utils import response
from one_fm.operations.report.roster_projection_view.roster_projection_view import execute as report_execute
from one_fm.operations.doctype.contracts.contracts import create_post_schedules

def get_last_id():
    all_docs = frappe.get_all("Post Schedule")
    striped_docnames = [int(each.name.split('PS-')[1]) for each in all_docs]
    return max(striped_docnames)

@frappe.whitelist()
def create_new_post_schedules():
    # Generate new post schedules for projects that have been generated by the roster projection view report
    frappe.enqueue(execute_post_creation, is_async=True, queue="long")
    return response("Post Creation Scheduled Sucessfully",{}, True, 200)

@frappe.whitelist()
def create_new_schedule_for_project(proj):
    # Generate new post schedules for single project that have been generated by the roster projection view report
    try:
        existing_proj = frappe.db.exists("Project",proj)
        if existing_proj:
            all_operations_post = frappe.get_all("Operations Post",{'project':existing_proj})
            all_operations_post_ = [frappe.get_doc("Operations Post",i.name) for i in all_operations_post]
            
            frappe.enqueue(create_post_schedules, operations_posts=all_operations_post_, queue="long",job_name = 'Create Post Schedules')
            return response("Post Creation Scheduled Sucessfully",{}, True, 200)
    except:
        frappe.log_error("Post Schedule Creation Error",frappe.get_traceback())



def execute_post_creation():
    """
        Get the list of projects with 0 Post Schedules  from the roster projection view report and Create the post schedules for projects with 0
    """
    filters = frappe._dict({
        'month':'06',
        'year':'2023'
    })
    result = report_execute(filters=filters)
    if result and len(result) > 1:
        project_set = set()
        for each in result[1]:
            if each.ps_qty in [0.0,0]:
                project_set.add(each.get('project'))
        update_ops_post(project_set)





def update_ops_post(projects):
    try:
        tuple_projects = tuple(projects)
        query = (f"Select name from `tabOperations Post` where project in {tuple_projects}")
        response = frappe.db.sql(query,as_dict=1)
        if response:
            posts = [i.name for i in response]
    except:
        frappe.log_error("Post Schedule Creation Error",frappe.get_traceback())

    try:
        for each in posts:
            doc = frappe.get_doc("Operations Post",each)

            create_post_schedule_for_operations_post(doc)
            frappe.db.commit()
    except:
        frappe.log_error("Error Creating Post Schedules",frappe.get_traceback())



class OperationsPost(Document):
    def after_insert(self):
        create_post_schedule_for_operations_post(self)

    def validate(self):
        if not self.post_name:
            frappe.throw("Post Name cannot be empty.")

        if not self.gender:
            frappe.throw("Gender cannot be empty.")

        if not self.site_shift:
            frappe.throw("Shift cannot be empty.")

        # if(frappe.db.get_value('Operations Role', self.post_template, 'shift') != self.site_shift):
        #     frappe.throw(f"Operations Role ({self.post_template}) does not belong to selected shift ({self.site_shift})")

        self.validate_operations_role_status()
        # check if operations site inactive
        if (self.status=='Active' and frappe.db.exists("Operations Site", {'name':self.site, 'status':'Inactive'})):frappe.throw(f"You cannot make this post active because Operations Site '{self.site}' is Inactive.")


    def validate_operations_role_status(self):
        if self.status == 'Active' and self.post_template \
            and frappe.db.get_value('Operations Role', self.post_template, 'status') != 'Active':
            frappe.throw(_("The Operations Role <br/>'<b>{0}</b>' selected in the Post '<b>{1}</b>' is <b>Inactive</b>. <br/> To make the Post atcive first make the Role active".format(self.post_template, self.name)))

    def on_update(self):
        self.validate_name()
        self.update_operation_roles()


    def validate_name(self):
        condition = self.post_name+"-"+self.gender+"|"+self.site_shift
        if condition != self.name:
            rename_doc(self.doctype, self.name, condition, force=True)

    def on_update(self):
        if self.status == "Active":
            check_list = frappe.db.get_list("Post Schedule", filters={"post":self.name, "date": [">", getdate()]})
            if len(check_list) < 1 :
                create_post_schedule_for_operations_post(self)
        elif self.status == "Inactive":
              delete_schedule(self)

def delete_schedule(doc):
    frappe.db.sql(f"""
        DELETE FROM `tabPost Schedule` WHERE post="{doc.name}" AND date>'{getdate()}'
    """)

def create_post_schedule_for_operations_post(operations_post):
    contracts = get_active_contracts_for_project(operations_post.project)
    if contracts:
        if contracts.end_date >= getdate():
            today = getdate()
            start_date = today if contracts.start_date < today else contracts.start_date
            exists_schedule_in_between = False
            if frappe.db.exists("Post Schedule", {"date": ['between', (start_date, contracts.end_date)], "post": operations_post.name}):
                exists_schedule_in_between = True
                
                frappe.enqueue(queue_create_post_schedule_for_operations_post, operations_post=operations_post, contracts=contracts, exists_schedule_in_between=exists_schedule_in_between, start_date=start_date, is_async=True, queue="long")
            else:
                queue_create_post_schedule_for_operations_post(operations_post, contracts, exists_schedule_in_between, start_date)
        else:
            frappe.msgprint(_("End date of the contract referenced in by the project is less than today."))
    else:
        frappe.msgprint(_("No active contract found for the project referenced."))

def queue_create_post_schedule_for_operations_post(operations_post, contracts, exists_schedule_in_between, start_date):
    try:
        owner = frappe.session.user
        creation = now()
        query = """
            Insert Into
                `tabPost Schedule`
                (
                    `name`, `post`, `operations_role`, `post_abbrv`, `shift`, `site`, `project`, `date`, `post_status`,
                    `owner`, `modified_by`, `creation`, `modified`, `paid`
                )
            Values
        """
        post_abbrv = frappe.db.get_value("Operations Role", operations_post.post_template, ["post_abbrv"])
        naming_series = NamingSeries('PS-')
        ps_name_idx = previous_series = naming_series.get_current_value()
        
        #The previous series value from frappe is wrong in some cases
        
        for date in	pd.date_range(start=start_date, end=contracts.end_date):
            doc_id_template = "-".join(["PS",str(datetime.datetime.now().microsecond),operations_post.name[0:5].upper(),post_abbrv.upper()])
            schedule_exists = False
            if exists_schedule_in_between:
                if  frappe.db.exists("Post Schedule", {"date": cstr(date.date()),'operations_role': operations_post.post_template, "post": operations_post.name}):
                    schedule_exists = True
            if not schedule_exists:
                ps_name_idx += 1
                ps_name = 'PS-'+str(ps_name_idx).zfill(5)
                query += f"""
                    (
                        "{doc_id_template}", "{operations_post.name}", "{operations_post.post_template}", "{post_abbrv}",
                        "{operations_post.site_shift}", "{operations_post.site}", "{operations_post.project}",
                        '{cstr(date.date())}', 'Planned', "{owner}", "{owner}", "{creation}", "{creation}", '0'
                    ),"""
        if previous_series == ps_name_idx:
            frappe.msgprint(_("Post is already scheduled."))
        else:
            frappe.db.sql(query[:-1], values=[], as_dict=1)
            frappe.db.commit()
            naming_series.update_counter(ps_name_idx)
            frappe.msgprint(_("Post is scheduled as Planned."))
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error('Post Schedule from Operations Post', e)
        frappe.msgprint(_("Error log is added."), alert=True, indicator='orange')
        operations_post.reload()
