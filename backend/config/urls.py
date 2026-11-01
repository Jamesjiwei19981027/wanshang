from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from catalog.views import inquiry_attachment_download_view


def api_v1_not_found(request, unmatched_path=""):
    return JsonResponse(
        {
            "success": False,
            "error_code": "NOT_FOUND",
            "message": "The requested API endpoint was not found.",
            "fields": {},
        },
        status=404,
    )

urlpatterns = [
    path("favicon.ico", RedirectView.as_view(url=f"{settings.STATIC_URL}catalog/admin/favicon.svg", permanent=False)),
    path("admin/inquiry-attachments/<int:pk>/download/", inquiry_attachment_download_view, name="inquiry-attachment-download"),
    path("admin/", admin.site.urls),
    path("api/", include("catalog.urls")),
    # Namespaced so reverse() of an unnamespaced route always yields /api/…
    path("api/v1/", include(("catalog.urls", "catalog"), namespace="v1")),
    path("api/v1/assistant/", include(("assistant.urls", "assistant"), namespace="v1-assistant")),
    path("api/assistant/", include("assistant.urls")),
    re_path(r"^api/v1(?:/(?P<unmatched_path>.*))?$", api_v1_not_found, name="api-v1-not-found"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
