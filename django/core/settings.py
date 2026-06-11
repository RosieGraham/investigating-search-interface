"""
Django settings for the Investigating Search Interface.

All deployment-specific configuration comes from environment variables, so the
same code runs locally (with sensible defaults) and on Render (where the
dashboard sets the variables). There is no local_settings.py mechanism any more.

Environment variables used in production:
    SECRET_KEY            required in production (Render: generateValue)
    DEBUG                 "true"/"false" (default false)
    ALLOWED_HOSTS         comma-separated, e.g. "myapp.onrender.com"
    DATABASE_URL          postgres://... (Neon). Falls back to local SQLite.
    CLASSIFIER_ENABLED    "true"/"false" (default true)
    CLASSIFIER_THRESHOLD  cosine similarity cut-off (default 0.35)
    EMBEDDING_MODEL_DIR   where the ONNX model + topic index live
    ADMIN_EMAIL           contact address shown in templates
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / 'core' / 'templates'


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


# Core security settings

DEBUG = env_bool('DEBUG', False)

SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'insecure-dev-only-key-do-not-use-in-production'
    else:
        raise RuntimeError('SECRET_KEY environment variable is required when DEBUG is false')

ALLOWED_HOSTS = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', '').split(',') if h.strip()]
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1'] if DEBUG else ['.onrender.com']

# Render provides the public URL of the service; trust it for CSRF (admin login).
RENDER_EXTERNAL_URL = os.environ.get('RENDER_EXTERNAL_URL', '')
CSRF_TRUSTED_ORIGINS = [RENDER_EXTERNAL_URL] if RENDER_EXTERNAL_URL else []

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    # Custom apps
    'account',
    'general',
    'researchdata',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

# Optional: django-debug-toolbar in local development only
if DEBUG:
    try:
        import debug_toolbar  # noqa: F401
        INSTALLED_APPS.append('debug_toolbar')
        MIDDLEWARE.insert(1, 'debug_toolbar.middleware.DebugToolbarMiddleware')
        INTERNAL_IPS = ['127.0.0.1']
    except ImportError:
        pass

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [TEMPLATE_DIR],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
            'libraries': {
                'settings_value': 'core.templatetags.settings_value',
            }
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


# Database: DATABASE_URL (Neon Postgres in production), SQLite fallback for dev

DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "dev.sqlite3"}',
        conn_max_age=600,
        conn_health_checks=True,
    )
}


# Custom user model for authentication

AUTH_USER_MODEL = 'account.User'


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization

LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Europe/London'
USE_I18N = False
USE_TZ = True


# Static files, served by WhiteNoise in production

STATICFILES_DIRS = [BASE_DIR / 'core' / 'static']
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
if DEBUG:
    STORAGES['staticfiles'] = {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}


# Media files (user uploaded content) - note: ephemeral on Render free tier

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# Default primary key field type

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Site admin contact

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', '')


# Query classifier (vector classification) settings

CLASSIFIER_ENABLED = env_bool('CLASSIFIER_ENABLED', True)
CLASSIFIER_THRESHOLD = float(os.environ.get('CLASSIFIER_THRESHOLD', '0.35'))
CLASSIFIER_TOP_K = int(os.environ.get('CLASSIFIER_TOP_K', '3'))
EMBEDDING_MODEL_DIR = Path(os.environ.get('EMBEDDING_MODEL_DIR', BASE_DIR / 'model_cache'))
EMBEDDING_MODEL_ID = os.environ.get(
    'EMBEDDING_MODEL_ID', 'sentence-transformers/multi-qa-MiniLM-L6-cos-v1'
)
# When the classifier returns nothing above threshold, the legacy trigger
# (substring) matching is consulted instead.
TRIGGER_FALLBACK_ENABLED = env_bool('TRIGGER_FALLBACK_ENABLED', True)


# Logging: stream only (platform log drains capture stdout/stderr)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'stream': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['stream'],
            'level': 'INFO',
            'propagate': True,
        },
        'researchdata': {
            'handlers': ['stream'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
