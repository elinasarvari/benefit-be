from packages.platform.auth.passwords import (
    DUMMY_HASH,
    hash_password,
    needs_rehash,
    verify_password,
)


def test_FR_000_21_hash_is_argon2id_and_verifies() -> None:
    hashed = hash_password("a-demo-password")

    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "a-demo-password")
    assert not verify_password(hashed, "wrong-password")
    assert not needs_rehash(hashed)


def test_FR_000_21_same_password_gets_a_different_salted_hash() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_dummy_hash_never_verifies_a_real_password() -> None:
    assert not verify_password(DUMMY_HASH, "anything")
