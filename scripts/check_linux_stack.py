"""Build and verify a disposable Linux stack without using the local .env.

The generated image is deliberately retained for inspection or vulnerability
scanning. Only this invocation's Compose project and volumes are removed.
Requires Python 3.10+, Docker with a running Linux daemon, and Compose 2.24.4+.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]

SMOKE_TEST = r'''
import io, json, os, socket, ssl, sys, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
import rest_framework
from psycopg import pq
import importlib.util
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import Client
from PIL import Image
from redis import Redis
from catalog.models import ContactMessage, Inquiry, InquiryAttachment
from config.celery import app
from config.runtime_security import verify_runtime

assert sys.platform == 'linux'
assert os.getuid() == 10001
status = {}
with open('/proc/self/status', encoding='utf-8') as status_file:
    for line in status_file:
        key, _, value = line.partition(':')
        status[key] = value.strip()
assert status['NoNewPrivs'] == '1', status['NoNewPrivs']
assert int(status['CapEff'], 16) == 0, status['CapEff']
assert int(status['CapBnd'], 16) == 0, status['CapBnd']
assert int(status['CapPrm'], 16) == 0, status['CapPrm']
assert sys.version_info[:3] == (3, 13, 18)
assert rest_framework.VERSION == '3.18.3'
assert pq.__impl__ == 'c'
assert pq.version() == 180006
assert importlib.util.find_spec('psycopg_binary') is None
native_runtime = verify_runtime()
assert connection.vendor == 'postgresql'
with connection.cursor() as cursor:
    cursor.execute('select version()')
    server_version = cursor.fetchone()[0]
assert settings.DATABASES['default']['PASSWORD'] == os.environ['DJANGO_DB_PASSWORD']
assert settings.DATABASES['default']['HOST'] == 'postgres'
broker = Redis.from_url(settings.CELERY_BROKER_URL)
assert broker.ping()
redis_version = broker.info('server')['redis_version']
assert redis_version == '7.4.11'
cache.set('linux-check-connection', 'passed', 30)
assert cache.get('linux-check-connection') == 'passed'
cache.delete('linux-check-connection')
assert settings.CACHES['default']['BACKEND'] == 'config.cache.AtomicRedisCache'
counter_key = 'linux-check-atomic-counter'
cache.delete(counter_key)
assert cache.add(counter_key, 0, 30)
assert cache.incr(counter_key) == 1
assert cache.get(counter_key) == 1
cache_redis = Redis.from_url(settings.CACHE_URL)
assert 0 < cache_redis.ttl(cache.make_key(counter_key)) <= 30
cache.delete(counter_key)
try:
    cache.incr(counter_key)
except ValueError:
    pass
else:
    raise AssertionError('Missing counters must not be recreated without a TTL')
assert not cache_redis.exists(cache.make_key(counter_key))
cache_redis.close()
assert settings.CACHE_URL != 'redis://host-only.invalid:6379/1'
assert settings.ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT is False
destination = 'celery@' + socket.gethostname()
assert any(destination in item for item in app.control.ping(destination=[destination], timeout=5))
client = Client()
response = client.post('/api/v1/contact/', data=json.dumps({
    'name':'Linux Check Buyer', 'email':'review@example.com',
    'message':'Isolated Linux integration test', 'privacy_consent':True,
}), content_type='application/json')
assert response.status_code == 201, (response.status_code, response.content[:500])
contact = ContactMessage.objects.get(reference=response.json()['data']['reference'])
image = io.BytesIO()
Image.new('RGB', (2,2), color='white').save(image, format='PNG')
response = client.post('/api/v1/inquiries/', data={
    'name':'Linux Check Buyer', 'email':'review-rfq@example.com', 'privacy_consent':'true',
    'attachments':SimpleUploadedFile('review.png', image.getvalue(), content_type='image/png'),
})
assert response.status_code == 201, (response.status_code, response.content[:500])
inquiry = Inquiry.objects.get(reference=response.json()['data']['reference'])
attachment = InquiryAttachment.objects.get(inquiry=inquiry)
assert attachment.file.storage.exists(attachment.file.name)
deadline = time.monotonic()+30
while time.monotonic() < deadline:
    contact.refresh_from_db()
    inquiry.refresh_from_db()
    attachment.refresh_from_db()
    if (contact.notification_sent_at and contact.confirmation_sent_at
        and inquiry.notification_sent_at and inquiry.confirmation_sent_at
        and attachment.scan_status == InquiryAttachment.ScanStatus.ERROR):
        break
    time.sleep(0.25)
else:
    raise AssertionError('Asynchronous delivery/scanner fail-closed verification timed out')
assert not contact.notification_error and not inquiry.notification_error
assert 'No malware scanner' in attachment.scan_detail
assert client.get('/api/v1/health/').status_code == 200
print(json.dumps({'uid':os.getuid(),'no_new_privileges':True,'effective_capabilities':0,
    'bounding_capabilities':0,'permitted_capabilities':0,
    'python':sys.version.split()[0],'drf':rest_framework.VERSION,
    'python_openssl':ssl.OPENSSL_VERSION,'psycopg_implementation':pq.__impl__,
    'libpq':pq.version(),'bundled_psycopg_binary':False,
    'runtime_policy':native_runtime,
    'postgres':server_version,'redis':redis_version,'redis_broker':True,
    'shared_redis_cache':True,'atomic_cache_increment':True,'special_character_passwords':True,
    'worker_destination_ping':True,'contact_and_rfq':201,'asynchronous_notifications':True,
    'private_attachment_shared_with_worker':True,'missing_scanner_keeps_quarantined':True}))
'''


def output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value or ''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', help='Reuse this existing image instead of building one.')
    parser.add_argument(
        '--output-dir', type=Path, default=ROOT / 'output' / 'linux-check',
        help='Directory for phase logs and results.json (default: output/linux-check).',
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    unique_id = uuid.uuid4().hex[:12]
    project = 'linux-check-' + unique_id
    image = args.image or 'wanshang-linux-check:' + unique_id
    compose_file = ROOT / 'docker-compose.yml'
    results: dict = {
        'project': project, 'image': image, 'image_retained': True,
        'built_image': args.image is None, 'phases': {}, 'passed': False,
    }
    # These are disposable test credentials. The reserved characters exercise
    # the same raw-component connection settings used by the Linux worker.
    test_environment = {
        'POSTGRES_DB': 'linux_check', 'POSTGRES_USER': 'review',
        'POSTGRES_PASSWORD': 'review#@/:% pass+$',
        'REDIS_PASSWORD': 'review#@/:% pass+$',
        'DJANGO_SECRET_KEY': 'review-only-secret-0123456789abcdefghijklmnopqrstuvwxyz',
        'DJANGO_ENV': 'development', 'DJANGO_DEBUG': 'False',
        'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1,testserver',
        'DJANGO_CACHE_URL': 'redis://host-only.invalid:6379/1',
        'ATTACHMENT_ALLOW_UNSCANNED_IN_DEVELOPMENT': 'False',
        'ATTACHMENT_SCANNER_COMMAND': '', 'CELERY_CONCURRENCY': '1',
        'EMAIL_BACKEND': 'django.core.mail.backends.console.EmailBackend',
        'DEFAULT_FROM_EMAIL': 'review@example.com',
        'SALES_NOTIFICATION_EMAIL': 'sales@example.com',
        'CELERY_DATABASE_URL': '', 'CELERY_REDIS_URL': '', 'CELERY_CACHE_URL': '',
        'DJANGO_CORS_ORIGINS': '', 'TURNSTILE_SECRET_KEY': '', 'TURNSTILE_REQUIRED': 'False',
    }
    interpolation_keys = set(re.findall(
        r'\$\{([A-Za-z_][A-Za-z0-9_]*)', compose_file.read_text(encoding='utf-8'),
    ))
    # Shell values have priority over --env-file in Compose. Clear every
    # interpolated key and all Compose control variables, retaining DOCKER_*
    # so the user's selected Docker daemon still works.
    command_env = {
        key: value for key, value in os.environ.items()
        if key not in interpolation_keys | set(test_environment)
        and not key.startswith('COMPOSE_')
    }

    def run(phase: str, command: list[str], *, input_text: str | None = None,
            timeout: int = 300, check: bool = True) -> subprocess.CompletedProcess:
        print('Running ' + phase, flush=True)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, cwd=ROOT, input=input_text, text=True, encoding='utf-8',
                errors='replace', capture_output=True, env=command_env, timeout=timeout,
            )
            combined = completed.stdout + completed.stderr
            code: int | str = completed.returncode
        except subprocess.TimeoutExpired as exc:
            combined = output_text(exc.stdout) + output_text(exc.stderr)
            combined += '\nTimed out after ' + str(timeout) + ' seconds.\n'
            code = 'timeout'
            completed = subprocess.CompletedProcess(command, 1, '', combined)
        except OSError as exc:
            combined = str(exc) + '\n'
            code = 'not-started'
            completed = subprocess.CompletedProcess(command, 1, '', combined)
        (output / (phase + '.log')).write_text(combined, encoding='utf-8')
        results['phases'][phase] = {
            'exit_code': code, 'seconds': round(time.monotonic() - started, 2),
        }
        if completed.returncode:
            print(combined[-5000:], flush=True)
            if check:
                raise RuntimeError(phase + ' failed; see ' + str(output / (phase + '.log')))
        else:
            print(phase + ' passed', flush=True)
        return completed

    with tempfile.TemporaryDirectory(prefix='wanshang-linux-check-') as temporary:
        temporary_dir = Path(temporary)
        env_file = temporary_dir / 'check.env'
        override = temporary_dir / 'check.compose.yml'
        env_file.write_text(
            '\n'.join(f"{key}='{value}'" for key, value in test_environment.items()) + '\n',
            encoding='utf-8',
        )
        # Compose resolves volumes under the explicit, unique project name.
        # No bind mounts or published host ports from the local stack survive.
        override.write_text('''services:
  postgres:
    ports: !reset []
    volumes: !override
      - check_pg:/var/lib/postgresql/data
  redis:
    ports: !reset []
    volumes: !override
      - check_redis:/data
  celery:
    image: ''' + json.dumps(image) + '''
    volumes: !override
      - check_media:/app/media
      - check_private:/app/private_media
volumes:
  check_pg:
  check_redis:
  check_media:
  check_private:
''', encoding='utf-8')
        compose = [
            'docker', 'compose', '--project-directory', str(ROOT), '--project-name', project,
            '--env-file', str(env_file), '-f', str(compose_file), '-f', str(override),
        ]
        stack_started = False
        try:
            run('compose-config', compose + ['config', '--quiet'])
            if args.image is None:
                run('image-build', [
                    'docker', 'build', '--pull', '--tag', image, str(ROOT / 'backend'),
                ], timeout=1200)
            inspected = run('image-inspect', [
                'docker', 'image', 'inspect', image, '--format', '{{.Id}}',
            ])
            results['image_id'] = inspected.stdout.strip()
            stack_started = True
            run('stack-start', compose + [
                'up', '-d', '--no-build', '--wait', '--wait-timeout', '120',
            ], timeout=600)
            run('database-migrations', compose + [
                'run', '--rm', '--no-deps', '-T', 'celery',
                'uv', 'run', 'python', 'manage.py', 'migrate', '--noinput',
            ])
            run('postgres-tests', compose + [
                'run', '--rm', '--no-deps', '-T', '-e', 'DJANGO_ENV=test',
                '-e', 'DJANGO_DEBUG=True',
                '-e', 'DJANGO_SECURITY_TEST_REDIS_URL=redis://:'
                + quote(test_environment['REDIS_PASSWORD'], safe='')
                + '@redis:6379/15',
                'celery',
                'uv', 'run', 'python', 'manage.py', 'test', '--verbosity', '1',
            ], timeout=600)
            run('linux-smoke', compose + [
                'exec', '-T', 'celery', 'uv', 'run', 'python', '-',
            ], input_text=SMOKE_TEST, timeout=120)
            run('stack-status', compose + ['ps', '--format', 'json'])
            results['passed'] = True
        except (Exception, KeyboardInterrupt) as exc:
            results['error'] = str(exc) or type(exc).__name__
            if stack_started:
                run('stack-failure-logs', compose + [
                    'logs', '--no-color', '--tail', '200',
                ], timeout=30, check=False)
        finally:
            # Never target a caller-supplied project or the root stack's name.
            if not re.fullmatch(r'linux-check-[0-9a-f]{12}', project):
                raise RuntimeError('Refusing cleanup outside the generated test project')
            cleanup = run('stack-cleanup', compose + [
                'down', '--volumes', '--remove-orphans',
            ], timeout=90, check=False)
            results['cleanup_exit_code'] = cleanup.returncode
            if cleanup.returncode:
                results['passed'] = False
            (output / 'results.json').write_text(
                json.dumps(results, indent=2) + '\n', encoding='utf-8',
            )
            print(json.dumps(results, indent=2), flush=True)
            print('Image retained for inspection: ' + image, flush=True)
    return 0 if results['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
