from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from .models import User

admin.site.site_header = 'Ethical Interfaces: Admin Dashboard'
# Custom index lives under a distinct name so it can {% extends "admin/index.html" %}
# without shadowing Django's stock template (DIRS would otherwise recurse).
admin.site.index_template = 'admin/index_with_content_tools.html'

admin.site.register(User, UserAdmin)
admin.site.unregister(Group)
