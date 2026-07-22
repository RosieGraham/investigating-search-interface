import logging
import os

from django.conf import settings
from django.forms.models import model_to_dict
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import models
from .embedding import ClassifierUnavailable, classify_query, embed_query, rank_prompts
from .classifier_config import get_classifier_margin, get_classifier_threshold, get_serve_placeholders

logger = logging.getLogger('researchdata')

MAX_PROMPTS_RETURNED = 4


def _approved_prompts():
    """Base queryset: only admin-approved prompts are ever served (Bug 3 fix)."""
    return models.Prompt.objects.filter(admin_approved=True).select_related('topic', 'topic__topic_group')


def _prompt_payload(prompt, confidence=None, matched_by=None):
    data = {
        'id': prompt.id,
        'topic': str(prompt.topic),
        'topic_id': prompt.topic_id,
        'prompt_content': prompt.prompt_content.replace('\n', '<br>'),
        'response_required': prompt.response_required,
        'seeed_url': prompt.seeed_url or None,
        'matched_by': matched_by,
    }
    if confidence is not None:
        data['confidence'] = round(confidence, 3)
    return data


def _placeholder_payload(topic_id, confidence):
    """
    Same dict shape as _prompt_payload, for a topic that matched above
    threshold but has no approved prompt to serve yet. Evaluation scaffold
    only: gated behind SERVE_PLACEHOLDERS (see
    classifier_config.get_serve_placeholders). Does NOT call
    _approved_prompts(); it serves regardless of approval state, keyed
    only on the matched topic already in hand.
    """
    topic = models.Topic.objects.filter(id=topic_id).select_related('topic_group').first()
    data = {
        'id': None,
        'topic': str(topic) if topic else None,
        'topic_id': topic_id,
        'prompt_content': (
            'This topic does not have a written prompt yet. '
            'We are noting what your search matched.'
        ),
        'response_required': False,
        'seeed_url': None,
        'matched_by': 'placeholder',
    }
    if confidence is not None:
        data['confidence'] = round(confidence, 3)
    return data


def _stem(search_term):
    """
    Light suffix-stripping carried over from the original implementation,
    with the operator-precedence bug fixed: previously
    `len(...) > 4 and endswith('ing') or endswith('ies')` stripped 3 chars
    from ANY word ending in 'ies', including 3-letter ones.
    """
    if len(search_term) > 4 and (search_term.endswith('ing') or search_term.endswith('ies')):
        return search_term[:-3]
    if len(search_term) > 2 and search_term.endswith('s'):
        return search_term[:-1]
    return search_term


def _trigger_match(user_search_query, search_exact, topics_exclude):
    """
    Legacy substring matching, retained as the fallback path.
    Bug 1 fix: every word of the query is tried (no early return).
    Bug 2 fix: exclusion filters on the prompt's TopicGroup id, not Prompt id.
    Returns a list of (prompt, matched_term) best-first.
    """
    search_terms = [user_search_query] if search_exact == 1 else user_search_query.split(' ')
    seen_ids = set()
    matched = []
    for raw_term in search_terms:
        term = raw_term if search_exact == 1 else _stem(raw_term)
        if not term:
            continue
        prompts = _approved_prompts()
        if topics_exclude:
            prompts = prompts.exclude(topic__topic_group__id__in=topics_exclude)
        for prompt in prompts.filter(triggers__trigger_text__icontains=term).order_by('-priority').distinct()[:MAX_PROMPTS_RETURNED]:
            if prompt.id not in seen_ids:
                seen_ids.add(prompt.id)
                matched.append((prompt, term))
    return matched


@csrf_exempt
def prompt_get(request):
    """
    Core API endpoint: given a user's search query, return matching prompt(s).

    Accepts POST (preferred: query stays out of URL-level logs) or GET
    (legacy / diagnostics). csrf_exempt because the Chrome extension cannot
    supply a Django CSRF token.

    Primary path: vector classification (semantic similarity between the query
    and Topic descriptions). Fallback path: legacy trigger substring matching,
    used when the classifier is disabled, unavailable, or finds no confident
    match. The response reports which path produced each prompt.

    Response shape (backward compatible with the v1 popup):
        topics:  list of topic groups with excluded flags (for settings UIs)
        prompt:  first matched prompt object, or false
        prompts: up to 3 matched prompt objects (new in v2)
        classifier: status string ('matched', 'no_match', 'disabled', 'unavailable')
    """
    params = request.POST if request.method == 'POST' else request.GET
    user_search_query = params.get('user_search_query', '').strip()
    try:
        search_exact = int(params.get('search_exact', '0'))
    except ValueError:
        search_exact = 0
    topics_exclude = []
    for topic in params.get('topics_exclude', '').split(','):
        topic = topic.strip()
        if topic.isdigit():
            topics_exclude.append(int(topic))

    # Topic group list (for settings UIs), with excluded flags
    topics_data = [
        {**model_to_dict(group), **{'excluded': 1 if group.id in topics_exclude else 0}}
        for group in models.TopicGroup.objects.all()
    ]

    if not user_search_query:
        response = JsonResponse({'prompt': False, 'prompts': [], 'topics': topics_data, 'classifier': 'no_query'})
        response['Cache-Control'] = 'no-store'
        return response

    prompts_payload = []
    classifier_state = 'disabled'
    serve_placeholders = get_serve_placeholders()

    # --- Primary path: vector classification ---
    if settings.CLASSIFIER_ENABLED:
        try:
            # Embed once; reuse for both topic classification and prompt ranking.
            query_vec = embed_query(user_search_query)
            matches = classify_query(user_search_query, _query_vec=query_vec)
            classifier_state = 'matched' if matches else 'no_match'
            for topic_id, confidence in matches:
                if len(prompts_payload) >= MAX_PROMPTS_RETURNED:
                    break
                # Fetch all candidates for this topic in one query.
                candidate_map = {
                    p.id: p
                    for p in _approved_prompts()
                    .filter(topic_id=topic_id)
                    .exclude(topic__topic_group__id__in=topics_exclude)
                }
                if not candidate_map:
                    if serve_placeholders:
                        prompts_payload.append(_placeholder_payload(topic_id, confidence))
                    continue
                # Similarity decides WHICH prompts are shown, which is the only
                # thing it can usefully decide once the cap is smaller than the
                # candidate set. Editorial priority decides the ORDER they are
                # presented in, because within a matched topic every candidate
                # is on-topic and similarity between the query and each prompt
                # is close to noise. With a cap of one the sort is a no-op.
                ranked = rank_prompts(query_vec, list(candidate_map.keys()))
                n_take = MAX_PROMPTS_RETURNED - len(prompts_payload)
                chosen = [
                    candidate_map[pid]
                    for pid, _score in ranked[:n_take]
                    if pid in candidate_map
                ]
                chosen.sort(key=lambda p: (-(p.priority or 0), p.id))
                for prompt in chosen:
                    prompts_payload.append(_prompt_payload(prompt, confidence, matched_by='classifier'))
        except ClassifierUnavailable:
            classifier_state = 'unavailable'
        except Exception:
            logger.exception('Classifier error; falling back to triggers.')
            classifier_state = 'error'

    # --- Fallback path: legacy trigger matching ---
    if not prompts_payload and settings.TRIGGER_FALLBACK_ENABLED:
        for prompt, term in _trigger_match(user_search_query, search_exact, topics_exclude)[:MAX_PROMPTS_RETURNED]:
            prompts_payload.append(_prompt_payload(prompt, matched_by='trigger'))

    response = JsonResponse({
        'prompt': prompts_payload[0] if prompts_payload else False,
        'prompts': prompts_payload,
        'topics': topics_data,
        'classifier': classifier_state,
    })
    response['Cache-Control'] = 'no-store'
    return response


def classifier_debug(request):
    """
    Read-only diagnostic for editorial spot checks and intern query testing.

    Unlike prompt_get, this reports the classifier's top matches even when the
    matched topics carry no approved prompt, so description quality can be
    checked for every topic. Returns topic names, groups, raw confidences,
    and the active threshold. Stores nothing; returns no prompt text.

        /data/classifier/debug/?user_search_query=...
    """
    user_search_query = request.GET.get('user_search_query', '').strip()
    threshold = get_classifier_threshold()
    configured_margin = get_classifier_margin()
    base = {'threshold': threshold, 'margin': configured_margin, 'matches': []}
    if not user_search_query:
        return JsonResponse({**base, 'classifier': 'no_query'})
    if not settings.CLASSIFIER_ENABLED:
        return JsonResponse({**base, 'classifier': 'disabled'})
    try:
        # margin=0.0: the debug view always shows what the index thinks,
        # including matches the production margin rule would abstain on.
        raw = classify_query(user_search_query, threshold=0.0, top_k=5, margin=0.0)
    except ClassifierUnavailable:
        return JsonResponse({**base, 'classifier': 'unavailable'})

    topic_map = {
        topic.id: topic
        for topic in models.Topic.objects.filter(
            id__in=[topic_id for topic_id, _ in raw]
        ).select_related('topic_group')
    }
    matches = []
    for topic_id, confidence in raw:
        topic = topic_map.get(topic_id)
        if topic is None:
            continue
        matches.append({
            'topic_id': topic_id,
            'topic': topic.name,
            'group': topic.topic_group.name if topic.topic_group_id else None,
            'confidence': round(confidence, 4),
            'above_threshold': confidence >= threshold,
            'has_description': bool(topic.description),
            'example_query_count': len(topic.example_queries or []),
        })
    top_gap = (round(raw[0][1] - raw[1][1], 4) if len(raw) > 1 else None)
    return JsonResponse({
        **base,
        'matches': matches,
        'top1_top2_gap': top_gap,
        'margin_would_abstain': (top_gap is not None
                                 and configured_margin > 0
                                 and top_gap < configured_margin),
        'classifier': 'ok',
    })


CANARY_QUERIES = [
    # (query, expected_top1_topic_name or None for below-threshold)
    # Small fixed probes whose correct behaviour is stable and known.
    # If content or code changes what these return, the status endpoint
    # says so before a human has to discover it in a spreadsheet.
    ('what is SEO', 'Search engine optimisation'),
    ('why does everyone use Google', 'Concentration and defaults'),
    ('weather tomorrow', None),
]


def ops_status(request):
    """
    Machine-readable operational truth: is what an editor is looking at
    what users are getting?

    Public and read-only, like /healthz: exposes counts, hashes, config and
    canary matching results. No participant data, no secrets, no prompt
    text. Most of this project's costly debugging has been invisible state
    (a stale index, a cached flag, an unapplied file); this endpoint makes
    the invisible state a URL.
    """
    from django.conf import settings as dj_settings

    from . import embedding
    from .evaluation import model_artifact_fingerprint, runtime_descriptor

    status = {'service': 'investigating-search-interface', 'time': None}
    import datetime
    status['time'] = datetime.datetime.utcnow().isoformat() + 'Z'
    status['git_commit'] = os.environ.get('RENDER_GIT_COMMIT', 'unknown')[:12]

    # NOTE: the classifier block is filled in AFTER the canary loop below.
    # The canaries load the index on a freshly woken worker, so capturing
    # classifier_status() here would report index_rows 0 in the same response
    # whose canaries pass (observed at the 10 July deploy verification).

    try:
        last_apply = (models.ContentApply.objects.filter(dry_run=False)
                      .order_by('-created_datetime').first())
        last_eval = models.MatchingEvaluation.objects.first()
        status['content'] = {
            'topics': models.Topic.objects.count(),
            'topics_with_description': models.Topic.objects.exclude(
                description__isnull=True).exclude(description='').count(),
            'topics_with_example_queries': models.Topic.objects.exclude(
                example_queries=[]).count(),
            'approved_prompts': models.Prompt.objects.filter(admin_approved=True).count(),
            'last_apply': last_apply.created_datetime.isoformat() if last_apply else None,
            'last_evaluation': {
                'when': last_eval.created_datetime.isoformat(),
                'accuracy_at_1': last_eval.metrics.get('accuracy_at_1'),
                'coverage': last_eval.metrics.get('coverage'),
                'labelled_file': last_eval.labelled_file,
            } if last_eval else None,
        }
        status['database'] = 'ok'
    except Exception:
        status['database'] = 'error'

    status['config'] = {
        'classifier_enabled': dj_settings.CLASSIFIER_ENABLED,
        'trigger_fallback_enabled': dj_settings.TRIGGER_FALLBACK_ENABLED,
        'top_k': dj_settings.CLASSIFIER_TOP_K,
    }

    canaries = []
    all_pass = True
    for query, expected in CANARY_QUERIES:
        row = {'query': query, 'expected_top1': expected}
        try:
            matches = classify_query(query, threshold=0.0, top_k=1, margin=0.0)
            if matches:
                topic = models.Topic.objects.filter(id=matches[0][0]).first()
                row['top1'] = topic.name if topic else str(matches[0][0])
                row['confidence'] = round(matches[0][1], 4)
                threshold = get_classifier_threshold()
                served = matches[0][1] >= threshold
                if expected is None:
                    row['pass'] = not served
                else:
                    row['pass'] = served and row['top1'] == expected
            else:
                row['top1'] = None
                row['pass'] = expected is None
        except ClassifierUnavailable:
            row['pass'] = False
            row['error'] = 'classifier unavailable'
        except Exception:
            row['pass'] = False
            row['error'] = 'canary error'
        all_pass = all_pass and row['pass']
        canaries.append(row)
    status['canaries'] = canaries
    status['canaries_pass'] = all_pass

    # Captured after the canaries so a cold worker reports the index the
    # canaries just loaded, not the empty pre-warm state.
    try:
        classifier = embedding.classifier_status()
    except Exception:
        classifier = {'error': 'classifier status unavailable'}
    classifier['model_artifact'] = model_artifact_fingerprint()
    classifier['runtime'] = runtime_descriptor()
    status['classifier'] = classifier

    return JsonResponse(status)


@csrf_exempt
def response_post(request):
    """
    Function-based view to create a new Response data object
    """

    user_response_content = request.POST.get('user_response_content', '')
    active_prompt_id = request.POST.get('active_prompt_id', '')
    response = None
    if len(user_response_content) and len(active_prompt_id):
        try:
            prompt = models.Prompt.objects.get(id=active_prompt_id)
        except (models.Prompt.DoesNotExist, ValueError):
            return JsonResponse({'response_saved': 0}, status=400)
        response = models.Response.objects.create(
            prompt=prompt,
            response_content=user_response_content
        )
    data = {'response_saved': 1 if response else 0}
    return JsonResponse(data)


@csrf_exempt
def notrelevantreport_post(request):
    """
    Function-based view to create a new NotRelevantReport data object.
    Each report is a labelled query/topic mismatch used to calibrate the
    classifier threshold.
    """

    active_prompt_id = request.POST.get('active_prompt_id', '')
    user_search_query = request.POST.get('user_search_query', '')
    confidence = request.POST.get('classifier_confidence', '')
    report = None
    if len(user_search_query) and len(active_prompt_id):
        try:
            prompt = models.Prompt.objects.get(id=active_prompt_id)
        except (models.Prompt.DoesNotExist, ValueError):
            return JsonResponse({'report_saved': 0}, status=400)
        report = models.NotRelevantReport.objects.create(
            prompt=prompt,
            user_search_query=user_search_query,
            classifier_confidence=float(confidence) if confidence else None,
        )
    data = {'report_saved': 1 if report else 0}
    return JsonResponse(data)


@csrf_exempt
def event_post(request):
    """
    Anonymous engagement event logging (research instrument).
    Accepts: event_type (required), prompt_id, topic_id, session_key,
    serp_mode, classifier_confidence. Never accepts or stores query text.
    """
    event_type = request.POST.get('event_type', '')
    valid_types = {choice[0] for choice in models.EngagementEvent.EVENT_TYPES}
    if event_type not in valid_types:
        return JsonResponse({'event_saved': 0}, status=400)

    def _fk(model, value):
        if value and value.isdigit():
            return model.objects.filter(id=int(value)).first()
        return None

    confidence = request.POST.get('classifier_confidence', '')
    models.EngagementEvent.objects.create(
        event_type=event_type,
        prompt=_fk(models.Prompt, request.POST.get('prompt_id', '')),
        topic=_fk(models.Topic, request.POST.get('topic_id', '')),
        session_key=request.POST.get('session_key', '')[:64],
        serp_mode=request.POST.get('serp_mode', '')[:16],
        classifier_confidence=float(confidence) if confidence else None,
    )
    return JsonResponse({'event_saved': 1})
