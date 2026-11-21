#!/usr/bin/env python3
"""Validate the distributable APK's identity, release certificate and manifest."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('apk', type=Path)
parser.add_argument('--sdk', type=Path, default=Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home()/'Android/Sdk'))
parser.add_argument('--report', type=Path)
args = parser.parse_args()
build_tools = args.sdk/'build-tools/37.0.0'
expected = json.loads((ROOT/'RELEASE-SIGNING.json').read_text())

def run(name, *arguments):
    return subprocess.check_output([str(build_tools/name), *map(str, arguments)], text=True, stderr=subprocess.STDOUT)

def require(condition, message):
    if not condition:
        raise ValueError(message)

try:
    signature = run('apksigner', 'verify', '--verbose', '--print-certs', args.apk)
    require(re.search(r'Number of signers: 1(?:\s|$)', signature), 'Release APK must have exactly one signer')
    for version in (1, 2, 3):
        require(re.search(rf'Verified using v{version} scheme .*: true', signature), f'APK signature v{version} failed')
    certificate = re.search(r'certificate SHA-256 digest: ([0-9a-f]{64})', signature)
    require(certificate is not None and certificate.group(1) == expected['certificate_sha256'], 'APK signer does not match the permanent VELIQEN release certificate')
    run('zipalign', '-c', '-P', '16', '4', args.apk)
    badging = run('aapt2', 'dump', 'badging', args.apk)
    require("name='tech.veliqen.link' versionCode='13706' versionName='1.37.3-veliqen-link-1.2.1'" in badging, 'Unexpected application ID or release version')
    require("launchable-activity: name='org.kde.kdeconnect.ui.VeliqenHomeActivity'" in badging, 'Unexpected launcher activity')
    require('application-debuggable' not in badging, 'Release APK must not be debuggable')
    manifest = run('aapt2', 'dump', 'xmltree', '--file', 'AndroidManifest.xml', args.apk)
    for property_name in ('debuggable', 'testOnly'):
        for line in manifest.splitlines():
            if re.search(r'A: (?:android|http://schemas.android.com/apk/res/android):'+property_name+r'\(', line):
                require(re.search(r'=\(type 0x12\)0x0(?:\s|$)', line) is not None, f'Release manifest has {property_name}=true')
    require('"tech.veliqen.link.fileprovider"' in manifest, 'FileProvider authority does not match the application ID')
    require('"org.kde.kdeconnect_tp.fileprovider"' not in manifest, 'Legacy FileProvider authority present')
    require('android.permission.READ_LOGS' not in badging, 'READ_LOGS is not part of this release')
    result = {
        'status': 'passed', 'apk': args.apk.name,
        'apk_sha256': hashlib.sha256(args.apk.read_bytes()).hexdigest(),
        'apk_size_bytes': args.apk.stat().st_size,
        'application_id': 'tech.veliqen.link', 'version_code': 13706,
        'version_name': '1.37.3-veliqen-link-1.2.1',
        'certificate_sha256': certificate.group(1),
        'signature_v1_v2_v3_verified': True, 'zipalign_4byte_16KiB_pass': True,
        'debuggable': False, 'test_only': False,
        'fileprovider_authority': 'tech.veliqen.link.fileprovider',
        'physical_device_tested': False,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
except (ValueError, OSError, subprocess.CalledProcessError) as error:
    print(f'FAIL: {error}', file=sys.stderr)
    sys.exit(1)
