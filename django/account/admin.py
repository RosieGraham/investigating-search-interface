from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from .models import User

admin.site.site_header = 'Investigating Search Interface: Admin Dashboard'
# Custom index lives under a distinct name so it can {% extends "admin/index.html" %}
# without shadowing Django's stock template (DIRS would otherwise recurse).
admin.site.index_template = 'admin/index_with_content_tools.html'


class SuperuserOnlyUserAdmin(UserAdmin):
    """User management is superuser-only (July 2026 role model).

    Staff editors must not be able to see other accounts, grant themselves
    flags, or reset passwords. Django's stock UserAdmin already guards flag
    escalation for non-superusers, but hiding the module entirely is the
    boundary the ethics application describes.
    """

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


admin.site.register(User, SuperuserOnlyUserAdmin)
admin.site.unregister(Group)
