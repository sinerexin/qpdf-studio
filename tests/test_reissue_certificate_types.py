"""Synthetic reissue coverage for CA-issued, CA-capable and extensionless inputs."""
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from pdf_studio.keygen import generate_personal_credentials, reissue_personal_certificate


@pytest.mark.parametrize(('external_issuer', 'ca_flag'), [(True, False), (False, True), (True, True), (True, None)])
def test_reissue_accepts_certificate_types_and_preserves_source(tmp_path, external_issuer, ca_flag):
    source, target = tmp_path / 'existing', tmp_path / 'new'
    password = 'Synthetic local test password'
    generate_personal_credentials(source, label='Original', password=password)
    key = serialization.load_pem_private_key((source / 'private-key-encrypted.pem').read_bytes(),
                                             password=password.encode())
    subject = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Input without common name')])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic issuer')]) if external_issuer else subject
    issuer_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if external_issuer else key
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30)))
    if ca_flag is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca_flag, path_length=None), critical=True)
    original_cert = builder.sign(issuer_key, hashes.SHA256())
    (source / 'self-signed-certificate.pem').write_bytes(original_cert.public_bytes(serialization.Encoding.PEM))
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    result = reissue_personal_certificate(source, target, label='New certificate', password=password)
    assert result['key_reused']
    assert all(p.read_bytes() == before[p.name] for p in source.iterdir())
    new_cert = x509.load_pem_x509_certificate((target / 'self-signed-certificate.pem').read_bytes())
    assert new_cert.subject == new_cert.issuer
    assert new_cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == 'New certificate'
    assert not new_cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert new_cert.public_key().public_numbers() == original_cert.public_key().public_numbers()
