import sys
import os
vendor_dir = os.path.dirname(__file__)
instructor_path = os.path.join(vendor_dir, "instructor")
if instructor_path not in sys.path:
    sys.path.append(instructor_path)
