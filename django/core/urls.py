from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse


def healthz(request):
    """Health check endpoint (used by Render and by keep-alive pingers)."""
    return JsonResponse({'status': 'ok'})


urlpatterns = [
    # Custom apps
    path('', include('general.urls')),
    path('data/', include('researchdata.urls')),

    # Django admin
    path('dashboard/', admin.site.urls),

    # Health check
    path('healthz', healthz, name='healthz'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Debug toolbar only when installed (local development)
if settings.DEBUG and 'debug_toolbar' in settings.INSTALLED_APPS:
    import debug_toolbar
    urlpatterns.append(path('__debug__/', include(debug_toolbar.urls)))
