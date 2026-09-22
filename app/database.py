import sqlite3
import os
import shutil
import logging
from contextlib import contextmanager

try:
    import resource  # Unix-only; unavailable on Windows dev machines.
except ImportError:
    resource = None

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "tracker.db"))


def _cleanup_wal_files():
    """Remove corrupted WAL files that prevent database access."""
    wal_path = f"{DB_PATH}-wal"
    shm_path = f"{DB_PATH}-shm"
    for path in [wal_path, shm_path]:
        if os.path.exists(path):
            try:
                os.remove(path)
                logger.warning(f"[database] Removed corrupted WAL file: {path}")
            except Exception as e:
                logger.error(f"[database] Failed to remove {path}: {e}")


def _log_diagnostics():
    """Log why the database directory might be unreachable — a vanished mount,
    permissions, or a full disk all raise the same generic OperationalError,
    so we have to inspect the filesystem ourselves to tell them apart."""
    db_dir = os.path.dirname(DB_PATH) or "."
    try:
        dir_exists = os.path.isdir(db_dir)
        logger.error(f"[database] diagnostics: dir={db_dir} exists={dir_exists}")
        if dir_exists:
            logger.error(f"[database] diagnostics: dir writable={os.access(db_dir, os.W_OK)}")
            usage = shutil.disk_usage(db_dir)
            logger.error(
                f"[database] diagnostics: disk free={usage.free / 1024 / 1024:.1f}MB "
                f"of {usage.total / 1024 / 1024:.1f}MB"
            )
            logger.error(f"[database] diagnostics: db file exists={os.path.exists(DB_PATH)}")
        if resource is not None:
            try:
                open_fds = len(os.listdir("/proc/self/fd"))
                soft_limit, hard_limit = resource.getrlimit(resource.RLIMIT_NOFILE)
                logger.error(
                    f"[database] diagnostics: open fds={open_fds} "
                    f"(soft limit={soft_limit}, hard limit={hard_limit})"
                )
            except Exception as fd_err:
                logger.error(f"[database] fd diagnostics failed: {fd_err}")
    except Exception as diag_err:
        logger.error(f"[database] diagnostics failed: {diag_err}")


def _ensure_db_dir():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.isdir(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        logger.warning(f"[database] Recreated missing database directory: {db_dir}")


@contextmanager
def get_connection():
    """Yield a connection that is committed on success and always closed.

    sqlite3's own context manager commits or rolls back but never closes, so
    `with sqlite3.connect(...)` leaks a handle on every call — which adds up
    fast in a long-running bot. timeout=30 sets busy_timeout so the bot and the
    scheduler wait for each other instead of raising "database is locked".
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as e:
        if "unable to open database file" in str(e):
            logger.error(f"[database] Failed to open database: {e}. Attempting recovery...")
            _log_diagnostics()
            _cleanup_wal_files()
            try:
                _ensure_db_dir()
                conn = sqlite3.connect(DB_PATH, timeout=30)
                conn.row_factory = sqlite3.Row
                logger.info("[database] Database recovered")
            except sqlite3.OperationalError as recovery_err:
                logger.critical(f"[database] Recovery failed: {recovery_err}")
                raise
        else:
            raise

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"[database] Created database directory: {db_dir}")
        except Exception as e:
            logger.error(f"[database] Failed to create database directory: {e}")
            raise

    with get_connection() as conn:
        # WAL lets the scheduler read while the bot writes instead of the two
        # blocking each other. It is a persistent property of the file, so this
        # only has to be set once, but re-running it is harmless.
        conn.execute("PRAGMA journal_mode=WAL")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                name TEXT,
                last_price REAL,
                previous_price REAL,
                image_url TEXT,
                merchant_name TEXT,
                is_amazon BOOLEAN DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                alert_threshold REAL,
                price_min REAL,
                price_max REAL,
                require_amazon_merchant BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                UNIQUE(user_id, product_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                price REAL NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                is_admin BOOLEAN DEFAULT FALSE,
                language TEXT DEFAULT 'en',
                authorized_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_user_products ON user_products(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_product_price_history ON price_history(product_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_authorized_users_status ON authorized_users(status)")

        try:
            conn.execute("ALTER TABLE products ADD COLUMN previous_price REAL")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE products ADD COLUMN image_url TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE products ADD COLUMN merchant_name TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE products ADD COLUMN is_amazon BOOLEAN DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE products ADD COLUMN available BOOLEAN DEFAULT 1")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE user_products ADD COLUMN price_min REAL")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE user_products ADD COLUMN price_max REAL")
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute("ALTER TABLE user_products ADD COLUMN require_amazon_merchant BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        conn.commit()
        logger.info("[database] Database initialized successfully")


# Runtime settings live in the database rather than the environment so an admin
# can change them from Telegram and have them survive a restart.
CHECK_INTERVAL_SETTING = "check_interval_minutes"


def get_setting(key: str, default: str | None = None) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
            (key, value),
        )


def get_check_interval(default: int) -> int:
    """Minutes between scheduled price checks, falling back to `default`.

    `default` is what CHECK_INTERVAL_MINUTES says, so an untouched deployment
    keeps behaving exactly as its .env describes.
    """
    raw = get_setting(CHECK_INTERVAL_SETTING)
    if raw is None:
        return default
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        logger.warning(f"[database] Ignoring non-numeric {CHECK_INTERVAL_SETTING}={raw!r}")
        return default
    if minutes <= 0:
        logger.warning(f"[database] Ignoring non-positive {CHECK_INTERVAL_SETTING}={minutes}")
        return default
    return minutes


def set_check_interval(minutes: int):
    set_setting(CHECK_INTERVAL_SETTING, str(int(minutes)))
    logger.info(f"[database] Check interval set to {minutes} minute(s)")


def add_product(url: str, name: str, price: float, image_url: str | None = None, merchant_name: str | None = None, is_amazon: bool = True) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO products (url, name, last_price, previous_price, image_url, merchant_name, is_amazon) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (url, name, price, price, image_url, merchant_name, is_amazon),
        )
        product_id = cursor.lastrowid or conn.execute(
            "SELECT id FROM products WHERE url = ?", (url,)
        ).fetchone()[0]

        _insert_price_history(conn, product_id, price)
        return product_id


def add_user_product(user_id: int, product_id: int, alert_threshold: float | None = None, price_min: float | None = None, price_max: float | None = None, require_amazon_merchant: bool = False):
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_products (user_id, product_id, alert_threshold, price_min, price_max, require_amazon_merchant) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, product_id, alert_threshold, price_min, price_max, require_amazon_merchant),
        )
        conn.commit()


def update_user_product_preferences(user_id: int, product_id: int, price_min: float | None = None, price_max: float | None = None, require_amazon_merchant: bool | None = None):
    with get_connection() as conn:
        updates = []
        params = []
        if price_min is not None:
            updates.append("price_min = ?")
            params.append(price_min)
        if price_max is not None:
            updates.append("price_max = ?")
            params.append(price_max)
        if require_amazon_merchant is not None:
            updates.append("require_amazon_merchant = ?")
            params.append(require_amazon_merchant)

        if updates:
            params.extend([user_id, product_id])
            query = f"UPDATE user_products SET {', '.join(updates)} WHERE user_id = ? AND product_id = ?"
            conn.execute(query, params)
            conn.commit()


def clear_user_product_alert(user_id: int, product_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE user_products SET price_min = NULL, price_max = NULL WHERE user_id = ? AND product_id = ?",
            (user_id, product_id),
        )
        conn.commit()


def remove_user_product(user_id: int, product_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM user_products WHERE user_id = ? AND product_id = ?", (user_id, product_id))

        product_used = conn.execute(
            "SELECT COUNT(*) FROM user_products WHERE product_id = ?", (product_id,)
        ).fetchone()[0]

        if product_used == 0:
            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.execute("DELETE FROM price_history WHERE product_id = ?", (product_id,))

        conn.commit()


def get_all_products():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, url, name, last_price, previous_price, image_url, added_at, available "
            "FROM products ORDER BY added_at DESC"
        ).fetchall()
    return [tuple(row) for row in rows]


def get_user_products(user_id: int):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.id, p.url, p.name, p.last_price, p.previous_price, p.image_url, p.added_at, p.merchant_name, p.is_amazon,
                   up.alert_threshold, up.price_min, up.price_max, up.require_amazon_merchant, p.available
            FROM products p
            JOIN user_products up ON p.id = up.product_id
            WHERE up.user_id = ?
            ORDER BY p.added_at DESC
        """, (user_id,)).fetchall()
    return [tuple(row) for row in rows]


def update_price(url: str, new_price: float):
    with get_connection() as conn:
        product = conn.execute("SELECT id FROM products WHERE url = ?", (url,)).fetchone()
        if product:
            conn.execute(
                "UPDATE products SET previous_price = last_price, last_price = ?, available = 1 WHERE url = ?",
                (new_price, url),
            )
            # Same connection, same reason as add_product — see _insert_price_history.
            _insert_price_history(conn, product[0], new_price)


def set_product_availability(url: str, available: bool):
    """Record whether the listing is currently buyable.

    Kept separate from update_price: an unavailable page's price is stale or
    belongs to another seller's offer, so it must not become the new baseline
    that the next comparison is made against.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE products SET available = ? WHERE url = ?",
            (1 if available else 0, url),
        )


def _insert_price_history(conn, product_id: int, price: float):
    """Append a price point using a caller-supplied connection.

    Callers that already hold a write transaction MUST use this rather than
    add_price_to_history, which opens its own connection and would deadlock
    against the lock the caller is holding.
    """
    conn.execute(
        "INSERT INTO price_history (product_id, price) VALUES (?, ?)",
        (product_id, price),
    )


def add_price_to_history(product_id: int, price: float):
    with get_connection() as conn:
        _insert_price_history(conn, product_id, price)


def get_price_history(product_id: int, limit: int = 30):
    with get_connection() as conn:
        # Tie-break on id: recorded_at only has second granularity, so two points
        # logged in the same second would otherwise come back in arbitrary order.
        rows = conn.execute(
            "SELECT price, recorded_at FROM price_history WHERE product_id = ? "
            "ORDER BY recorded_at DESC, id DESC LIMIT ?",
            (product_id, limit),
        ).fetchall()
    return [tuple(row) for row in rows]


def remove_product(product_id: int):
    with get_connection() as conn:
        conn.execute("DELETE FROM user_products WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM price_history WHERE product_id = ?", (product_id,))
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()


def get_product_by_id(product_id: int):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, url, name, last_price, previous_price, image_url, added_at, merchant_name, is_amazon, available "
            "FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
    return tuple(row) if row else None


def get_product_by_url(url: str):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, url, name, last_price, previous_price, image_url, added_at FROM products WHERE url = ?",
            (url,),
        ).fetchone()
    return tuple(row) if row else None


def is_user_authorized(user_id: int) -> bool:
    """Check if user is authorized."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM authorized_users WHERE user_id = ? AND status = 'active'",
            (user_id,)
        ).fetchone()
    return row is not None


def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_admin FROM authorized_users WHERE user_id = ? AND status = 'active'",
            (user_id,)
        ).fetchone()
    return row and row[0] if row else False


def add_authorized_user(user_id: int, username: str | None = None, is_admin_flag: bool = False):
    """Add or reactivate authorized user."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM authorized_users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE authorized_users SET status = 'active', username = ?, is_admin = ? WHERE user_id = ?",
                (username, is_admin_flag, user_id)
            )
        else:
            conn.execute(
                "INSERT INTO authorized_users (user_id, username, is_admin, language, status) VALUES (?, ?, ?, 'en', 'active')",
                (user_id, username, is_admin_flag)
            )
        conn.commit()
        logger.info(f"[database] User {user_id} ({username}) authorized with admin={is_admin_flag}")


def remove_authorized_user(user_id: int):
    """Disable user."""
    with get_connection() as conn:
        conn.execute("UPDATE authorized_users SET status = 'disabled' WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_products WHERE user_id = ?", (user_id,))
        conn.commit()
        logger.info(f"[database] User {user_id} disabled")


def get_authorized_users():
    """Get all authorized users."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, username, is_admin, authorized_at FROM authorized_users WHERE status = 'active' ORDER BY authorized_at DESC"
        ).fetchall()
    return [tuple(row) for row in rows]


def get_user_language(user_id: int) -> str:
    """Get user's language preference."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT language FROM authorized_users WHERE user_id = ? AND status = 'active'",
            (user_id,)
        ).fetchone()
    return row[0] if row else "en"


def set_user_language(user_id: int, language: str):
    """Save user's language preference."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM authorized_users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE authorized_users SET language = ? WHERE user_id = ?",
                (language, user_id)
            )
        else:
            conn.execute(
                "INSERT INTO authorized_users (user_id, language, status) VALUES (?, ?, 'active')",
                (user_id, language)
            )
        conn.commit()
        logger.info(f"[database] User {user_id} language set to {language}")


def get_all_authorized_users_for_notification():
    """Get all active users with their language for notifications."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, language FROM authorized_users WHERE status = 'active'"
        ).fetchall()
    return [tuple(row) for row in rows]


def get_user_for_product_notification(product_id: int):
    """Get all users tracking this product with their language and preferences."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT au.user_id, au.language, up.price_min, up.price_max, up.require_amazon_merchant
            FROM authorized_users au
            JOIN user_products up ON au.user_id = up.user_id
            WHERE up.product_id = ? AND au.status = 'active'
        """, (product_id,)).fetchall()
    return [tuple(row) for row in rows]
