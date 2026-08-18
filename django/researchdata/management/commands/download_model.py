"""
Download the ONNX sentence-embedding model + tokenizer into EMBEDDING_MODEL_DIR.

Runs during the Render build (see render.yaml) and can be run locally:
    python manage.py download_model

The workshop release pins one Hugging Face revision and one int8 ONNX
filename. It does not try a candidate list.
"""

import shutil

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from researchdata.embedding import MODEL_FILENAME, TOKENIZER_FILENAME, _model_dir
from researchdata.model_pin import ModelPinError, verify_model_artifacts


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
        revision = settings.EMBEDDING_MODEL_REVISION
        onnx_filename = settings.EMBEDDING_ONNX_FILENAME

        if model_path.exists() and tokenizer_path.exists() and not options['force']:
            try:
                verify_model_artifacts(
                    model_dir=model_dir,
                    expected_model_sha256=settings.EMBEDDING_MODEL_SHA256,
                    expected_tokenizer_sha256=settings.EMBEDDING_TOKENIZER_SHA256,
                    expected_onnx_filename=onnx_filename,
                )
            except ModelPinError as exc:
                raise CommandError(f'Present model failed pin check: {exc}')
            self.stdout.write(f'Model already present in {model_dir} (use --force to re-download).')
            return

        self.stdout.write(f'Downloading tokenizer from {repo}@{revision}...')
        tok_file = hf_hub_download(repo_id=repo, filename='tokenizer.json', revision=revision)
        shutil.copyfile(tok_file, tokenizer_path)

        self.stdout.write(f'Downloading {onnx_filename} from {repo}@{revision}...')
        onnx_file = hf_hub_download(repo_id=repo, filename=onnx_filename, revision=revision)
        shutil.copyfile(onnx_file, model_path)
        try:
            verify_model_artifacts(
                model_dir=model_dir,
                expected_model_sha256=settings.EMBEDDING_MODEL_SHA256,
                expected_tokenizer_sha256=settings.EMBEDDING_TOKENIZER_SHA256,
                expected_onnx_filename=onnx_filename,
            )
        except ModelPinError as exc:
            raise CommandError(f'Downloaded model failed pin check: {exc}')
        size_mb = model_path.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f'Model ready: {onnx_filename} -> {model_path} ({size_mb:.1f} MB)'
        ))
