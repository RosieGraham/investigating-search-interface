"""
Regression tests for the four bugs fixed in June 2026, plus coverage of the
classifier-first/trigger-fallback flow, the SEEED link field, and the
engagement event endpoint.

Run with:  python manage.py test researchdata
The classifier itself is stubbed here (no model download needed); the real
model is exercised by scripts/calibrate_threshold.py instead.
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from . import models
from .embedding import ClassifierUnavailable


def make_content(topic_group='Politics', topic='Donald Trump 2016 presidential campaign',
                 prompt_text='Reflect on how election information is ranked.',
                 triggers=('donald', 'trump'), approved=True, **prompt_kwargs):
    group, _ = models.TopicGroup.objects.get_or_create(name=topic_group)
    topic_obj, _ = models.Topic.objects.get_or_create(name=topic, defaults={'topic_group': group})
    prompt = models.Prompt.objects.create(
        topic=topic_obj, prompt_content=prompt_text, admin_approved=approved, **prompt_kwargs
    )
    for t in triggers:
        trigger, _ = models.Trigger.objects.get_or_create(trigger_text=t)
        prompt.triggers.add(trigger)
    return group, topic_obj, prompt


@override_settings(CLASSIFIER_ENABLED=False)
class TriggerFallbackBugFixTests(TestCase):
    """The legacy matching path, with Bugs 1-3 fixed."""

    def get(self, query, **params):
        return self.client.get(
            reverse('researchdata:prompt-get'),
            {'user_search_query': query, **params},
        ).json()

    def test_bug1_second_word_of_query_matches(self):
        """Bug 1: the view returned after the first word; later words never matched."""
        make_content(triggers=('trump',))
        data = self.get('president trump rally')  # match is on word 2
        self.assertTrue(data['prompt'], 'Multi-word query should match on its second word')
        self.assertEqual(data['prompt']['matched_by'], 'trigger')

    def test_bug2_topics_exclude_filters_topic_group(self):
        """Bug 2: exclusion filtered Prompt.id instead of the TopicGroup id."""
        group, _, _ = make_content(triggers=('trump',))
        data = self.get('trump', topics_exclude=str(group.id))
        self.assertFalse(data['prompt'], 'Prompt should be excluded via its topic group id')
        # An unrelated exclusion id must NOT hide the prompt
        data = self.get('trump', topics_exclude=str(group.id + 999))
        self.assertTrue(data['prompt'])

    def test_bug3_unapproved_prompts_never_served(self):
        """Bug 3: prompts were served regardless of admin_approved."""
        make_content(triggers=('trump',), approved=False)
        data = self.get('trump')
        self.assertFalse(data['prompt'], 'Unapproved prompt must not be served')

    def test_stemming_no_longer_mangles_short_ies_words(self):
        """Operator-precedence fix: 'pies' stems to 'pie' (s-rule), never 'p'."""
        make_content(triggers=('pie',))
        data = self.get('pies')
        self.assertTrue(data['prompt'])

    def test_exact_search_skips_stemming(self):
        make_content(triggers=('climate crisis',))
        data = self.get('climate crisis', search_exact='1')
        self.assertTrue(data['prompt'])


class Bug4ModelStrTests(TestCase):
    def test_notrelevantreport_str_works(self):
        """Bug 4: __str__ referenced a non-existent field and raised AttributeError."""
        _, _, prompt = make_content()
        report = models.NotRelevantReport.objects.create(
            prompt=prompt, user_search_query='donald duck disney'
        )
        text = str(report)  # must not raise
        self.assertIn('donald duck', text)


class ClassifierFlowTests(TestCase):
    """The classifier-first path, with the classifier stubbed."""

    def get(self, query, **params):
        return self.client.get(
            reverse('researchdata:prompt-get'),
            {'user_search_query': query, **params},
        ).json()

    @override_settings(CLASSIFIER_ENABLED=True)
    def test_classifier_match_returns_prompt_with_confidence(self):
        _, topic, prompt = make_content(triggers=())
        prompt.seeed_url = 'https://seeed.example.org/entries/election-information'
        prompt.save()
        with mock.patch('researchdata.views.classify_query', return_value=[(topic.id, 0.82)]):
            data = self.get('who won the 2016 election')
        self.assertTrue(data['prompt'])
        self.assertEqual(data['prompt']['matched_by'], 'classifier')
        self.assertAlmostEqual(data['prompt']['confidence'], 0.82)
        self.assertEqual(data['prompt']['seeed_url'], 'https://seeed.example.org/entries/election-information')
        self.assertEqual(data['classifier'], 'matched')

    @override_settings(CLASSIFIER_ENABLED=True)
    def test_classifier_respects_topic_group_exclusion(self):
        group, topic, _ = make_content(triggers=())
        with mock.patch('researchdata.views.classify_query', return_value=[(topic.id, 0.9)]):
            data = self.get('anything', topics_exclude=str(group.id))
        self.assertFalse(data['prompt'])

    @override_settings(CLASSIFIER_ENABLED=True, TRIGGER_FALLBACK_ENABLED=True)
    def test_classifier_unavailable_falls_back_to_triggers(self):
        make_content(triggers=('trump',))
        with mock.patch('researchdata.views.classify_query', side_effect=ClassifierUnavailable('no model')):
            data = self.get('trump speech')
        self.assertTrue(data['prompt'])
        self.assertEqual(data['prompt']['matched_by'], 'trigger')
        self.assertEqual(data['classifier'], 'unavailable')

    @override_settings(CLASSIFIER_ENABLED=True)
    def test_multiple_prompts_returned_up_to_three(self):
        group, topic, _ = make_content(triggers=())
        for i in range(4):
            models.Prompt.objects.create(
                topic=topic, prompt_content=f'Extra prompt {i}', admin_approved=True, priority=i
            )
        with mock.patch('researchdata.views.classify_query', return_value=[(topic.id, 0.7)]):
            data = self.get('query')
        self.assertEqual(len(data['prompts']), 3)
        self.assertTrue(all(p['matched_by'] == 'classifier' for p in data['prompts']))


class FeedbackEndpointTests(TestCase):
    def test_notrelevantreport_post_stores_confidence(self):
        _, _, prompt = make_content()
        resp = self.client.post(reverse('researchdata:notrelevantreport-post'), {
            'active_prompt_id': prompt.id,
            'user_search_query': 'donald duck disney',
            'classifier_confidence': '0.41',
        })
        self.assertEqual(resp.json()['report_saved'], 1)
        report = models.NotRelevantReport.objects.get()
        self.assertAlmostEqual(report.classifier_confidence, 0.41)

    def test_event_post_valid_and_invalid(self):
        _, topic, prompt = make_content()
        resp = self.client.post(reverse('researchdata:event-post'), {
            'event_type': 'prompt_shown',
            'prompt_id': prompt.id,
            'topic_id': topic.id,
            'session_key': 'abc123',
            'serp_mode': 'classic',
            'classifier_confidence': '0.5',
        })
        self.assertEqual(resp.json()['event_saved'], 1)
        self.assertEqual(models.EngagementEvent.objects.count(), 1)

        resp = self.client.post(reverse('researchdata:event-post'), {'event_type': 'not_a_thing'})
        self.assertEqual(resp.status_code, 400)

    def test_event_post_never_stores_query_text(self):
        """Privacy guarantee: event_post has no field that accepts query text."""
        resp = self.client.post(reverse('researchdata:event-post'), {
            'event_type': 'prompt_shown',
            'user_search_query': 'this should be ignored',
        })
        self.assertEqual(resp.json()['event_saved'], 1)
        event = models.EngagementEvent.objects.get()
        for field in event._meta.get_fields():
            value = getattr(event, field.name, None)
            if isinstance(value, str):
                self.assertNotIn('ignored', value)


class HealthzTests(TestCase):
    def test_healthz(self):
        resp = self.client.get('/healthz')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')
