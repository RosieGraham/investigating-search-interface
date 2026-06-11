"""
Download the ONNX sentence-embedding model + tokenizer into EMBEDDING_MODEL_DIR.

Runs during the Render build (see render.yaml) and can be run locally:
    python manage.py download_model

Prefers an int8-quantised ONNX export when the model repository provides one
(roughly 4x smaller and faster on CPU, near-identical similarity behaviour);
falls back to the full-precision ONNX export.
"""

import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from researchdata.embedding import MODEL_FILENAME, TOKENIZER_FILENAME, _model_dir

# Tried in order; first available wins.
ONNX_CANDIDATES = [
    'onnx/model_qint8_avx512_vnni.onnx',
    'onnx/model_quint8_avx2.onnx',
    'onnx/model_qint8_arm64.onnx',
    'onnx/model_O2.onnx',
    'onnx/model.onnx',
]


class Command(BaseCommand):
    help = 'Download the ONNX embedding model and tokenizer into EMBEDDING_MODEL_DIR.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Re-download even if files exist.')

    def handle(self, *args, **options):
        try:
            from huggingface_hub import hf_hub_download
        except ImportError:
            raise CommandError('huggingface_hub is not installed (it is in requirements.txt).')

        model_dir = _model_dir()
        model_path = model_dir / MODEL_FILENAME
        tokenizer_path = model_dir / TOKENIZER_FILENAME
        repo = settings.EMBEDDING_MODEL_ID

        if model_path.exists() and tokenizer_path.exists() and not options['force']:
            self.stdout.write(f'Model already present in {model_dir} (use --force to re-download).')
            return

        self.stdout.write(f'Downloading tokenizer from {repo}...')
        tok_file = hf_hub_download(repo_id=repo, filename='tokenizer.json')
        shutil.copyfile(tok_file, tokenizer_path)

        last_error = None
        for candidate in ONNX_CANDIDATES:
            try:
                self.stdout.write(f'Trying {candidate}...')
                onnx_file = hf_hub_download(repo_id=repo, filename=candidate)
                shutil.copyfile(onnx_file, model_path)
                size_mb = model_path.stat().st_size / (1024 * 1024)
                self.stdout.write(self.style.SUCCESS(
                    f'Model ready: {candidate} -> {model_path} ({size_mb:.1f} MB)'
                ))
                return
            except Exception as e:  # try the next candidate
                last_error = e

        raise CommandError(f'No ONNX export could be downloaded from {repo}: {last_error}')
