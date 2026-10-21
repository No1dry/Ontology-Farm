import ssl


def test_fuseki_reuses_verified_context_without_disabling_hostname_checks():
    from lzo.semantic import fuseki
    assert hasattr(fuseki, "_verified_context"), "Repeated Fuseki requests recreate the trust context"
    first = fuseki._verified_context()
    assert fuseki._verified_context() is first
    assert first.verify_mode == ssl.CERT_REQUIRED and first.check_hostname
    assert first.cert_store_stats()["x509_ca"] > 0
