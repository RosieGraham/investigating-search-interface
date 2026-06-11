from django.contrib import admin
from . import models


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
class TopicGroupAdminView(admin.ModelAdmin):
    list_display = ('id', 'name', 'meta_created_datetime', 'meta_lastupdated_datetime')
    search_fields = ('name',)


@admin.register(models.Topic)
class TopicAdminView(admin.ModelAdmin):
    list_display = ('id', 'name', 'topic_group', 'has_description', 'meta_lastupdated_datetime')
    list_filter = ('topic_group',)
    search_fields = ('name', 'description')
    autocomplete_fields = ('topic_group',)

    @admin.display(boolean=True, description='Description written?')
    def has_description(self, obj):
        return bool(obj.description and obj.description.strip())


@admin.register(models.Trigger)
class TriggerAdminView(admin.ModelAdmin):
    """
    Customise the content of the list of Triggers in the Django admin
    """
    list_display = ('id', 'trigger_text', 'meta_created_datetime', 'meta_lastupdated_datetime')
    search_fields = ('trigger_text',)
    list_per_page = 100


@admin.register(models.Prompt)
class PromptAdminView(admin.ModelAdmin):
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

    @admin.display(boolean=True, description='SEEED link?')
    def has_seeed_url(self, obj):
        return bool(obj.seeed_url)


@admin.register(models.Response)
class ResponseAdminView(admin.ModelAdmin):
    """
    Customise the content of the list of Responses in the Django admin
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
class NotRelevantReportAdminView(admin.ModelAdmin):
    """
    Labelled query/topic mismatches - the calibration dataset for the
    classifier threshold.
    """
    list_display = ('id', 'prompt', 'user_search_query', 'classifier_confidence', 'meta_created_datetime')
    search_fields = ('user_search_query',)
    autocomplete_fields = ('prompt',)


@admin.register(models.EngagementEvent)
class EngagementEventAdminView(admin.ModelAdmin):
    """
    Anonymous engagement events (research instrument). Read-only.
    """
    list_display = ('id', 'event_type', 'prompt', 'topic', 'serp_mode', 'classifier_confidence', 'meta_created_datetime')
    list_filter = ('event_type', 'serp_mode')
    date_hierarchy = 'meta_created_datetime'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(models.DataInsert)
class DataInsertAdminView(admin.ModelAdmin):
    """
    Customise the content of the list of DataInserts in the Django admin
    """
    list_display = ('id', 'meta_created_datetime')
