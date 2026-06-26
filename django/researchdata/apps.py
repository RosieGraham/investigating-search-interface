from django.apps import AppConfig

app_name = "researchdata"


class ThisAppConfig(AppConfig):
    name = app_name

    def ready(self):
        # Keep the classifier indexes in sync with admin edits.
        # Any save/delete marks the relevant cached index dirty so it rebuilds
        # lazily on the next query rather than blocking admin saves.
        from django.db.models.signals import post_save, post_delete
        from .models import Topic, Prompt
        from .embedding import mark_index_dirty, mark_prompt_index_dirty
        post_save.connect(mark_index_dirty, sender=Topic, dispatch_uid='topic-index-dirty-save')
        post_delete.connect(mark_index_dirty, sender=Topic, dispatch_uid='topic-index-dirty-delete')
        post_save.connect(mark_prompt_index_dirty, sender=Prompt, dispatch_uid='prompt-index-dirty-save')
        post_delete.connect(mark_prompt_index_dirty, sender=Prompt, dispatch_uid='prompt-index-dirty-delete')
