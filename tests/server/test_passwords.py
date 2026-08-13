from cas_server.security.passwords import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    hashed = hash_password("s3cr3t!")
    assert hashed != "s3cr3t!"
    assert verify_password("s3cr3t!", hashed)


def test_verify_rejects_wrong_password():
    hashed = hash_password("s3cr3t!")
    assert not verify_password("wrong", hashed)
