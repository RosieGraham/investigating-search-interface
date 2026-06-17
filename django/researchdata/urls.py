from django.urls import path
from . import views
from . import apps

app_name = apps.app_name

urlpatterns = [
    path('prompt/get/', views.prompt_get, name='prompt-get'),
    path('classifier/debug/', views.classifier_debug, name='classifier-debug'),
    path('response/post/', views.response_post, name='response-post'),
    path('notrelevantreport/post/', views.notrelevantreport_post, name='notrelevantreport-post'),
    path('event/post/', views.event_post, name='event-post'),
]
