import os
from django.http import HttpResponse
from django.conf import settings

def apple_app_site_association(request):
    file_path = os.path.join(
        settings.BASE_DIR,
        "authentication",
        "apple-app-site-association"
    )
    with open(file_path, "r", encoding="utf-8") as f:
        data = f.read()

    return HttpResponse(
        data,
        content_type="application/json"
    )