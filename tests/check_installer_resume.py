#!/usr/bin/env python3
"""Isolated installer checks: fake kit, fake APT/sudo, injected source fixtures."""
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile

SOURCE = Path(__file__).resolve().parents[1] / 'INSTALL-UBUNTU.sh'
original = SOURCE.read_text()

def write(path, text, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)

with tempfile.TemporaryDirectory(prefix='installer-resume-qa-') as temporary:
    base = Path(temporary)
    kit = base / 'kit with spaces'
    apt = base / 'etc-apt'
    keyring = base / 'ubuntu-archive-keyring.gpg'
    os_release = base / 'os-release'
    write(keyring, 'fixture')
    write(os_release, 'ID=ubuntu\nVERSION_CODENAME=resolute\n')
    write(kit / 'artifacts/veliqen-link_1.2.1_amd64.deb', 'placeholder; never installed')
    write(kit / 'linux/veliqen_link/__init__.py', '')
    write(kit / 'linux/veliqen_link/session_setup.py', (SOURCE.parent / 'linux/veliqen_link/session_setup.py').read_text())
    write(kit / 'scripts/STOP-RUNNING.py', "import json, os\nwith open(os.environ['QA_LOG'], 'a') as f: f.write(json.dumps({'phase':'stop'})+'\\n')\nraise SystemExit(8 if os.environ.get('QA_FAIL') == 'stop' else 0)\n")
    for filename, phase in [('INSTALL-SCRCPY.sh', 'scrcpy'), ('INSTALL-GNOME-BRIDGE.sh', 'gnome')]:
        write(kit / 'scripts' / filename, '#!/usr/bin/env bash\nprintf \'{"phase":"' + phase + '"}\\n\' >> "$QA_LOG"\n', True)
    write(apt / 'sources.list', 'deb [arch=amd64] http://ru.archive.ubuntu.com/ubuntu resolute-updates main restricted # retain comment\n# deb http://archive.ubuntu.com/ubuntu noble main\ndeb https://repo.yandex.ru/yandex-browser/deb stable main\n')
    trusted = 'Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg'
    inline_key = 'Signed-By:\n -----BEGIN PGP PUBLIC KEY BLOCK-----\n .\n fixture\n -----END PGP PUBLIC KEY BLOCK-----'
    write(apt / 'sources.list.d/ubuntu.sources', 'Types: deb\nURIs: http://ru.archive.ubuntu.com/ubuntu/\nSuites: resolute resolute-backports\nComponents: main universe restricted multiverse\nArchitectures: amd64\n' + trusted + '\n\nTypes: deb\nURIs: http://security.ubuntu.com/ubuntu\nSuites: resolute-security\nComponents: main universe\n' + inline_key + '\n\nTypes: deb\nURIs: http://ru.archive.ubuntu.com/ubuntu\nSuites: noble\nComponents: main\nEnabled: no\n\nTypes: deb\nURIs: https://repo.yandex.ru/yandex-browser/deb\nSuites: stable\nComponents: main\n')
    baseline = {str(p): p.read_bytes() for p in apt.rglob('*') if p.is_file()}
    bin_dir = base / 'bin'
    write(bin_dir / 'sudo', '#!/usr/bin/python3\nimport os,sys\nassert sys.argv[1] == "apt-get", sys.argv\nos.execv(os.environ["QA_APT"], ["apt-get", *sys.argv[2:]])\n', True)
    write(bin_dir / 'dpkg', '#!/usr/bin/env bash\nprintf "%s\\n" "${QA_ARCH:-amd64}"\n', True)
    write(bin_dir / 'apt-get', '''#!/usr/bin/python3
import json, os, pathlib, stat, sys
args = sys.argv[1:]
options = dict(args[i+1].split('=', 1) for i, a in enumerate(args) if a == '-o')
phase = 'update' if 'update' in args else 'download' if '--download-only' in args else 'install'
work = pathlib.Path(options['Dir::State::lists']).parent
deb = work / 'veliqen-link_1.2.1_amd64.deb'
parts = pathlib.Path(options.get('Dir::Etc::sourceparts', str(work / 'unused')))
record = {'phase':phase, 'args':args, 'options':options, 'work':str(work),
          'dir_mode':stat.S_IMODE(work.stat().st_mode), 'deb_mode':stat.S_IMODE(deb.stat().st_mode),
          'sources':{p.name:p.read_text() for p in parts.glob('*')}}
with open(os.environ['QA_LOG'], 'a') as f: f.write(json.dumps(record)+'\\n')
raise SystemExit(9 if os.environ.get('QA_FAIL') == phase else 0)
''', True)
    injected = original.replace(' /etc/apt /etc/os-release /usr/share/keyrings/ubuntu-archive-keyring.gpg ', ' ' + ' '.join(shlex.quote(str(p)) for p in (apt, os_release, keyring)) + ' ')
    assert injected != original
    installer = base / 'standalone-fix.sh'
    write(installer, injected, True)
    env = dict(os.environ)
    env.update(PATH=str(bin_dir) + ':' + env['PATH'], QA_APT=str(bin_dir / 'apt-get'), XDG_CONFIG_HOME=str(base / 'config'))
    env.pop('DISPLAY', None)
    env.pop('WAYLAND_DISPLAY', None)
    def run(name, extra=(), fail=None, expected=0):
        log = base / (name + '.jsonl')
        run_env = dict(env, QA_LOG=str(log), QA_FAIL=fail or '')
        result = subprocess.run(['bash', str(installer), '--kit', str(kit), '--ubuntu-main-mirror', '--no-autostart', *extra], cwd=base, env=run_env, text=True, capture_output=True)
        if expected == 0:
            assert result.returncode == 0, (name, result.stdout, result.stderr)
        else:
            assert result.returncode != 0, (name, result.stdout, result.stderr)
        records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        for record in records:
            if 'work' in record:
                assert not Path(record['work']).exists(), 'temporary path leaked'
        return records, result

    records, result = run('success')
    assert [r['phase'] for r in records] == ['update', 'download', 'stop', 'install']
    apt_calls = [r for r in records if 'options' in r]
    assert apt_calls[0]['options'] == apt_calls[1]['options'] == apt_calls[2]['options']
    assert all(r['dir_mode'] == 0o755 and r['deb_mode'] == 0o644 for r in apt_calls)
    assert '--no-remove' in apt_calls[1]['args'] and '--no-remove' in apt_calls[2]['args']
    assert '--download-only' in apt_calls[1]['args']
    assert '--download-only' not in apt_calls[2]['args']
    assert '--no-download' not in apt_calls[2]['args']
    assert all(Path(r['args'][-1]).is_absolute() for r in apt_calls[1:])
    sources = '\n'.join(apt_calls[0]['sources'].values())
    assert 'https://archive.ubuntu.com/ubuntu' in sources and 'https://security.ubuntu.com/ubuntu' in sources
    assert 'repo.yandex.ru' not in sources and 'noble' not in sources and 'ru.archive' not in sources
    assert 'Suites: resolute resolute-backports' in sources and 'Architectures: amd64' in sources
    assert trusted in sources and inline_key in sources
    assert 'signed-by=' + str(keyring) in sources
    assert 'arch=amd64' in sources and '# retain comment' in sources
    assert 'Hidden=true' in (base / 'config/autostart/veliqen-link.desktop').read_text()
    print('PASS success: three consistent APT phases, STOP after download, standalone DEB, private caches, readable package, preserved trust/source fields')

    for phase, sequence in [('update', ['update']), ('download', ['update', 'download']), ('stop', ['update', 'download', 'stop']), ('install', ['update', 'download', 'stop', 'install'])]:
        records, result = run('failure-' + phase, fail=phase, expected=1)
        assert [r['phase'] for r in records] == sequence
    print('PASS failures: update/download never stop app; STOP failure prevents install; install failure prevents finalizers')

    help_log = base / 'help.jsonl'
    result = subprocess.run(['bash', str(installer), '--kit', '/nonexistent/kit', '--help'], cwd=base, env=dict(env, QA_LOG=str(help_log)), text=True, capture_output=True)
    assert result.returncode == 0 and not help_log.exists()
    print('PASS help: invalid kit allowed, no APT/helper invocation')

    write(os_release, 'ID=debian\nVERSION_CODENAME=trixie\n')
    records, result = run('non-ubuntu', expected=1)
    assert not records and 'только для Ubuntu' in result.stderr
    write(os_release, 'ID=ubuntu\nVERSION_CODENAME=resolute\n')
    source_file = apt / 'sources.list.d/ubuntu.sources'
    normal_source = source_file.read_text()
    source_file.write_text(normal_source.replace('URIs: http://ru.archive.ubuntu.com/ubuntu/', 'URIs: http://ru.archive.ubuntu.com/ubuntu/ https://repo.yandex.ru/ubuntu'))
    records, result = run('mixed-stanza', expected=1)
    assert not records and 'смешивает' in result.stderr
    source_file.write_text(normal_source.replace('Components: main universe restricted multiverse', 'Components: main universe restricted multiverse\nTrusted: yes'))
    records, result = run('unsafe-trust', expected=1)
    assert not records and 'проверка доверия' in result.stderr
    source_file.write_text(normal_source)
    assert {str(p):p.read_bytes() for p in apt.rglob('*') if p.is_file()} == baseline
    assert SOURCE.read_text() == original
    print('PASS source refusal: non-Ubuntu, mixed origins, unsafe trust; fixture source bytes unchanged after normal runs')
print('ALL ISOLATED CHECKS PASSED; no real sudo, APT, service, or system file changes')
