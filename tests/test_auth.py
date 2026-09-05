"""auth 密码校验单元测试：哈希格式 / 恒定时间比对 / 配置解析。"""
import pytest

from auth import hash_password, load_password_hash, verify_password


def test_hash_and_verify_ok():
    encoded = hash_password("s3cret")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret", encoded)


def test_verify_wrong_password():
    encoded = hash_password("right")
    assert not verify_password("wrong", encoded)


def test_verify_malformed_returns_false():
    assert not verify_password("x", "")
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "md5$1$salt$abc")
    assert not verify_password("x", "pbkdf2_sha256$0$salt$abc")  # 迭代次数非法
    assert not verify_password("x", "pbkdf2_sha256$1000$$abc")   # 空盐
    assert not verify_password("x", "pbkdf2_sha256$1000$salt$")  # 空哈希


def test_hash_rejects_empty():
    with pytest.raises(ValueError):
        hash_password("")


def test_load_hash_priority(monkeypatch):
    """HASH 优先级高于明文，二者同设时取 HASH。"""
    monkeypatch.setenv("FINLEDGER_PASSWORD_HASH", "pbkdf2_sha256$1$salt$deadbeef")
    monkeypatch.setenv("FINLEDGER_PASSWORD", "plain")
    assert load_password_hash() == "pbkdf2_sha256$1$salt$deadbeef"


def test_load_plain_warns(monkeypatch, capsys):
    """明文模式：派生为哈希并明确警告，明文不再参与后续比对。"""
    monkeypatch.delenv("FINLEDGER_PASSWORD_HASH", raising=False)
    monkeypatch.setenv("FINLEDGER_PASSWORD", "devpass")
    result = load_password_hash()
    assert result.startswith("pbkdf2_sha256$")
    err = capsys.readouterr().err
    assert "警告" in err and "FINLEDGER_PASSWORD" in err
    assert verify_password("devpass", result)


def test_load_none_rejects(monkeypatch):
    """两者都未设置 → 拒绝启动。"""
    monkeypatch.delenv("FINLEDGER_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("FINLEDGER_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="拒绝启动"):
        load_password_hash()
