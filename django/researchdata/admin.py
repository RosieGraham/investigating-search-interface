from django.contrib import admin
from . import models

# Role model (July 2026): two tiers, using Django's existing flags.
#
# - Superuser (Rosie): everything, unchanged.
# - Staff editor (Emilia, collaborators, a SEASON demo login): can sign in to
#   the dashboard and VIEW content (topic groups, topics, triggers, prompts)
#   plus the ContentApply and MatchingEvaluation histories, but cannot add,
#   change or delete anything, and cannot see participant data (Response,
#   EngagementEvent, NotRelevantReport), the Setting table, DataInsert
#   (whose save() creates triggers), or user management.
#
# These has_*_permission overrides are the enforcement, not the hiding: a
# direct URL to a blocked changelist returns 403 regardless of what the
# admin index shows. The ethics application's data-access section relies on
# this boundary, so treat any loosening as an ethics change, not a UI tweak.


class StaffReadSuperuserWriteMixin:
    """Content tables: staff see them read-only; only superusers write."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_staff

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


class SuperuserOnlyMixin:
    """Participant data and system tables: invisible to non-superusers."""

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


def approve(modeladmin, request, queryset):
    """
    Sets all selected items in queryset to approved
    """
    queryset.update(admin_approved=True)


approve.short_description = "Approve selected objects (will be publicly visible)"


def unapprove(modeladmin, request, queryset):
    """
    Sets all selected items in queryset to not approved
    """
    queryset.update(admin_approved=False)


unapprove.short_description = "Unapprove selected objects (will not be publicly visible)"


@admin.register(models.TopicGroup)
class TopicGroupAdminView(StaffReadSuperuserWriteMixin, admin.ModelAdmin):
    list_display = ('id', 'name', 'meta_created_datetime', 'meta_lastupdated_datetime')
    search_fields = ('name',)


@admin.register(models.Topic)
class TopicAdminView(StaffReadSuperuserWriteMixin, admin.ModelAdmin):
    list_display = ('id', 'name', 'topic_group', 'has_description', 'meta_lastupdated_datetime')
    list_filter = ('topic_group',)
    search_fields = ('name', 'description')
    autocomplete_fields = ('topic_group',)

    @admin.display(boolean=True, description='Description written?')
    def has_description(self, obj):
        return bool(obj.description and obj.description.strip())


@admin.register(models.Trigger)
class TriggerAdminView(StaffReadSuperuserWriteMixin, admin.ModelAdmin):
    """
    Customise the content of the list of Triggers in the Django admin
    """
    list_display = ('id', 'trigger_text', 'meta_created_datetime', 'meta_lastupdated_datetime')
    search_fields = ('trigger_text',)
    list_per_page = 100


@admin.register(models.Prompt)
class PromptAdminView(StaffReadSuperuserWriteMixin, admin.ModelAdmin):
    """
    Customise the content of the list of Prompts in the Django admin
    """
    list_display = ('id',
                    'topic',
                    'prompt_content',
                    'has_seeed_url',
                    'response_required',
                    'priority',
                    'admin_approved',
                    'meta_lastupdated_datetime')
    list_filter = ('admin_approved', 'topic__topic_group')
    search_fields = ('prompt_content', 'topic__name')
    autocomplete_fields = ('topic',)
    filter_horizontal = ('triggers',)
    actions = (approve, unapprove)

    def get_actions(self, request):
        """Approve/unapprove write to admin_approved; superuser only.

        Custom admin actions do not check model permissions themselves, so
        strip them here rather than trusting the changelist UI alone.
        """
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop('approve', None)
            actions.pop('unapprove', None)
        return actions

    @admin.display(boolean=True, description='SEEED link?')
    def has_seeed_url(self, obj):
        return bool(obj.seeed_url)


@admin.register(models.Response)
class ResponseAdminView(SuperuserOnlyMixin, admin.ModelAdmin):
    """
    Participant responses (research data). Superuser only: the ethics
    application's data-access section depends on this table being closed
    to ordinary staff logins.
    """
    list_display = ('id',
                    'response_content',
                    'prompt',
                    'admin_approved',
                    'meta_created_datetime')
    list_filter = ('admin_approved',)
    search_fields = ('response_content',)
    autocomplete_fields = ('prompt',)
    actions = (approve, unapprove)


@admin.register(models.NotRelevantReport)
class NotRelevantReportAdminView(SuperuserOnlyMixin, admin.ModelAdmin):
    """
    Labelled query/topic mismatches - the calibration dataset for the
    classifier threshold. Participant-submitted; superuser only.
    """
    list_display = ('id', 'prompt', 'user_search_query', 'classifier_confidence', 'meta_created_datetime')
    search_fields = ('user_search_query',)
    autocomplete_fields = ('prompt',)


@admin.register(models.EngagementEvent)
class EngagementEventAdminView(SuperuserOnlyMixin, admin.ModelAdmin):
    """
    Anonymous engagement events (research instrument). Read-only, and
    superuser only: participant data.
    """
    list_display = ('id', 'event_type', 'prompt', 'topic', 'serp_mode', 'classifier_confidence', 'meta_created_datetime')
    list_filter = ('event_type', 'serp_mode')
    date_hierarchy = 'meta_created_datetime'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(models.ContentApply)
class ContentApplyAdminView(StaffReadSuperuserWriteMixin, admin.ModelAdmin):
    """
    Audit trail of package applies. Staff may read it (it is how an editor
    checks what is live); nobody edits it; rows are created by the apply flow.
    """
    list_display = ('id', 'filename', 'dry_run', 'actor', 'created_datetime')
    list_filter = ('dry_run',)
    date_hierarchy = 'created_datetime'
    readonly_fields = (
        'actor', 'filename', 'dry_run', 'created_counts', 'updated_counts', 'changes', 'created_datetime',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(models.MatchingEvaluation)
class MatchingEvaluationAdminView(StaffReadSuperuserWriteMixin, admin.ModelAdmin):
    """
    Provenance-stamped evaluation runs. Staff may read the history; rows are
    created by the harness (command line or Content tools), never by hand.
    """
    list_display = ('id', 'created_datetime', 'labelled_file', 'scorer', 'threshold', 'margin', 'accuracy_at_1')
    list_filter = ('scorer',)
    date_hierarchy = 'created_datetime'
    readonly_fields = (
        'created_datetime', 'git_sha', 'model_id', 'model_artifact', 'runtime',
        'index_fingerprint', 'labelled_file', 'scorer', 'threshold', 'margin',
        'metrics', 'results', 'notes',
    )

    @admin.display(description='acc@1')
    def accuracy_at_1(self, obj):
        return obj.metrics.get('accuracy_at_1')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(models.Setting)
class SettingAdminView(SuperuserOnlyMixin, admin.ModelAdmin):
    """
    Runtime configuration (threshold, margin, alpha). Superuser only: these
    rows change what the live classifier serves.
    """
    list_display = ('key', 'value', 'meta_lastupdated_datetime')
    readonly_fields = ('meta_lastupdated_datetime',)


@admin.register(models.DataInsert)
class DataInsertAdminView(SuperuserOnlyMixin, admin.ModelAdmin):
    """
    Bulk-insert helper. Superuser only: saving a row CREATES triggers, so
    this is a write surface despite its innocuous look.
    """
    list_display = ('id', 'meta_created_datetime')
