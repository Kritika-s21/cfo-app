"""Persistent account and session storage for browser authentication."""
import hashlib
import hmac
import os
import re
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import quote_plus, urlencode

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError


PASSWORD_ITERATIONS = 600_000
SESSION_TTL_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_FAILED_LOGINS = 10
_DUMMY_PASSWORD_HASH = f"pbkdf2_sha256${PASSWORD_ITERATIONS}${'00' * 16}${'00' * 32}"
_SQL_CONNECTION_STRING = (
    os.environ.get("CFO_AUTH_SQL_CONNECTION_STRING", "").strip()
    or os.environ.get("SQL_CONNECTION_STRING", "").strip()
)
_SQL_SERVER = bool(_SQL_CONNECTION_STRING)
_SQLITE_PATH = Path(os.environ.get(
    "CFO_AUTH_DATABASE_PATH", Path(__file__).with_name("auth.sqlite3")
))


class DuplicateAccountError(Exception):
    pass


def _sqlalchemy_url(connection_string: str) -> str:
    if "://" in connection_string:
        return connection_string

    fields = {}
    for name, pattern in {
        "server": r"Server",
        "database": r"Database",
        "username": r"UID|User\s*Id",
        "password": r"PWD|Password",
        "encrypt": r"Encrypt",
        "trust_server_certificate": r"TrustServerCertificate",
    }.items():
        match = re.search(rf"(?:{pattern})=([^;]*)", connection_string, re.IGNORECASE)
        fields[name] = match.group(1).strip() if match else ""

    if not fields["server"] or not fields["database"]:
        raise ValueError(
            "CFO_AUTH_SQL_CONNECTION_STRING must be a SQLAlchemy URL or include Server and Database."
        )

    query = {"driver": os.environ.get("SQL_ODBC_DRIVER", "ODBC Driver 17 for SQL Server")}
    if fields["encrypt"]:
        query["Encrypt"] = fields["encrypt"]
    if fields["trust_server_certificate"]:
        query["TrustServerCertificate"] = fields["trust_server_certificate"]
    return (
        f"mssql+pyodbc://{quote_plus(fields['username'])}:{quote_plus(fields['password'])}"
        f"@{fields['server']}/{fields['database']}?{urlencode(query)}"
    )


if _SQL_SERVER:
    _engine: Engine = create_engine(_sqlalchemy_url(_SQL_CONNECTION_STRING), pool_pre_ping=True)
else:
    _engine = create_engine(
        f"sqlite:///{_SQLITE_PATH.resolve().as_posix()}",
        connect_args={"timeout": 10},
    )


@contextmanager
def _connect() -> Iterator[Connection]:
    with _engine.begin() as connection:
        if not _SQL_SERVER:
            connection.exec_driver_sql("PRAGMA busy_timeout = 10000")
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        yield connection


def _create_table(connection: Connection, sqlite_ddl: str, sql_server_ddl: str) -> None:
    ddl = sql_server_ddl if _SQL_SERVER else sqlite_ddl
    connection.execute(text(ddl))


def initialize() -> None:
    if _SQL_SERVER:
        statements = [
            """IF OBJECT_ID('dbo.CFOAgentUsers', 'U') IS NULL
            CREATE TABLE dbo.CFOAgentUsers (
                id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                name NVARCHAR(120) NOT NULL,
                email NVARCHAR(254) NOT NULL UNIQUE,
                password_hash NVARCHAR(256) NOT NULL,
                created_at BIGINT NOT NULL
            )""",
            """IF OBJECT_ID('dbo.CFOAgentSessions', 'U') IS NULL
            CREATE TABLE dbo.CFOAgentSessions (
                token_hash CHAR(64) NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                CONSTRAINT FK_CFOAgentSessions_CFOAgentUsers
                    FOREIGN KEY (user_id) REFERENCES dbo.CFOAgentUsers(id)
            )""",
            """IF OBJECT_ID('dbo.CFOAgentLoginFailures', 'U') IS NULL
            CREATE TABLE dbo.CFOAgentLoginFailures (
                id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                ip_address NVARCHAR(64) NOT NULL,
                attempted_at BIGINT NOT NULL
            )""",
        ]
        with _connect() as connection:
            for statement in statements:
                connection.execute(text(statement))
            connection.execute(text("""
                IF NOT EXISTS (
                    SELECT 1 FROM sys.indexes
                    WHERE name = 'IX_CFOAgentSessions_Expiry'
                      AND object_id = OBJECT_ID('dbo.CFOAgentSessions')
                )
                CREATE INDEX IX_CFOAgentSessions_Expiry
                    ON dbo.CFOAgentSessions(expires_at)
            """))
            connection.execute(text("""
                IF NOT EXISTS (
                    SELECT 1 FROM sys.indexes
                    WHERE name = 'IX_CFOAgentLoginFailures_AddressTime'
                      AND object_id = OBJECT_ID('dbo.CFOAgentLoginFailures')
                )
                CREATE INDEX IX_CFOAgentLoginFailures_AddressTime
                    ON dbo.CFOAgentLoginFailures(ip_address, attempted_at)
            """))
        return

    with _connect() as connection:
        _create_table(connection, """
            CREATE TABLE IF NOT EXISTS CFOAgentUsers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """, "")
        _create_table(connection, """
            CREATE TABLE IF NOT EXISTS CFOAgentSessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES CFOAgentUsers(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            )
        """, "")
        _create_table(connection, """
            CREATE TABLE IF NOT EXISTS CFOAgentLoginFailures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                attempted_at INTEGER NOT NULL
            )
        """, "")
        connection.execute(text("""
            CREATE INDEX IF NOT EXISTS IX_CFOAgentSessions_Expiry
            ON CFOAgentSessions(expires_at)
        """))
        connection.execute(text("""
            CREATE INDEX IF NOT EXISTS IX_CFOAgentLoginFailures_AddressTime
            ON CFOAgentLoginFailures(ip_address, attempted_at)
        """))


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)


def _encode_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _password_hash(password, salt)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, expected_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            raise ValueError("Unsupported password hash")
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations_text)
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except (ValueError, TypeError):
        return hmac.compare_digest(
            _password_hash(password, bytes(16)).hex(),
            _DUMMY_PASSWORD_HASH.rsplit("$", 1)[1],
        )


def create_user(name: str, email: str, password: str) -> dict:
    try:
        with _connect() as connection:
            if _SQL_SERVER:
                user_id = connection.execute(text("""
                    INSERT INTO dbo.CFOAgentUsers(name, email, password_hash, created_at)
                    OUTPUT INSERTED.id
                    VALUES (:name, :email, :password_hash, :created_at)
                """), {
                    "name": name,
                    "email": email,
                    "password_hash": _encode_password(password),
                    "created_at": int(time.time()),
                }).scalar_one()
            else:
                result = connection.execute(text("""
                    INSERT INTO CFOAgentUsers(name, email, password_hash, created_at)
                    VALUES (:name, :email, :password_hash, :created_at)
                """), {
                    "name": name,
                    "email": email,
                    "password_hash": _encode_password(password),
                    "created_at": int(time.time()),
                })
                user_id = result.lastrowid
    except IntegrityError as exc:
        raise DuplicateAccountError from exc
    return {"id": user_id, "name": name, "email": email, "role": "Controller"}


def verify_user(email: str, password: str) -> Optional[dict]:
    table = "dbo.CFOAgentUsers" if _SQL_SERVER else "CFOAgentUsers"
    with _connect() as connection:
        row = connection.execute(text(
            f"SELECT id, name, email, password_hash FROM {table} WHERE email = :email"
        ), {"email": email}).mappings().first()
    stored_hash = row["password_hash"] if row else _DUMMY_PASSWORD_HASH
    valid_password = _verify_password(password, stored_hash)
    if not row or not valid_password:
        return None
    return {"id": row["id"], "name": row["name"], "email": row["email"], "role": "Controller"}


def issue_session(user_id: int) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    table = "dbo.CFOAgentSessions" if _SQL_SERVER else "CFOAgentSessions"
    with _connect() as connection:
        connection.execute(text(f"DELETE FROM {table} WHERE expires_at <= :now"), {
            "now": int(time.time()),
        })
        connection.execute(text(f"""
            INSERT INTO {table}(token_hash, user_id, expires_at)
            VALUES (:token_hash, :user_id, :expires_at)
        """), {"token_hash": token_hash, "user_id": user_id, "expires_at": expires_at})
    return token, expires_at


def user_for_session(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = int(time.time())
    users = "dbo.CFOAgentUsers" if _SQL_SERVER else "CFOAgentUsers"
    sessions = "dbo.CFOAgentSessions" if _SQL_SERVER else "CFOAgentSessions"
    with _connect() as connection:
        row = connection.execute(text(f"""
            SELECT users.id, users.name, users.email
            FROM {sessions} AS sessions
            JOIN {users} AS users ON users.id = sessions.user_id
            WHERE sessions.token_hash = :token_hash AND sessions.expires_at > :now
        """), {"token_hash": token_hash, "now": now}).mappings().first()
        connection.execute(text(f"DELETE FROM {sessions} WHERE expires_at <= :now"), {
            "now": now,
        })
    if not row:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "role": "Controller",
        "initials": "".join(part[0] for part in row["name"].split() if part)[:2].upper(),
    }


def revoke_session(token: Optional[str]) -> None:
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    table = "dbo.CFOAgentSessions" if _SQL_SERVER else "CFOAgentSessions"
    with _connect() as connection:
        connection.execute(text(f"DELETE FROM {table} WHERE token_hash = :token_hash"), {
            "token_hash": token_hash,
        })


def login_is_limited(ip_address: str) -> bool:
    cutoff = int(time.time()) - LOGIN_WINDOW_SECONDS
    table = "dbo.CFOAgentLoginFailures" if _SQL_SERVER else "CFOAgentLoginFailures"
    with _connect() as connection:
        connection.execute(text(f"DELETE FROM {table} WHERE attempted_at < :cutoff"), {
            "cutoff": cutoff,
        })
        failures = connection.execute(text(f"""
            SELECT COUNT(*) FROM {table}
            WHERE ip_address = :ip_address AND attempted_at >= :cutoff
        """), {"ip_address": ip_address, "cutoff": cutoff}).scalar_one()
    return failures >= MAX_FAILED_LOGINS


def record_login_failure(ip_address: str) -> None:
    table = "dbo.CFOAgentLoginFailures" if _SQL_SERVER else "CFOAgentLoginFailures"
    with _connect() as connection:
        connection.execute(text(f"""
            INSERT INTO {table}(ip_address, attempted_at)
            VALUES (:ip_address, :attempted_at)
        """), {"ip_address": ip_address, "attempted_at": int(time.time())})


def clear_login_failures(ip_address: str) -> None:
    table = "dbo.CFOAgentLoginFailures" if _SQL_SERVER else "CFOAgentLoginFailures"
    with _connect() as connection:
        connection.execute(text(f"DELETE FROM {table} WHERE ip_address = :ip_address"), {
            "ip_address": ip_address,
        })
