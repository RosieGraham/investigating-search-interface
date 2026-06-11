from django.apps import AppConfig

app_name = "researchdata"


class ThisAppConfig(AppConfig):
    name = app_name

    def ready(self):
        # Keep the classifier's topic index in sync with admin edits:
        # any save/delete of a Topic marks the cached index dirty so it is
        # rebuilt (lazily) on the next classified query.
        from django.db.models.signals import post_save, post_delete
        from .models import Topic
        from .embedding import mark_index_dirty
        post_save.connect(mark_index_dirty, sender=Topic, dispatch_uid='topic-index-dirty-save')
        post_delete.connect(mark_index_dirty, sender=Topic, dispatch_uid='topic-index-dirty-delete')
