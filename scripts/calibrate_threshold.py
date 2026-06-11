"""
Threshold calibration for the query classifier.

Usage (from the django/ directory, with the model downloaded and data imported):

    python ../scripts/calibrate_threshold.py [--positives 500]

Method
------
The live NotRelevantReport table was empty at migration time (June 2026), so
this script builds its evaluation set from what does exist:

  POSITIVES: a random sample of stored Triggers, each treated as a proxy user
  query whose gold label is the topic(s) of the prompt(s) it is linked to.
  (The 10k trigger list is, in effect, a large labelled query->topic dataset.)

  NEGATIVES: constructed off-topic queries: everyday informational, shopping,
  navigational and entertainment searches that should trigger NOTHING, plus an
  adversarial set sharing surface vocabulary with real topics (the 'donald
  duck disney' class) where substring matching used to fail.

For each candidate threshold it reports:
  coverage   = share of positive queries where a gold topic appears in the
               top-3 matches at or above threshold
  top1-acc   = share of positive queries where the single best match is a
               gold topic at or above threshold
  fp-rate    = share of negative queries that return ANY match at or above
               threshold (lower is better)

Once real NotRelevantReport rows accumulate in production, re-run calibration
with those as the negative set: they are better evidence than constructions.
"""

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'django'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django  # noqa: E402

django.setup()

from researchdata.embedding import classify_query  # noqa: E402
from researchdata.models import Trigger  # noqa: E402

NEGATIVES_EVERYDAY = [
    'cheap flights to malaga', 'best chocolate cake recipe', 'weather tomorrow birmingham',
    'how to tie a tie', 'premier league results today', 'screwfix opening hours',
    'convert 5km to miles', 'next train to london euston', 'how old is tom hanks',
    'birthday present ideas for mum', 'italian restaurants near me', 'how to descale a kettle',
    'film showings this weekend', 'cheapest broadband deals', 'how to draw a horse',
    'what time is sunset today', 'argos discount code', 'when is mothers day 2027',
    'best running shoes flat feet', 'second hand bikes for sale', 'passport renewal how long',
    'easy crochet patterns beginners', 'dishwasher not draining', 'best beaches cornwall',
    'guitar chords wonderwall', 'how to make sourdough starter', 'council tax bands explained',
    'iphone screen repair cost', 'when do clocks change', 'м25 traffic now',
]

NEGATIVES_ADVERSARIAL = [
    # Shares surface vocabulary with stored topics/triggers but is off-topic.
    'donald duck disney',                  # vs Donald Trump topics
    'duck pond near me',
    'trump card game rules',               # 'trump' the word, not the person
    'apple crumble recipe',                # vs tech topics
    'mercury thermometer for sale',        # vs vaccine/mercury topics
    'crystal palace fixtures',             # vs crystal healing topics
    'detox your washing machine',          # vs detox diet topics
    'chemtrails of my tears band',         # invented band name vs chemtrails
    'flat earth society meme shirt',       # merch query vs flat earth topic
    'vitamin water flavours',              # vs supplement topics
    'fasting blood test rules nhs',        # clinical logistics vs fasting diets
    'george soros open society jobs',      # employment query vs conspiracy topic
    'bill gates net worth',                # celebrity-finance vs conspiracy topics
    'electric cars range comparison',      # consumer vs climate topics
    'paleo dog food review',               # pet food vs paleo diet
    'keto bread tesco',                    # shopping vs keto risks
    'is the moon out tonight',             # astronomy-lite vs moon landing hoax
    'area 51 raid meme',                   # meme vs UFO topics
    'illuminati nail art',                 # fashion vs illuminati topic
    'mind control magic trick tutorial',   # entertainment vs 5G mind control
]

THRESHOLDS = [0.25, 0.30, 0.35, 0.40, 0.45, 0.50]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--positives', type=int, default=500, help='Number of triggers to sample as positive queries.')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    triggers = list(
        Trigger.objects.exclude(prompts=None)
        .prefetch_related('prompts__topic')
        .order_by('?')[: args.positives]
    )
    if not triggers:
        sys.exit('No triggers with linked prompts found. Run import_live_export first.')

    print(f'Scoring {len(triggers)} positive queries (sampled triggers) and '
          f'{len(NEGATIVES_EVERYDAY) + len(NEGATIVES_ADVERSARIAL)} negative queries...\n')

    # Score every query once at threshold 0; evaluate thresholds offline.
    positives = []
    for trig in triggers:
        gold = {p.topic_id for p in trig.prompts.all()}
        matches = classify_query(trig.trigger_text, threshold=0.0, top_k=3)
        positives.append((gold, matches))

    negatives = []
    for q in NEGATIVES_EVERYDAY + NEGATIVES_ADVERSARIAL:
        negatives.append(classify_query(q, threshold=0.0, top_k=3))

    print(f'{"threshold":>9} | {"coverage":>8} | {"top1-acc":>8} | {"fp-rate":>8} | {"fp-adversarial":>14}')
    print('-' * 62)
    for th in THRESHOLDS:
        covered = sum(
            1 for gold, m in positives
            if any(tid in gold and s >= th for tid, s in m)
        )
        top1 = sum(
            1 for gold, m in positives
            if m and m[0][1] >= th and m[0][0] in gold
        )
        fp = sum(1 for m in negatives if m and m[0][1] >= th)
        n_adv = len(NEGATIVES_ADVERSARIAL)
        fp_adv = sum(1 for m in negatives[-n_adv:] if m and m[0][1] >= th)
        print(f'{th:>9.2f} | {covered / len(positives):>8.1%} | {top1 / len(positives):>8.1%} | '
              f'{fp / len(negatives):>8.1%} | {fp_adv}/{n_adv:>12}')

    print(
        '\nReading the table: pick the threshold where fp-rate drops sharply while '
        'coverage remains acceptable. Coverage against trigger-text proxies UNDERSTATES '
        'real coverage once Topic descriptions are written (triggers are terse; real '
        'queries carry more context). Re-run after writing descriptions, and again once '
        'NotRelevantReport data accumulates in production.'
    )


if __name__ == '__main__':
    main()
