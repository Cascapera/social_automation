from django.urls import path

from . import views

urlpatterns = [
    path("connect/", views.youtube_connect),
    path("callback/", views.youtube_callback),
    path("pending-channels/", views.youtube_pending_channels),
    path("select-channel/", views.youtube_select_channel),
]
