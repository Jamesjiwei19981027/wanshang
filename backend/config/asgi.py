import os

from django.core.asgi import get_asgi_application

from catalog.upload_limits import RequestBodyLimitASGI

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = RequestBodyLimitASGI(get_asgi_application())

