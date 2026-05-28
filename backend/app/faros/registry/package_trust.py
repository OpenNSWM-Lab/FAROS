import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Dict

from app.faros.errors import FarosTrustError

ALLOWED_TRUST_LEVELS = {'verified', 'internal', 'community'}
ALLOWED_INTEGRITY_ALGORITHMS = {'sha256'}
ALLOWED_SIGNATURE_ALGORITHMS = {'hmac-sha256'}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _trust_keyring_path() -> Path:
    return Path(__file__).resolve().parents[3] / 'data' / 'faros' / 'packages' / 'trust_keyring.json'


def _load_trust_keyring() -> Dict[str, Any]:
    path = _trust_keyring_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _signature_payload(payload: Dict[str, Any]) -> bytes:
    canonical = dict(payload)
    canonical.pop('signature', None)
    return json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _verify_integrity(package_dir: Path, payload: Dict[str, Any], report: Dict[str, Any]) -> None:
    integrity = payload.get('integrity')
    if integrity is None:
        report['integrityStatus'] = 'absent'
        return
    if not isinstance(integrity, dict):
        report['reasons'].append('integrity must be an object when provided')
        report['integrityStatus'] = 'invalid'
        return

    algorithm = integrity.get('algorithm', 'sha256')
    files = integrity.get('files')
    report['integrityAlgorithm'] = algorithm
    if algorithm not in ALLOWED_INTEGRITY_ALGORITHMS:
        report['reasons'].append(f"integrity.algorithm must be one of {sorted(ALLOWED_INTEGRITY_ALGORITHMS)}")
        report['integrityStatus'] = 'invalid'
        return
    if not isinstance(files, dict) or not files:
        report['reasons'].append('integrity.files must be a non-empty object')
        report['integrityStatus'] = 'invalid'
        return

    mismatches = []
    missing = []
    checked = []
    for rel_path, expected in files.items():
        if not isinstance(rel_path, str) or not rel_path.strip():
            report['reasons'].append('integrity file paths must be non-empty strings')
            report['integrityStatus'] = 'invalid'
            return
        if not isinstance(expected, str) or not expected.strip():
            report['reasons'].append(f"integrity checksum for '{rel_path}' must be a non-empty string")
            report['integrityStatus'] = 'invalid'
            return
        full_path = (package_dir / rel_path).resolve()
        try:
            full_path.relative_to(package_dir.resolve())
        except ValueError:
            report['reasons'].append(f"integrity path '{rel_path}' escapes package root")
            report['integrityStatus'] = 'invalid'
            return
        if not full_path.is_file():
            missing.append(rel_path)
            continue
        actual = _sha256_file(full_path)
        checked.append(rel_path)
        if actual != expected:
            mismatches.append({'path': rel_path, 'expected': expected, 'actual': actual})

    report['integrityCheckedFiles'] = sorted(checked)
    report['integrityMissingFiles'] = sorted(missing)
    report['integrityMismatches'] = mismatches
    if missing:
        report['reasons'].append(f"integrity files missing: {sorted(missing)}")
    if mismatches:
        report['reasons'].append(f"integrity checksum mismatches: {[item['path'] for item in mismatches]}")
    report['integrityStatus'] = 'verified' if not missing and not mismatches else 'invalid'


def _verify_signature(payload: Dict[str, Any], report: Dict[str, Any]) -> None:
    signature = payload.get('signature')
    if signature is None:
        report['signatureStatus'] = 'absent'
        return
    if not isinstance(signature, dict):
        report['reasons'].append('signature must be an object when provided')
        report['signatureStatus'] = 'invalid'
        return

    algorithm = signature.get('algorithm', 'hmac-sha256')
    signer = signature.get('signedBy') or payload.get('signedBy')
    value = signature.get('value')
    keyring = _load_trust_keyring()

    report['signatureAlgorithm'] = algorithm
    report['signatureSigner'] = signer
    report['keyringPath'] = str(_trust_keyring_path())

    if algorithm not in ALLOWED_SIGNATURE_ALGORITHMS:
        report['reasons'].append(f"signature.algorithm must be one of {sorted(ALLOWED_SIGNATURE_ALGORITHMS)}")
        report['signatureStatus'] = 'invalid'
        return
    if not isinstance(signer, str) or not signer.strip():
        report['reasons'].append('signature.signedBy is required')
        report['signatureStatus'] = 'invalid'
        return
    if not isinstance(value, str) or not value.strip():
        report['reasons'].append('signature.value is required')
        report['signatureStatus'] = 'invalid'
        return
    if signer not in keyring:
        report['reasons'].append(f"signer '{signer}' is not present in trust keyring")
        report['signatureStatus'] = 'invalid'
        return

    signer_entry = keyring[signer]
    secret = signer_entry.get('secret')
    if not isinstance(secret, str) or not secret:
        report['reasons'].append(f"signer '{signer}' has no secret in trust keyring")
        report['signatureStatus'] = 'invalid'
        return

    expected = hmac.new(secret.encode('utf-8'), _signature_payload(payload), hashlib.sha256).hexdigest()
    report['keyringMatched'] = True
    if not hmac.compare_digest(expected, value):
        report['reasons'].append(f"signature mismatch for signer '{signer}'")
        report['signatureStatus'] = 'invalid'
        return

    report['signatureStatus'] = 'verified'


def inspect_package_trust(package_type: str, package_id: str, package_dir: Path, version: str | None = None) -> Dict[str, Any]:
    package_dir = package_dir.resolve()
    trust_path = package_dir / 'TRUST.json'
    report: Dict[str, Any] = {
        'packageType': package_type,
        'packageId': package_id,
        'version': version,
        'packagePath': str(package_dir),
        'hasManifest': trust_path.is_file(),
        'trusted': False,
        'trustLevel': 'untrusted',
        'publisher': None,
        'source': None,
        'signedBy': None,
        'policyStatus': 'untrusted',
        'reasons': [],
        'integrityStatus': 'absent',
        'integrityAlgorithm': None,
        'integrityCheckedFiles': [],
        'integrityMissingFiles': [],
        'integrityMismatches': [],
        'signatureStatus': 'absent',
        'signatureAlgorithm': None,
        'signatureSigner': None,
        'keyringMatched': False,
        'keyringPath': str(_trust_keyring_path()),
    }
    if not trust_path.is_file():
        report['reasons'].append('missing TRUST.json')
        return report

    payload = json.loads(trust_path.read_text())
    publisher = payload.get('publisher')
    source = payload.get('source')
    trust_level = payload.get('trustLevel')
    signed_by = payload.get('signedBy')

    report.update({
        'publisher': publisher,
        'source': source,
        'signedBy': signed_by,
        'trustLevel': trust_level or 'untrusted',
    })

    if not isinstance(publisher, str) or not publisher.strip():
        report['reasons'].append('publisher is required')
    if not isinstance(source, str) or not source.strip():
        report['reasons'].append('source is required')
    if trust_level not in ALLOWED_TRUST_LEVELS:
        report['reasons'].append(f"trustLevel must be one of {sorted(ALLOWED_TRUST_LEVELS)}")

    _verify_integrity(package_dir, payload, report)
    _verify_signature(payload, report)

    if report['reasons']:
        report['policyStatus'] = 'invalid'
        return report

    if trust_level in {'verified', 'internal'}:
        report['trusted'] = True
        report['policyStatus'] = 'trusted'
    else:
        report['reasons'].append(f"trustLevel '{trust_level}' is informational only and not trusted by policy")
    return report


def enforce_trust_policy(report: Dict[str, Any], allow_untrusted: bool = True) -> None:
    if report.get('trusted') or allow_untrusted:
        return
    reasons = '; '.join(report.get('reasons', [])) or 'package is not trusted'
    raise FarosTrustError(
        f"{report['packageType'].capitalize()} '{report['packageId']}' is not trusted: {reasons}. "
        'Pass allowUntrusted=true to override.'
    )
