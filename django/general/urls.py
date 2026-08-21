from django.urls import path

from .views import workshop_cookies, workshop_home, workshop_privacy_notice

app_name = 'general'
urlpatterns = [
    path('', workshop_home, name='home'),
    path('cookies/', workshop_cookies, name='cookies'),
    path('privacy/', workshop_privacy_notice, name='privacy'),
]
