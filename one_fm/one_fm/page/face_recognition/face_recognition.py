import base64
import grpc
from one_fm.proto import facial_recognition_pb2, facial_recognition_pb2_grpc, enroll_pb2, enroll_pb2_grpc
import frappe
from frappe import _
from frappe.utils import now_datetime, cstr, nowdate, cint , getdate
import numpy as np
import datetime
from json import JSONEncoder
# import cv2, os
# import face_recognition
import json
# from imutils import face_utils, paths
from one_fm.api.doc_events import haversine
from one_fm.api.v1.roster import get_current_shift
from one_fm.one_fm.page.face_recognition.utils import check_existing, update_onboarding_employee
from one_fm.api.v2.face_recognition import verify_checkin_checkout, enroll


# setup channel for face recognition
face_recognition_service_url = frappe.local.conf.face_recognition_service_url
channels = [
    grpc.secure_channel(i, grpc.ssl_channel_credentials()) for i in face_recognition_service_url
]

# setup stub for face recognition
stubs = [
    facial_recognition_pb2_grpc.FaceRecognitionServiceStub(i) for i in channels
]


class NumpyArrayEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return JSONEncoder.default(self, obj)

def setup_directories():
	"""
		Use this function to create directories needed for the face recognition system: dataset directory and facial embeddings
	"""
	from pathlib import Path
	Path(frappe.utils.cstr(frappe.local.site)+"/private/files/user/").mkdir(parents=True, exist_ok=True)
	Path(frappe.utils.cstr(frappe.local.site)+"/private/files/dataset/").mkdir(parents=True, exist_ok=True)
	Path(frappe.utils.cstr(frappe.local.site)+"/private/files/facial_recognition/").mkdir(parents=True, exist_ok=True)
	Path(frappe.utils.cstr(frappe.local.site)+"/private/files/face_rec_temp/").mkdir(parents=True, exist_ok=True)
	Path(frappe.utils.cstr(frappe.local.site)+"/private/files/dataset/"+frappe.session.user+"/").mkdir(parents=True, exist_ok=True)

@frappe.whitelist()
def enrollment():
	try:
		files = frappe.request.files
		file = files['file']
		employee_id = frappe.db.get_value("Employee", {'user_id': frappe.session.user}, ["employee_id"])

		# Get user video
		content_bytes = file.stream.read()
		content_base64_bytes = base64.b64encode(content_bytes)
		video_content = content_base64_bytes.decode('ascii')

		doc = frappe.get_doc("Employee", {"user_id": frappe.session.user})
		# Setup channel
		# face_recognition_enroll_service_url = frappe.local.conf.face_recognition_enroll_service_url
		# channel = grpc.secure_channel(face_recognition_enroll_service_url, grpc.ssl_channel_credentials())
		# setup stub

		# stub = enroll_pb2_grpc.FaceRecognitionEnrollmentServiceStub(channel)
		# request body
		req = enroll_pb2.EnrollRequest(
			username = frappe.session.user,
			user_encoded_video = video_content,
		)

		res = stub.FaceRecognitionEnroll(req)

		data = {'employee':doc.name, 'log_type':'Enrollment', 'verification':res.enrollment,
				'message':res.message, 'data':res.data, 'source': 'Enroll'}
		#frappe.enqueue('one_fm.operations.doctype.face_recognition_log.face_recognition_log.create_face_recognition_log',**{'data':data})
		
		if res.enrollment == "FAILED":
			return response(res.message, 400, None, res.data)

		doc.enrolled = 1
		doc.save(ignore_permissions=True)
		update_onboarding_employee(doc)
		frappe.db.commit()

		return _("Successfully Enrolled!")

	except Exception as exc:
		print(frappe.get_traceback())
		frappe.log_error(frappe.get_traceback())
		raise exc


@frappe.whitelist()
def verify():
	try:
		log_type = frappe.local.form_dict['log_type']
		skip_attendance = frappe.local.form_dict['skip_attendance']
		latitude = frappe.local.form_dict['latitude']
		longitude = frappe.local.form_dict['longitude']
		employee = frappe.db.get_value("Employee", {'user_id': frappe.session.user}, ["name"])
		shift_assignment = get_current_shift(employee)
		# timestamp = frappe.local.form_dict['timestamp']
		files = frappe.request.files
		file = files['file']
		# Get user video
		content_bytes = file.stream.read()
		content_base64_bytes = base64.b64encode(content_bytes)
		video_content = content_base64_bytes.decode('ascii')

		employee_id = frappe.db.get_value("Employee", {'user_id': frappe.session.user}, ["employee_id"])

		if not employee:
            return response("Resource Not Found", 404, None, "No employee found with {employee_id}".format(employee_id=employee_id))

        right_now = now_datetime() 

        if log_type == "IN":
            shift_type = frappe.db.sql(f""" select shift_type from `tabShift Assignment` where employee = '{employee}' order by creation desc limit 1 """, as_dict=1)[0]
            val_in_shift_type = frappe.db.sql(f""" select begin_check_in_before_shift_start_time, start_time, late_entry_grace_period, working_hours_threshold_for_absent from `tabShift Type` where name = '{shift_type["shift_type"]}' """, as_dict=1)[0]
            time_threshold = datetime.strptime(str(val_in_shift_type["start_time"] - timedelta(minutes=val_in_shift_type["begin_check_in_before_shift_start_time"])), "%H:%M:%S").time()

            if right_now.time() < time_threshold:
                return response("Bad Request", 400, None, f" Oops! You can't check in right now. Your check-in time is {val_in_shift_type['begin_check_in_before_shift_start_time']} minutes before you start your shift." + "\U0001F612")

            existing_perm = frappe.db.sql(f""" select name from `tabShift Permission` where date = '{right_now.date()}' and employee = '{employee}' and permission_type = '{log_type}' and workflow_state = 'Approved' """, as_dict=1)
            if not existing_perm and (right_now.time() >  datetime.strptime(str(val_in_shift_type["start_time"] + + timedelta(hours=int(val_in_shift_type['working_hours_threshold_for_absent']))), "%H:%M:%S").time()):
                return response("Bad Request", 400, None, f"Oops! You are late beyond the  {int(val_in_shift_type['working_hours_threshold_for_absent'])} - hour time mark and you are marked absent !" + "\U0001F612")       
 
        req = facial_recognition_pb2.FaceRecognitionRequest(
            username = frappe.session.user,
            media_type = "video",
            media_content = video
        )
        # Call service stub and get response
        
        res = random.choice(stubs).FaceRecognition(req)

        if res.verification == "FAILED" and 'Invalid media content' in res.data:
            frappe.enqueue('one_fm.operations.doctype.face_recognition_log.face_recognition_log.create_face_recognition_log',
            **{'data':{'employee':employee, 'log_type':log_type, 'verification':res.verification,
                'message':res.message, 'data':res.data, 'source': 'Checkin'}})
            doc = create_checkin_log(employee, log_type, skip_attendance, latitude, longitude, shift_assignment)
            if log_type == "IN":
                check = late_checkin_checker(doc, val_in_shift_type, existing_perm )
                if check:
                    doc.update({"message": "You Checked in, but you were late, try to checkin early next time !" +  "\U0001F612"})
                    return _("Success", 201, doc, None)
            return _("Success")
        elif res.verification == "FAILED":
            msg = res.message
            data = res.data
            frappe.enqueue('one_fm.operations.doctype.face_recognition_log.face_recognition_log.create_face_recognition_log',
            **{'data':{'employee':employee, 'log_type':log_type, 'verification':res.verification,
                'message':res.message, 'data':res.data, 'source': 'Checkin'}})
            return _(msg)
        elif res.verification == "OK":
            doc = create_checkin_log(employee, log_type, skip_attendance, latitude, longitude, shift_assignment)
            if log_type == "IN":
                check = late_checkin_checker(doc, val_in_shift_type, existing_perm )
                if check:
                    doc.update({"message": "You Checked in, but you were late, try to checkin early next time !" +  "\U0001F612"})
                    return _("Success")
            quote = fetch_quote(direct_response=True)
            return _("Success")

        else:
            return _("Success")
	except Exception as exc:
		frappe.log_error(frappe.get_traceback())
		raise exc

@frappe.whitelist()
def user_within_site_geofence(employee, log_type, user_latitude, user_longitude):
	""" This method checks if user's given coordinates fall within the geofence radius of the user's assigned site in Shift Assigment. """
	shift = get_current_shift(employee)
	date = cstr(getdate())
	if shift:
		if frappe.db.exists("Shift Request", {"employee":employee, 'from_date':['<=',date],'to_date':['>=',date]}):
			check_in_site, check_out_site = frappe.get_value("Shift Request", {"employee":employee, 'from_date':['<=',date],'to_date':['>=',date]},["check_in_site","check_out_site"])
			if log_type == "IN":
				location = frappe.get_list("Location", {'name':check_in_site}, ["latitude","longitude", "geofence_radius"])
			else:
				location = frappe.get_list("Location", {'name':check_out_site}, ["latitude","longitude", "geofence_radius"])			
		
		else:
			if shift.site_location:
				location = frappe.get_list("Location", {'name':shift.site_location}, ["latitude","longitude", "geofence_radius"])
			elif shift.shift:
				site = frappe.get_value("Operations Shift", shift.shift, "site")
				location= frappe.db.sql("""
					SELECT loc.latitude, loc.longitude, loc.geofence_radius
					FROM `tabLocation` as loc
					WHERE
					loc.name IN (SELECT site_location FROM `tabOperations Site` where name="{site}")
					""".format(site=site), as_dict=1)

		if location:
			location_details = location[0]
			distance = float(haversine(location_details.latitude, location_details.longitude, user_latitude, user_longitude))
			if distance <= float(location_details.geofence_radius):
				return True
	return False

def check_in(log_type, skip_attendance, latitude, longitude):
	employee = frappe.get_value("Employee", {"user_id": frappe.session.user})
	checkin = frappe.new_doc("Employee Checkin")
	checkin.employee = employee
	checkin.log_type = log_type
	checkin.device_id = cstr(latitude)+","+cstr(longitude)
	checkin.skip_auto_attendance = cint(skip_attendance)
	# checkin.time = now_datetime()
	# checkin.actual_time = now_datetime()
	checkin.save()
	frappe.db.commit()
	return _('Check {log_type} successful! {docname}'.format(log_type=log_type.lower(), docname=checkin.name))

@frappe.whitelist()
def forced_checkin(employee, log_type, time):
	checkin = frappe.new_doc("Employee Checkin")
	checkin.employee = employee
	checkin.log_type = log_type
	checkin.device_id = cstr('0')+","+cstr('0')
	checkin.skip_auto_attendance = cint('0')
	checkin.time = time
	checkin.actual_time = time
	checkin.save()
	frappe.db.commit()
	return _('Check {log_type} successful! {docname}'.format(log_type=log_type.lower(), docname=checkin.name))


# def create_dataset(video):
# 	OUTPUT_DIRECTORY = frappe.utils.cstr(frappe.local.site)+"/private/files/dataset/"+frappe.session.user+"/"
# 	count = 0 
	
# 	cap = cv2.VideoCapture(video)
# 	success, img = cap.read()
# 	count = 0
# 	while success:
# 		#Resizing the image
# 		img = cv2.resize(img, (0, 0), fx=0.5, fy=0.5)
# 		#Limiting the number of images for training. %5 gives 10 images %5.8 -> 8 images %6.7 ->7 images
# 		if count%5 == 0 :
# 			cv2.imwrite(OUTPUT_DIRECTORY + "{0}.jpg".format(count+1), img)
# 		count = count + 1
# 		success, img = cap.read()

# 	create_encodings(OUTPUT_DIRECTORY)
# 	doc = frappe.get_doc("Employee", {"user_id": frappe.session.user})
# 	print(doc.as_dict())
# 	doc.enrolled = 1
# 	doc.save(ignore_permissions=True)
# 	frappe.db.commit()


# def create_encodings(directory, detection_method="hog"):# detection_method can be "hog" or "cnn". cnn is more cpu and memory intensive.
# 	"""
# 		directory : directory path containing dataset 
# 	"""
# 	print(directory)
# 	OUTPUT_ENCODING_PATH_PREFIX = frappe.utils.cstr(frappe.local.site)+"/private/files/facial_recognition/"
# 	user_id = frappe.session.user
# 	# grab the paths to the input images in our dataset
# 	imagePaths = list(paths.list_images(directory))
# 	print(imagePaths)
# 	#encodings file output path
# 	encoding_path = OUTPUT_ENCODING_PATH_PREFIX + user_id +".json"
# 	# initialize the list of known encodings and known names
# 	knownEncodings = []
# 	# knownNames = []

# 	for (i, imagePath) in enumerate(imagePaths):
# 		# extract the person name from the image path i.e User Id
# 		print("[INFO] processing image {}/{}".format(i + 1, len(imagePaths)))
# 		name = imagePath.split(os.path.sep)[-2]

# 		# load the input image and convert it from BGR (OpenCV ordering)
# 		# to dlib ordering (RGB)
# 		image = cv2.imread(imagePath)
# 		#BGR to RGB conversion
# 		rgb =  image[:, :, ::-1]

# 		# detect the (x, y)-coordinates of the bounding boxes
# 		# corresponding to each face in the input image
# 		boxes = face_recognition.face_locations(rgb, model=detection_method)

# 		# compute the facial embedding for the face
# 		encodings = face_recognition.face_encodings(rgb, boxes)

# 		# loop over the encodings
# 		for encoding in encodings:
# 			# add each encoding + name to our set of known names and
# 			# encodings
# 			knownEncodings.append(encoding)

# 	# dump the facial encodings + names to disk	
# 	data = {"encodings": knownEncodings}
# 	print(data)
# 	if len(knownEncodings) == 0:
# 		frappe.throw(_("No face found in the video. Please make sure you position your face correctly in front of the camera."))
# 	data = json.dumps(data, cls=NumpyArrayEncoder)
# 	with open(encoding_path,"w") as f:
# 		f.write(data)
# 		f.close()


@frappe.whitelist()
def check_existing():
	"""API to determine the applicable Log type.
	The api checks employee's last lcheckin log type. and determine what next log type needs to be
	Returns:
		True: The log in was "IN", so his next Log Type should be "OUT".
		False: either no log type or last log type is "OUT", so his next Ltg Type should be "IN".
	"""
	employee = frappe.get_value("Employee", {"user_id": frappe.session.user})

	# define logs
	logs = []
	
	# get current and previous day date.
	today = nowdate()
	prev_date = ((datetime.datetime.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")).split(" ")[0]

	#get Employee Schedule
	last_shift = frappe.get_list("Shift Assignment",fields=["*"],filters={"employee":employee},order_by='creation desc',limit_page_length=1)

	if not employee:
		frappe.throw(_("Please link an employee to the logged in user to proceed further."))

	shift = get_current_shift(employee)
	#if employee schedule is linked with the previous Checkin doc

	if shift and last_shift:
		start_date = (shift.start_date).strftime("%Y-%m-%d")
		if start_date == today or start_date == prev_date:
			logs = frappe.db.sql("""
				select log_type from `tabEmployee Checkin` where skip_auto_attendance=0 and employee="{employee}" and shift_assignment="{shift_assignment}"
				""".format(employee=employee, shift_assignment=last_shift[0].name), as_dict=1)
	else:
		#get checkin log of today.
		logs = frappe.db.sql("""
			select log_type from `tabEmployee Checkin` where date(time)=date("{date}") and skip_auto_attendance=0 and employee="{employee}"
			""".format(date=today, employee=employee), as_dict=1)
	val = [log.log_type for log in logs]

	#For Check IN
	if not val or (val and val[-1] == "OUT"):
		return False
	#For Check OUT
	else:
		return True

# def recognize_face(image):
# 	try:
# 		ENCODINGS_PATH = frappe.utils.cstr(
# 			frappe.local.site)+"/private/files/facial_recognition/"+frappe.session.user+".json"
# 		# values should be "hog" or "cnn" . cnn is CPU and memory intensive.
# 		DETECTION_METHOD = "hog"

# 		# load the known faces and embeddings
# 		face_data = json.loads(open(ENCODINGS_PATH, "rb").read())

# 		# load the input image and convert it from BGR to RGB
# 		image = cv2.imread(image)
# 		rgb =  image[:, :, ::-1]

# 		# detect the (x, y)-coordinates of the bounding boxes corresponding
# 		# to each face in the input image, then compute the facial embeddings
# 		# for each face
# 		boxes = face_recognition.face_locations(rgb,
# 												model=DETECTION_METHOD)
# 		encodings = face_recognition.face_encodings(rgb, boxes)

# 		if not encodings:
# 			return False
# 		return match_encodings(encodings, face_data)

# 	except Exception as e:
# 		print(frappe.get_traceback())


# def match_encodings(encodings, face_data):
# 	try:
# 		# loop over the facial embeddings
# 		for encoding in encodings:
# 			# attempt to match each face in the input image to our known
# 			# encodings
# 			matches = face_recognition.compare_faces(
# 				face_data["encodings"], encoding)
# 			# check to see if we have found a match
# 			if True in matches:
# 				# find the indexes of all matched faces
# 				matchedIdxs = [i for (i, b) in enumerate(matches) if b]
# 				print(matchedIdxs, matches)
# 				return True if ((len(matchedIdxs) / len(matches)) * 100 > 80) else False
# 			else:
# 				return False
# 		else:
# 			return False
# 	except Exception as identifier:
# 		print(frappe.get_traceback())
