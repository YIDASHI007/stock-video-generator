from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from stock_video_generator.config import Settings
from stock_video_generator.database import Database, PublishAccountRecord
from stock_video_generator.main import create_app
from stock_video_generator.publishing import PublishAccountCreate, PublishingService


def _settings(tmp_path: Path) -> Settings:
    settings = Settings(
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        node_executable="missing-node",
    )
    settings.ensure_directories()
    return settings


def test_existing_douyin_account_schema_is_upgraded_without_losing_data(tmp_path):
    settings = _settings(tmp_path)
    database_path = settings.data_dir / "database" / "stock_video.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE publish_accounts (
                account_id VARCHAR(64) PRIMARY KEY,
                display_name VARCHAR(120) NOT NULL,
                browser_profile_dir TEXT NOT NULL,
                enabled BOOLEAN NOT NULL,
                auto_publish_enabled BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                last_login_at DATETIME NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO publish_accounts (
                account_id, display_name, browser_profile_dir, enabled,
                auto_publish_enabled, created_at, updated_at, last_login_at
            ) VALUES (?, ?, ?, 1, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
            """,
            ("douyin-main", "原抖音账号", str(tmp_path / "profile")),
        )

    database = Database(settings)
    database.initialize()

    columns = {item["name"] for item in inspect(database.engine).get_columns("publish_accounts")}
    assert {"platform", "auth_status", "last_checked_at"}.issubset(columns)
    with database.session() as session:
        account = session.scalar(
            select(PublishAccountRecord).where(
                PublishAccountRecord.account_id == "douyin-main"
            )
        )
    assert account is not None
    assert account.platform == "douyin"
    assert account.display_name == "原抖音账号"


def test_accounts_api_supports_three_platforms_and_safe_unbind(tmp_path):
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        for platform, account_id, name in (
            ("douyin", "dy-main", "抖音主账号"),
            ("xiaohongshu", "xhs-main", "小红书主账号"),
            ("wechat_channels", "wx-main", "视频号主账号"),
        ):
            response = client.post(
                "/api/accounts",
                json={
                    "account_id": account_id,
                    "platform": platform,
                    "display_name": name,
                },
            )
            assert response.status_code == 201
            assert response.json()["platform"] == platform

        accounts = client.get("/api/accounts")
        assert accounts.status_code == 200
        assert {item["platform"] for item in accounts.json()} == {
            "douyin",
            "xiaohongshu",
            "wechat_channels",
        }
        publish_accounts = client.get("/api/publish/accounts")
        assert [item["account_id"] for item in publish_accounts.json()] == ["dy-main"]

        profile = (
            settings.data_dir / "publish-accounts" / "xhs-main" / "chrome-profile"
        )
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_text("session", encoding="utf-8")
        unbound = client.post("/api/accounts/xhs-main/unbind")
        assert unbound.status_code == 200
        assert unbound.json()["enabled"] is False
        assert unbound.json()["auth_status"] == "logged_out"
        assert not profile.exists()

        deleted = client.delete("/api/accounts/xhs-main")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "account_id": "xhs-main"}
        assert all(
            item["account_id"] != "xhs-main"
            for item in client.get("/api/accounts").json()
        )


def test_connected_account_must_be_unbound_before_deletion(tmp_path):
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/accounts",
            json={
                "account_id": "xhs-connected",
                "platform": "xiaohongshu",
                "display_name": "已连接账号",
            },
        )
        assert created.status_code == 201

        blocked = client.delete("/api/accounts/xhs-connected")
        assert blocked.status_code == 409
        assert "先解绑" in blocked.json()["detail"]


def test_account_identifier_cannot_be_reassigned_to_another_platform(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    service = PublishingService(settings, database)
    service.save_account(
        PublishAccountCreate(
            account_id="shared-id",
            platform="douyin",
            display_name="抖音账号",
        )
    )

    with pytest.raises(ValueError, match="不能切换"):
        service.save_account(
            PublishAccountCreate(
                account_id="shared-id",
                platform="xiaohongshu",
                display_name="小红书账号",
            )
        )


def test_login_qr_is_served_locally_without_exposing_file_path(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        created = client.post(
            "/api/accounts",
            json={
                "account_id": "qr-account",
                "platform": "douyin",
                "display_name": "二维码测试账号",
            },
        )
        assert created.status_code == 201

        qr_path = (
            settings.data_dir
            / "publish-accounts"
            / "qr-account"
            / "login-evidence"
            / "login-qrcode.png"
        )
        qr_path.parent.mkdir(parents=True, exist_ok=True)
        qr_path.write_bytes(b"\x89PNG\r\n\x1a\nlocal-test")
        app.state.publish_manager._login_states["qr-account"] = {
            "account_id": "qr-account",
            "status": "waiting_scan",
            "message": "请扫码",
            "qr_code_path": str(qr_path),
            "qr_code_url": "/api/accounts/qr-account/login/qr",
            "qr_revision": 1,
        }

        status_response = client.get("/api/accounts/qr-account/login")
        assert status_response.status_code == 200
        assert status_response.json()["qr_code_url"].endswith("/login/qr")
        assert "qr_code_path" not in status_response.json()

        qr_response = client.get("/api/accounts/qr-account/login/qr")
        assert qr_response.status_code == 200
        assert qr_response.headers["cache-control"] == "no-store, max-age=0"
        assert qr_response.content.startswith(b"\x89PNG")

        cancelled = client.post("/api/accounts/qr-account/login/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert client.get("/api/accounts/qr-account/login/qr").status_code == 409
