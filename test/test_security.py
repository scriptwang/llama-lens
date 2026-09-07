from backend.ctl.security import (create_token, decode_token, decrypt_secret,
                                 encrypt_secret, hash_password, verify_password)


def test_password_hash_verify():
    h = hash_password("s3cret")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
    assert not verify_password("s3cret", "garbage")
    assert hash_password("s3cret") != h  # 盐随机


def test_fernet_roundtrip():
    tok = encrypt_secret("top secret")
    assert tok != "top secret"
    assert decrypt_secret(tok) == "top secret"


def test_jwt_roundtrip():
    t = create_token("admin")
    assert decode_token(t)["sub"] == "admin"
