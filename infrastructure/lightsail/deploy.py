#!/usr/bin/env python3
"""Promote the current main commit once its push workflow succeeds."""

import json
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
from time import sleep
from urllib.request import urlopen


STATE = Path('/var/lib/talky-deploy')
COMPOSE = [
    'docker', 'compose', '--project-directory', '/opt/talky', '-p', 'talky',
    '-f', 'docker-compose.yml', '-f', 'docker-compose.api.yml',
    '-f', 'docker-compose.lightsail.yml', '-f', 'docker-compose.https.yml',
]
SERVICES = ['app', 'worker', 'graph-worker']
LOCAL_IMAGE = 'talky-app:local'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(*args):
    return run(*args, stdout=subprocess.PIPE, text=True).stdout.strip()


def ci_passed(runs, commit):
    latest = next(iter(runs), {})
    return (
        latest.get('head_sha') == commit and latest.get('head_branch') == 'main'
        and latest.get('event') == 'push' and latest.get('status') == 'completed'
        and latest.get('conclusion') == 'success'
    )


def wait_for_services(compose):
    run(*compose, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '150', *SERVICES)
    for attempt in range(15):
        try:
            for service, role in [('worker', 'worker'), ('graph-worker', 'graph')]:
                run(*compose, 'exec', '-T', service, 'python', '-m', 'app.health', role)
            return
        except subprocess.CalledProcessError:
            if attempt == 14:
                raise
            sleep(2)


def deploy(previous, commit):
    candidate = f'talky-app:{commit}'
    rollback = f'talky-app:rollback-{previous}'
    with TemporaryDirectory(prefix='talky-build-') as temp:
        source = Path(temp) / 'source'
        source.mkdir()
        archive = Path(temp) / 'source.tar'
        with archive.open('wb') as handle:
            run('git', 'archive', commit, stdout=handle)
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter='data')
        compose = COMPOSE.copy()
        for name in ('docker-compose.yml', 'docker-compose.api.yml'):
            compose[compose.index(name)] = source / name
        run(*compose, 'config', '--quiet')
        run('docker', 'build', '-f', source / 'docker/Dockerfile.app', '-t', candidate, source)
        saved_image = subprocess.run(['docker', 'image', 'inspect', rollback],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if saved_image.returncode:
            previous_image = output('docker', 'inspect', '--format', '{{.Image}}', 'talky-app-1')
            run('docker', 'tag', previous_image, rollback)
        try:
            if output('git', 'diff', '--name-only', previous, commit, '--', 'app/db/migrations'):
                run(*COMPOSE, 'stop', *SERVICES)
            run('docker', 'tag', candidate, LOCAL_IMAGE)
            wait_for_services(compose)
            run('git', 'checkout', '--detach', commit)
        except subprocess.CalledProcessError:
            # The previous web image may only start if its schema contract still holds.
            run('docker', 'tag', rollback, LOCAL_IMAGE)
            try:
                run(*COMPOSE, 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'python', 'app', '-c',
                    'from app.db.postgres import get_engine; from app.schema_contract import check_schema; '
                    'check_schema(get_engine().connect())')
                wait_for_services(COMPOSE)
            except subprocess.CalledProcessError:
                print('Automatic rollback could not restore the previous services; inspect the DB schema and logs.')
            raise
        finally:
            # The active tag is shared with the existing key-rotation and Compose commands.
            subprocess.run(['docker', 'image', 'rm', candidate, rollback], check=False)


def main():
    run('git', 'fetch', '--no-tags', 'origin', 'refs/heads/main')
    previous = output('git', 'rev-parse', 'HEAD')
    commit = output('git', 'rev-parse', 'FETCH_HEAD')
    failed = STATE / 'failed-commit'
    if commit == previous or (failed.exists() and failed.read_text().strip() == commit):
        return
    run('git', 'diff', '--quiet', 'HEAD', '--')
    run('git', 'merge-base', '--is-ancestor', previous, commit)
    url = ('https://api.github.com/repos/kingrangE/Talky/actions/workflows/ci.yml/runs'
           f'?branch=main&event=push&head_sha={commit}&per_page=1')
    with urlopen(url, timeout=20) as response:
        if not ci_passed(json.load(response)['workflow_runs'], commit):
            print(f'Waiting for successful CI on {commit}.')
            return
    try:
        deploy(previous, commit)
    except subprocess.CalledProcessError:
        failed.write_text(commit + '\n')
        raise
    failed.unlink(missing_ok=True)
    print(f'Deployed {commit}.')


if __name__ == '__main__':
    main()
