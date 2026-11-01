from django.urls import path

from .views import chat_view, config_view

urlpatterns = [
    path("config/", config_view, name="assistant-config"),
    path("chat/", chat_view, name="assistant-chat"),
]
