from django.db import models
from django.db.models.functions import Upper
import textwrap


class TopicGroup(models.Model):
    """
    A broad area that topics are grouped into
    e.g. 'Politics' or 'Technology'
    """

    name = models.CharField(
        max_length=255,
        help_text="A broad area that topics are grouped into, e.g. 'Politics' or 'Technology'",
        unique=True
    )

    admin_notes = models.TextField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")
    meta_lastupdated_datetime = models.DateTimeField(auto_now=True, verbose_name="last updated")

    def __str__(self):
        return self.name

    class Meta:
        ordering = (Upper('name'), 'id')


class Topic(models.Model):
    """
    A research topic/theme that prompts are organised into
    e.g. 'US Election 2024' (which would belong to the 'Politics' topic group)
    """

    topic_group = models.ForeignKey(TopicGroup, on_delete=models.RESTRICT)
    name = models.CharField(
        max_length=255,
        help_text="A research topic/theme that prompts are organised into, e.g. 'US Election 2024' (which would belong to the 'Politics' topic group).",
        unique=True
    )
    description = models.TextField(
        blank=True,
        null=True,
        help_text=(
            "Clean prose describing the topic, used by the vector classifier. "
            "Prose ONLY: example queries live in their own field below, and "
            "contrasts ('Distinct from ...') in theirs. The better this prose "
            "captures what the topic is about, the better the matching. Topics "
            "without a description fall back to '[group]: [name]' for matching, "
            "which is much weaker."
        )
    )
    example_queries = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Real query phrasings this topic should catch, as a JSON list of "
            "strings. Each is embedded as its own vector and blended with the "
            "description score. One careless entry here can capture other "
            "topics' traffic, so the content pipeline self-retrieval check "
            "must pass before these ship."
        )
    )
    contrasts = models.TextField(
        blank=True,
        default="",
        help_text=(
            "Editorial notes on adjacent topics ('Distinct from X (...), from "
            "Y (...)'). NEVER embedded: this text exists to keep human editors "
            "from writing overlapping topics, and embedding it injects the "
            "competitors' vocabulary into this topic's vector."
        )
    )

    admin_notes = models.TextField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")
    meta_lastupdated_datetime = models.DateTimeField(auto_now=True, verbose_name="last updated")

    @property
    def embedding_text(self):
        """The description text embedded by the classifier (prose only)."""
        if self.description and self.description.strip():
            return self.description.strip()
        return f'{self.topic_group.name}: {self.name}'

    def __str__(self):
        return f'{self.topic_group} - {self.name}'

    class Meta:
        ordering = ('topic_group', Upper('name'), 'id')


class Trigger(models.Model):
    """
    A word/phrase that a user searches for that triggers a prompt.

    Retained as a fallback matching path while the vector classifier is
    primary, and as a labelled vocabulary for threshold calibration.
    """

    trigger_text = models.TextField(
        help_text="A word or phrase that will trigger a prompt to the user. Must match exactly to user's search term.",
        unique=True
    )

    admin_notes = models.TextField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")
    meta_lastupdated_datetime = models.DateTimeField(auto_now=True, verbose_name="last updated")

    def __str__(self):
        return self.trigger_text

    class Meta:
        ordering = ('-meta_lastupdated_datetime', Upper('trigger_text'), 'id')


class Prompt(models.Model):
    """
    A piece of information shown to users to prompt them to perform an action

    An action may be to submit a Response (see below model)
    or simply to read and think about information provided in this prompt
    """

    topic = models.ForeignKey(Topic, on_delete=models.RESTRICT)
    prompt_content = models.TextField()
    seeed_url = models.URLField(
        blank=True,
        null=True,
        max_length=500,
        verbose_name="SEEED URL",
        help_text=(
            "Link to the corresponding SEEED encyclopaedia entry, once it exists. "
            "When set, the prompt card in the extension shows a 'Learn more' link."
        )
    )
    response_required = models.BooleanField(
        default=False,
        help_text="If you'd like the user to respond to this prompt via a text box, please tick this option."
    )
    priority = models.IntegerField(
        blank=True,
        null=True,
        help_text="Set a number that will be used to prioritise prompts if multiple are found (e.g. a priority of 100 will be shown to users before a priority of 1)"
    )

    triggers = models.ManyToManyField(Trigger, related_name='prompts', blank=True)

    admin_approved = models.BooleanField(
        default=False,
        help_text="Only prompts that are approved will be publicly visible (e.g. feature in the web browser extension)."
    )
    admin_notes = models.TextField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")
    meta_lastupdated_datetime = models.DateTimeField(auto_now=True, verbose_name="last updated")

    @property
    def prompt_content_preview(self):
        return textwrap.shorten(self.prompt_content, width=140, placeholder="...")

    def __str__(self):
        return f'Prompt #{self.id} - {str(self.meta_created_datetime)[:19]}: {textwrap.shorten(self.prompt_content, width=50, placeholder="...")}'

    class Meta:
        ordering = ('-meta_created_datetime', 'id')


class Response(models.Model):
    """
    The response that a user submits based on a prompt
    """

    prompt = models.ForeignKey(Prompt, related_name='responses', on_delete=models.PROTECT)
    response_content = models.TextField()

    admin_approved = models.BooleanField(default=False)
    admin_notes = models.TextField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")
    meta_lastupdated_datetime = models.DateTimeField(auto_now=True, verbose_name="last updated")

    def __str__(self):
        return f'Response #{self.id} - {str(self.meta_created_datetime)[:19]}: {textwrap.shorten(self.response_content, width=50, placeholder="...")}'

    class Meta:
        ordering = ['-meta_created_datetime']


class NotRelevantReport(models.Model):
    """
    The report that a user submits if information is not relevant to their search.

    Each row is a labelled example of a query/topic mismatch: the single most
    valuable dataset for calibrating the classifier threshold.
    """

    prompt = models.ForeignKey(Prompt, related_name='notrelevantreports', on_delete=models.PROTECT)
    user_search_query = models.TextField()
    # The classifier confidence at the time the prompt was shown (null when the
    # prompt came from trigger matching). Lets calibration distinguish
    # low-confidence misses from high-confidence errors.
    classifier_confidence = models.FloatField(blank=True, null=True)

    admin_notes = models.TextField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")

    def __str__(self):
        return f'Report #{self.id} - {str(self.meta_created_datetime)[:19]}: {textwrap.shorten(self.user_search_query, width=50, placeholder="...")}'

    class Meta:
        ordering = ['-meta_created_datetime']


class EngagementEvent(models.Model):
    """
    Lightweight, anonymous engagement logging for research use.

    Records that something happened (a prompt was shown, expanded, dismissed,
    its SEEED link followed) without recording who, or what they searched.
    session_key is a random identifier generated inside the extension, never
    derived from any user attribute. Supports session-level engagement analysis
    and post-intervention evaluation designs.
    """

    EVENT_TYPES = [
        ('prompt_shown', 'Prompt shown'),
        ('prompt_expanded', 'Prompt expanded'),
        ('prompt_dismissed', 'Prompt dismissed'),
        ('learn_more_clicked', 'SEEED link clicked'),
        ('response_submitted', 'Response submitted'),
        ('not_relevant_reported', 'Not relevant reported'),
        ('badge_shown', 'AI Mode badge shown'),
        ('badge_opened', 'AI Mode badge opened'),
    ]

    event_type = models.CharField(max_length=32, choices=EVENT_TYPES)
    prompt = models.ForeignKey(Prompt, blank=True, null=True, on_delete=models.SET_NULL)
    topic = models.ForeignKey(Topic, blank=True, null=True, on_delete=models.SET_NULL)
    session_key = models.CharField(
        max_length=64, blank=True,
        help_text="Random per-installation identifier generated by the extension. Not linked to any personal data."
    )
    serp_mode = models.CharField(
        max_length=16, blank=True,
        help_text="Which Google layout the event occurred in: 'classic' or 'ai'."
    )
    classifier_confidence = models.FloatField(blank=True, null=True)

    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")

    def __str__(self):
        return f'{self.event_type} @ {str(self.meta_created_datetime)[:19]}'

    class Meta:
        ordering = ['-meta_created_datetime']


class Setting(models.Model):
    """Key/value store for runtime configuration (e.g. classifier threshold)."""

    key = models.CharField(max_length=64, unique=True)
    value = models.TextField()
    meta_lastupdated_datetime = models.DateTimeField(auto_now=True, verbose_name="last updated")

    def __str__(self):
        return self.key

    class Meta:
        ordering = ('key',)


class ContentApply(models.Model):
    """Audit trail for content package uploads and applies."""

    actor = models.ForeignKey(
        'account.User',
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='content_applies',
    )
    filename = models.CharField(max_length=255, blank=True)
    dry_run = models.BooleanField(default=True)
    created_counts = models.JSONField(default=dict)
    updated_counts = models.JSONField(default=dict)
    changes = models.JSONField(default=list)
    created_datetime = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        tag = "dry run" if self.dry_run else "apply"
        return f"{self.filename or 'package'} ({tag}) @ {self.created_datetime:%Y-%m-%d %H:%M}"

    class Meta:
        ordering = ('-created_datetime',)
        verbose_name = "content apply"
        verbose_name_plural = "content applies"


class MatchingEvaluation(models.Model):
    """
    One run of the matching evaluation harness against a labelled query set.

    A time series, not a moment: every run persists its full provenance
    (code version, model artifact hash, runtime and architecture, index
    fingerprint, decision rule) plus metrics and per-row results, so any
    number quoted in a report or a paper can be traced to the exact system
    state that produced it. Runtime and artifact matter because int8 encoder
    scores differ measurably across CPU architectures; two runs are only
    comparable when these fields say they are.
    """

    created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")
    git_sha = models.CharField(max_length=40, blank=True)
    model_id = models.CharField(max_length=255, blank=True)
    model_artifact = models.CharField(
        max_length=120, blank=True,
        help_text="Hash and size of the ONNX file actually loaded (the filename is always model.onnx).")
    runtime = models.CharField(
        max_length=120, blank=True,
        help_text="onnxruntime version, CPU architecture and Python version that produced the scores.")
    index_fingerprint = models.CharField(max_length=64, blank=True)
    labelled_file = models.CharField(max_length=255, blank=True)
    scorer = models.CharField(
        max_length=64, default="production",
        help_text="Which ranking function produced the scores (production, or a named ablation).")
    threshold = models.FloatField()
    margin = models.FloatField(default=0.0)
    metrics = models.JSONField(default=dict)
    results = models.JSONField(default=list)
    notes = models.TextField(blank=True)

    def __str__(self):
        acc = self.metrics.get("accuracy_at_1")
        return (f"Evaluation @ {self.created_datetime:%Y-%m-%d %H:%M} "
                f"(t={self.threshold}, m={self.margin}, acc@1={acc})")

    class Meta:
        ordering = ("-created_datetime",)


class DataInsert(models.Model):
    """
    A model that allows data to be easily inserted into other models in this project
    """

    create_triggers = models.TextField(
        blank=True, null=True,
        help_text='Include a comma separated list of new Triggers to create them, e.g. "apple, banana, pear"'
    )
    meta_created_datetime = models.DateTimeField(auto_now_add=True, verbose_name="created")

    def save(self, *args, **kwargs):
        """
        Create objects for each specified field.
        """
        # Triggers
        if self.create_triggers:
            for trigger in self.create_triggers.split(','):
                t = trigger.strip()
                if len(t):
                    Trigger.objects.get_or_create(trigger_text=t)
        # Save this DataInsert object
        super().save(*args, **kwargs)
