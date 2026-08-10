"""Unit tests for SSRF protection (spec §23)."""

import socket

import pytest
from backend.core.ssrf import UnsafeUrlError, _ip_is_safe, check_url


def _fake_getaddrinfo(addresses):
    """Return a getaddrinfo replacement that yields a fixed set of IPs."""

    def fake(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]

    return fake


class TestIpIsSafe:
    def test_public_ipv4(self):
        assert _ip_is_safe("8.8.8.8") is True
        assert _ip_is_safe("93.184.216.34") is True

    def test_private_ipv4(self):
        assert _ip_is_safe("10.0.0.1") is False
        assert _ip_is_safe("172.16.0.1") is False
        assert _ip_is_safe("192.168.1.1") is False
        assert _ip_is_safe("127.0.0.1") is False

    def test_cloud_metadata(self):
        assert _ip_is_safe("169.254.169.254") is False

    def test_ipv6_loopback_and_private(self):
        assert _ip_is_safe("::1") is False
        assert _ip_is_safe("fd00::1") is False


class TestCheckUrl:
    def test_blocks_private_resolution(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["10.0.0.5"]))
        result = check_url("http://evil.example.com/feed")
        assert result.safe is False
        assert "not public internet" in result.reason

    def test_blocks_metadata_endpoint(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["169.254.169.254"]))
        assert check_url("http://metadata.example/").safe is False

    def test_blocks_non_http_scheme(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["8.8.8.8"]))
        assert check_url("file:///etc/passwd").safe is False
        assert check_url("ftp://example.com/x").safe is False

    def test_blocks_blocked_hostname(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["8.8.8.8"]))
        assert check_url("http://localhost/feed").safe is False
        assert check_url("http://metadata.google.internal/").safe is False

    def test_allows_public_host(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
        assert check_url("https://example.com/feed").safe is True

    def test_raises_on_fetch_of_unsafe_url(self, monkeypatch):
        from backend.modules.ingestion.fetcher import fetch_url

        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["127.0.0.1"]))
        with pytest.raises(UnsafeUrlError):
            fetch_url("http://localhost:9000/x")
