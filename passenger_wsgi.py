"""
Phusion Passenger entry point for hosting this Django project on Webuzo
(Setup Python App). Passenger looks for this exact filename —
`passenger_wsgi.py` — in the application root and imports `application`
from it.

Webuzo provisions a dedicated virtualenv for the app and runs this file
with that virtualenv's Python interpreter (the one picked in the panel),
so its site-packages are already on sys.path — no manual venv activation
needed here.
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
