#!/usr/bin/env python3
"""Exercise real APT's local-deb acquisition without installing any package.

Run as an ordinary user on Debian/Ubuntu. Everything APT can write is rooted
under one TemporaryDirectory. A tiny repository serves synthetic packages only
on 127.0.0.1; it is the sole source. Dir::Bin::dpkg is a guard executable that
records arguments and refuses every install operation with exit 42. The real
dpkg executable is never used to install, unpack or configure anything.

This complements check_installer_resume.py: command stubs cannot reproduce
APT's --no-download/volatile-index pathname failure.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading


def main():
    if os.geteuid() == 0:
        raise SystemExit('Run this isolated check as an ordinary user, without sudo.')
    for executable in ('apt-get', 'dpkg-deb'):
        if not shutil.which(executable):
            raise SystemExit(executable + ' is required.')
    with tempfile.TemporaryDirectory(prefix='veliqen-real-apt-') as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        for directory in ('etc/parts', 'etc/sources.list.d', 'etc/preferences.d',
                          'state/lists/partial', 'archives/partial', 'logs', 'repository'):
            (root / directory).mkdir(parents=True, exist_ok=True)
        for filename in ('etc/apt.conf', 'etc/sources.list', 'state/status', 'state/extended_states'):
            (root / filename).touch()

        def package(name, destination, dependency=None):
            directory = root / ('build-' + name)
            (directory / 'DEBIAN').mkdir(parents=True)
            control = (f'Package: {name}\nVersion: 1.0\nArchitecture: all\n'
                       'Maintainer: Isolated QA <qa@example.invalid>\n'
                       'Description: Synthetic APT acquisition fixture\n')
            if dependency:
                control += 'Depends: ' + dependency + ' (= 1.0)\n'
            (directory / 'DEBIAN/control').write_text(control)
            subprocess.run(['dpkg-deb', '--root-owner-group', '--build', str(directory), str(destination)],
                           check=True, capture_output=True, text=True)
            return control

        dependency = root / 'repository/veliqen-apt-dependency_1.0_all.deb'
        control = package('veliqen-apt-dependency', dependency)
        (root / 'repository/Packages').write_text(
            control + 'Filename: ' + dependency.name + '\n'
            + 'Size: ' + str(dependency.stat().st_size) + '\n'
            + 'SHA256: ' + hashlib.sha256(dependency.read_bytes()).hexdigest() + '\n\n')
        local_deb = root / 'veliqen-apt-fixture_1.0_all.deb'
        package('veliqen-apt-fixture', local_deb, 'veliqen-apt-dependency')
        calls_path = root / 'dpkg-calls.jsonl'
        guard = root / 'dpkg-guard'
        guard.write_text('''#!/usr/bin/python3
import json, sys
from pathlib import Path
with (Path(__file__).parent / 'dpkg-calls.jsonl').open('a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
if '--print-architecture' in sys.argv:
    print('amd64')
    sys.exit(0)
if '--print-foreign-architectures' in sys.argv or any(a.startswith('--assert-') for a in sys.argv):
    sys.exit(0)
print('QA_DPKG_GUARD: installation deliberately refused', file=sys.stderr)
sys.exit(42)
''')
        guard.chmod(0o755)
        requests = []

        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, _format, *_arguments):
                pass

            def do_GET(self):
                requests.append(self.path)
                return super().do_GET()

        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root / 'repository')))
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # trusted=yes is scoped to synthetic, loopback-only test packages.
            # No host sources or security settings are loaded or modified.
            source = f'deb [trusted=yes] http://127.0.0.1:{server.server_port}/ ./\n'
            (root / 'etc/sources.list').write_text(source)
            entries = {
                'Dir::Etc::main': str(root / 'etc/apt.conf'),
                'Dir::Etc::parts': str(root / 'etc/parts'),
                'Dir::Etc::sourcelist': str(root / 'etc/sources.list'),
                'Dir::Etc::sourceparts': str(root / 'etc/sources.list.d'),
                'Dir::Etc::preferences': str(root / 'etc/preferences'),
                'Dir::Etc::preferencesparts': str(root / 'etc/preferences.d'),
                'Dir::State': str(root / 'state'),
                'Dir::State::status': str(root / 'state/status'),
                'Dir::State::lists': str(root / 'state/lists'),
                'Dir::State::extended_states': str(root / 'state/extended_states'),
                'Dir::Cache': str(root / 'cache'),
                'Dir::Cache::archives': str(root / 'archives'),
                'Dir::Cache::pkgcache': str(root / 'pkgcache.bin'),
                'Dir::Cache::srcpkgcache': str(root / 'srcpkgcache.bin'),
                'Dir::Log': str(root / 'logs'),
                'Dir::Bin::dpkg': str(guard),
                'APT::Architecture': 'amd64',
                'Debug::NoLocking': 'true',
                'Acquire::http::Proxy::127.0.0.1': 'DIRECT',
                'Acquire::Retries': '0',
                'Acquire::http::Timeout': '3',
                'Acquire::Languages': 'none',
                'APT::Update::Error-Mode': 'any',
                'APT::Get::List-Cleanup': 'false',
            }
            configuration = root / 'isolated.conf'
            configuration.write_text('\n'.join(f'{key} "{value}";' for key, value in entries.items()) + '\n')
            environment = dict(os.environ, APT_CONFIG=str(configuration), LC_ALL='C', no_proxy='127.0.0.1,localhost')

            def apt(arguments):
                return subprocess.run(['apt-get', *arguments], env=environment, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)

            def recorded_installs():
                if not calls_path.exists():
                    return []
                return [json.loads(line) for line in calls_path.read_text().splitlines()
                        if '--unpack' in json.loads(line)]

            updated = apt(['update'])
            assert updated.returncode == 0, updated.stdout
            downloaded = apt(['--download-only', '--no-remove', '-y', 'install', str(local_deb)])
            assert downloaded.returncode == 0, downloaded.stdout
            cached = root / 'archives' / dependency.name
            assert cached.is_file() and cached.read_bytes() == dependency.read_bytes(), downloaded.stdout
            assert not recorded_installs(), 'Download-only invoked an installation.'
            download_count = sum(path.endswith(dependency.name) for path in requests)
            assert download_count == 1, requests
            print('PASS download-only: repository dependency cached once; no dpkg install')

            old = apt(['--no-download', '--no-remove', '-y', 'install', str(local_deb)])
            if "Pathname to install is not absolute 'veliqen-apt-fixture_1.0_all.deb'" in old.stdout:
                assert not recorded_installs(), old.stdout
                print('REPRODUCED --no-download: local absolute .deb becomes a relative pathname inside APT')
            else:
                # A future APT version may fix the old behaviour upstream.
                assert recorded_installs() and 'QA_DPKG_GUARD' in old.stdout, old.stdout
                print('INFO this APT already resolves --no-download local paths; guard still prevented installation')
            calls_path.write_text('')

            fixed = apt(['--no-remove', '-y', 'install', str(local_deb)])
            assert 'Pathname to install is not absolute' not in fixed.stdout, fixed.stdout
            assert 'QA_DPKG_GUARD' in fixed.stdout and fixed.returncode != 0, fixed.stdout
            calls = recorded_installs()
            assert calls, fixed.stdout
            arguments = [argument for call in calls for argument in call]
            assert str(local_deb) in arguments, calls
            package_arguments = [argument for argument in arguments if argument.endswith('.deb')]
            assert package_arguments and all(Path(path).is_absolute() for path in package_arguments), calls
            assert sum(path.endswith(dependency.name) for path in requests) == download_count, requests
            assert (root / 'state/status').read_text() == ''
            print('PASS corrected install: absolute local .deb reaches dpkg guard; cached dependency not downloaded again')
            print('PASS safety: guard refused actual install; temporary dpkg status stayed empty; no sudo or system mutation')
            print(subprocess.check_output(['apt-get', '--version'], text=True).splitlines()[0])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    main()
