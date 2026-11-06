"""Tests for Workspace Editor backend APIs (BETA-04 functional)."""

import os
import pytest

from fastapi.testclient import TestClient

from server import app


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# FS bridge
# ---------------------------------------------------------------------------
class TestFsHome:
    def test_returns_home_directory(self, client):
        resp = client.get("/api/fs/home")
        assert resp.status_code == 200
        data = resp.json()
        assert "home" in data
        assert data["home"] == os.path.expanduser("~")


class TestFsList:
    def test_list_home_defaults_to_user_home(self, client):
        resp = client.get("/api/fs/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == os.path.expanduser("~")
        assert "items" in data
        assert isinstance(data["items"], list)

    def test_list_tmp(self, client):
        resp = client.get("/api/fs/list", params={"path": "/tmp"})
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "truncated" in data

    def test_list_volumes_special_case(self, client):
        resp = client.get("/api/fs/list", params={"path": "/Volumes"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "/Volumes"
        assert isinstance(data["items"], list)
        # T7 should be filtered out
        names = [i["name"] for i in data["items"]]
        assert "T7" not in names

    def test_list_denied_path_t7(self, client):
        resp = client.get("/api/fs/list", params={"path": "/Volumes/T7"})
        assert resp.status_code == 403

    def test_list_nonexistent_dir(self, client):
        resp = client.get("/api/fs/list", params={"path": "/tmp/does_not_exist_xyz"})
        assert resp.status_code == 404


class TestFsReadWrite:
    def test_write_read_roundtrip(self, client, tmp_path):
        test_file = str(tmp_path / "test.txt")
        # Write
        resp = client.post(
            "/api/fs/write", json={"path": test_file, "content": "hello kernel"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["bytes"] > 0
        # Read
        resp = client.get("/api/fs/read", params={"path": test_file})
        assert resp.status_code == 200
        assert resp.json()["content"] == "hello kernel"
        # Cleanup
        client.delete("/api/fs/remove", params={"path": test_file})

    def test_read_missing_path_returns_400(self, client):
        resp = client.get("/api/fs/read")
        assert resp.status_code == 400

    def test_read_nonexistent_file(self, client):
        resp = client.get(
            "/api/fs/read", params={"path": "/tmp/no_such_file_abc123.txt"}
        )
        assert resp.status_code == 404


class TestFsMkdir:
    def test_mkdir_creates_directory(self, client, tmp_path):
        test_dir = str(tmp_path / "newdir")
        resp = client.post("/api/fs/mkdir", json={"path": test_dir})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert os.path.isdir(test_dir)
        os.rmdir(test_dir)


class TestFsRename:
    def test_rename_moves_file(self, client, tmp_path):
        src = str(tmp_path / "a.txt")
        dst = str(tmp_path / "b.txt")
        with open(src, "w") as f:
            f.write("data")
        resp = client.post("/api/fs/rename", json={"from": src, "to": dst})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not os.path.exists(src)
        assert os.path.exists(dst)


class TestFsRemove:
    def test_remove_file(self, client, tmp_path):
        f = str(tmp_path / "del.txt")
        with open(f, "w") as fh:
            fh.write("bye")
        resp = client.delete("/api/fs/remove", params={"path": f})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not os.path.exists(f)

    def test_remove_directory(self, client, tmp_path):
        d = str(tmp_path / "deldir")
        os.makedirs(d)
        resp = client.delete("/api/fs/remove", params={"path": d})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not os.path.exists(d)


class TestFsStat:
    def test_stat_returns_file_info(self, client, tmp_path):
        f = str(tmp_path / "statme.txt")
        with open(f, "w") as fh:
            fh.write("stat")
        resp = client.get("/api/fs/stat", params={"path": f})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "file"
        assert data["size"] == 4


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------
class TestVault:
    def test_vault_list_returns_ids(self, client):
        resp = client.get("/api/vault/list")
        assert resp.status_code == 200
        data = resp.json()
        assert "ids" in data
        assert isinstance(data["ids"], list)

    def test_vault_set_and_delete(self, client):
        # Set
        resp = client.post(
            "/api/vault/set",
            json={"id": "test-e2e-key", "key": "sk-test-placeholder-value"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify appears in list
        resp = client.get("/api/vault/list")
        assert "test-e2e-key" in resp.json()["ids"]
        # Delete
        resp = client.delete("/api/vault/delete", params={"id": "test-e2e-key"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify gone
        resp = client.get("/api/vault/list")
        assert "test-e2e-key" not in resp.json()["ids"]

    def test_vault_invalid_id_rejected(self, client):
        resp = client.post("/api/vault/set", json={"id": "../bad", "key": "x"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# LLM proxy
# ---------------------------------------------------------------------------
class TestLlmProxy:
    def test_llm_test_missing_key(self, client):
        resp = client.post(
            "/api/llm/test",
            json={
                "provider": {
                    "id": "bogus",
                    "type": "openai",
                    "baseUrl": "https://api.openai.com/v1",
                    "model": "gpt-4",
                }
            },
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "key" in resp.json()["error"].lower()

    def test_llm_test_missing_provider(self, client):
        resp = client.post("/api/llm/test", json={})
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_llm_stream_missing_body(self, client):
        resp = client.post("/api/llm/stream", json={})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Git proxy
# ---------------------------------------------------------------------------
class TestGitProxy:
    def test_git_proxy_blocks_bad_protocol(self, client):
        resp = client.get("/api/git-proxy/ftp://evil.com/repo")
        assert resp.status_code == 400

    def test_git_proxy_rejects_local_paths(self, client):
        """Ensure we can't proxy to file:// or other non-http schemes."""
        resp = client.get("/api/git-proxy/file:///etc/passwd")
        assert resp.status_code in (400, 422)

    def test_git_proxy_blocks_ipv4_loopback(self, client):
        resp = client.get("/api/git-proxy/http://127.0.0.1/api/health")
        assert resp.status_code == 403

    def test_git_proxy_blocks_ipv6_loopback(self, client):
        resp = client.get("/api/git-proxy/http://[::1]:11527/api/health")
        assert resp.status_code == 403

    def test_git_proxy_blocks_ipv6_mapped_loopback(self, client):
        """IPv4-mapped IPv6 (::ffff:127.0.0.1) must not bypass the block."""
        resp = client.get("/api/git-proxy/http://[::ffff:127.0.0.1]:11527/api/health")
        assert resp.status_code == 403

    def test_git_proxy_blocks_private_ipv4(self, client):
        resp = client.get("/api/git-proxy/http://10.0.0.1/repo")
        assert resp.status_code == 403

    def test_git_proxy_blocks_ipv6_private(self, client):
        resp = client.get("/api/git-proxy/http://[fc00::1]/repo")
        assert resp.status_code == 403
