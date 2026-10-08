"""Focused end-to-end tests for browser account registration and sessions."""
import os
import shutil
import sqlite3
import tempfile

from fastapi.testclient import TestClient


with tempfile.TemporaryDirectory() as data_dir:
    shutil.copytree("policies", os.path.join(data_dir, "policies"))
    shutil.copytree("skills", os.path.join(data_dir, "skills"))
    os.environ.update(
        POLICY_DIR=os.path.join(data_dir, "policies"),
        SKILLS_DIR=os.path.join(data_dir, "skills"),
        RUNS_FILE=os.path.join(data_dir, "runs.jsonl"),
        EVENTS_FILE=os.path.join(data_dir, "events.jsonl"),
        SCHEDULES_FILE=os.path.join(data_dir, "schedules.json"),
        LANCEDB_URI=os.path.join(data_dir, "lance"),
        GRAPH_FILE=os.path.join(data_dir, "graph.json"),
        FILES_REGISTRY=os.path.join(data_dir, "files.json"),
        CFO_AUTH_DATABASE_PATH=os.path.join(data_dir, "auth.sqlite3"),
        CFO_API_KEYS="",
        CFO_CORS_ORIGINS="http://localhost:5173",
        AUTH_COOKIE_SECURE="0",
        VECTOR_BACKEND="memory",
        SCHEDULER_ENABLED="0",
    )

    import main

    sqlalchemy_url = main.accounts._sqlalchemy_url(
        "Server=db.example,1433;Database=cfo;UID=unit-user;PWD=p@ss;"
        "Encrypt=yes;TrustServerCertificate=no"
    )
    assert sqlalchemy_url.startswith("mssql+pyodbc://unit-user:p%40ss@db.example,1433/cfo?")
    assert "Encrypt=yes" in sqlalchemy_url and "TrustServerCertificate=no" in sqlalchemy_url

    with TestClient(main.app) as first_browser:
        assert first_browser.get("/api/v1/agents").status_code == 401
        assert first_browser.post("/api/v1/auth/register", headers={
            "Origin": "https://untrusted.example",
        }, json={
            "name": "Blocked User",
            "email": "blocked@example.com",
            "password": "a-long-secure-password",
        }).status_code == 403
        assert first_browser.post("/api/v1/auth/register", json={
            "name": "Too Short",
            "email": "short@example.com",
            "password": "short",
        }).status_code == 422

        created = first_browser.post("/api/v1/auth/register", json={
            "name": "Shared Account",
            "email": "Shared@Example.com",
            "password": "a-long-secure-password",
        })
        assert created.status_code == 200, created.text
        assert created.json() == {
            "id": 1,
            "name": "Shared Account",
            "email": "shared@example.com",
            "role": "Controller",
            "initials": "SA",
        }
        cookie = created.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=43200" in cookie
        session_token = first_browser.cookies.get("cfo_session")
        database = sqlite3.connect(os.environ["CFO_AUTH_DATABASE_PATH"])
        try:
            stored_hash = database.execute("SELECT password_hash FROM CFOAgentUsers").fetchone()[0]
            stored_session = database.execute("SELECT token_hash FROM CFOAgentSessions").fetchone()[0]
        finally:
            database.close()
        assert stored_hash.startswith("pbkdf2_sha256$") and "a-long-secure-password" not in stored_hash
        assert stored_session != session_token
        assert first_browser.get("/api/v1/agents").status_code == 200

        assert first_browser.post("/api/v1/auth/register", json={
            "name": "Duplicate",
            "email": "shared@example.com",
            "password": "a-different-secure-password",
        }).status_code == 409
        assert first_browser.post("/api/v1/auth/logout").status_code == 200
        assert first_browser.get("/api/v1/agents").status_code == 401

    with TestClient(main.app) as second_browser:
        wrong_password = second_browser.post("/api/v1/auth/login", json={
            "email": "shared@example.com",
            "password": "wrong-password",
        })
        assert wrong_password.status_code == 401
        assert wrong_password.json()["detail"] == "Invalid email or password"

        login = second_browser.post("/api/v1/auth/login", json={
            "email": "SHARED@example.com",
            "password": "a-long-secure-password",
        })
        assert login.status_code == 200 and login.json()["email"] == "shared@example.com"
        assert second_browser.get("/api/v1/auth/me").json()["name"] == "Shared Account"
        assert second_browser.get("/api/v1/agents").status_code == 200
        assert second_browser.post("/api/v1/auth/logout").status_code == 200
        assert second_browser.get("/api/v1/auth/me").status_code == 401

    main.accounts._engine.dispose()

print("authentication tests passed")
