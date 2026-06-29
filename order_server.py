#!/usr/bin/env python3
"""
White Dacha Shop — Order Reception Server
Принимает заказы из мини-приложения, сохраняет в БД, уведомляет через MAX API.

Запуск: python3 order_server.py
Порт: 8090
"""

import json
import os
import sys
import sqlite3
import hashlib
import hmac
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ─── Конфигурация ───────────────────────────────────────────────
PORT = int(os.environ.get("SHOP_PORT", "8090"))
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")
MAX_API = "https://platform-api.max.ru"
MAX_BOT_TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
MAX_CHAT_ID = os.environ.get("SHOP_MAX_CHAT_ID", "72239874")
SECRET_KEY = os.environ.get("SHOP_SECRET", "white-dacha-shop-2026")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("shop-server")

EKAT_TZ = timezone(timedelta(hours=5))


# ─── База данных ─────────────────────────────────────────────────

def init_db():
    """Инициализация SQLite базы заказов."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_uid TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            delivery_date TEXT NOT NULL,
            comment TEXT DEFAULT '',
            items_json TEXT NOT NULL,
            total INTEGER NOT NULL,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(delivery_date)")
    conn.commit()
    conn.close()
    log.info("DB initialized: %s", DB_PATH)


def save_order(order_data: dict) -> str:
    """Сохраняет заказ в БД, возвращает order_uid."""
    now = datetime.now(EKAT_TZ).isoformat()
    items = order_data.get("items", [])
    total = sum(i.get("price", 0) * i.get("qty", 0) for i in items)

    # Генерируем уникальный ID заказа
    uid_raw = f"{order_data.get('phone', '')}-{order_data.get('delivery_date', '')}-{time.time()}"
    order_uid = hashlib.sha256(uid_raw.encode()).hexdigest()[:12].upper()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO orders (order_uid, name, phone, address, delivery_date, comment, items_json, total, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
            (
                order_uid,
                order_data.get("name", ""),
                order_data.get("phone", ""),
                order_data.get("address", ""),
                order_data.get("delivery_date", ""),
                order_data.get("comment", ""),
                json.dumps(items, ensure_ascii=False),
                total,
                now,
                now,
            ),
        )
        conn.commit()
        log.info("Order saved: %s, total=%d ₽", order_uid, total)
        return order_uid
    except sqlite3.IntegrityError:
        log.warning("Duplicate order attempt: %s", order_uid)
        return order_uid
    finally:
        conn.close()


def get_orders(status=None, limit=50) -> list:
    """Получает список заказов."""
    conn = sqlite3.connect(DB_PATH)
    if status:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()

    columns = ["id", "order_uid", "name", "phone", "address", "delivery_date",
               "comment", "items_json", "total", "status", "created_at", "updated_at"]
    return [dict(zip(columns, row)) for row in rows]


def update_order_status(order_uid: str, new_status: str) -> bool:
    """Обновляет статус заказа."""
    now = datetime.now(EKAT_TZ).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE order_uid = ?",
        (new_status, now, order_uid),
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


# ─── Уведомление через MAX API ──────────────────────────────────

def send_max_notification(order_data: dict, order_uid: str):
    """Отправляет уведомление о заказе в MAX чат."""
    if not MAX_BOT_TOKEN:
        log.warning("MAX_BOT_TOKEN not set, skipping notification")
        return False

    items = order_data.get("items", [])
    items_text = "\n".join(
        f"{i.get('emoji', '•')} {i.get('name', '?')} — {i.get('qty', 0)}×{i.get('unit', '?')} = {i.get('price', 0) * i.get('qty', 0)}₽"
        for i in items
    )
    total = sum(i.get("price", 0) * i.get("qty", 0) for i in items)

    text = (
        f"🛒 НОВЫЙ ЗАКАЗ #{order_uid}\n\n"
        f"{items_text}\n\n"
        f"💰 Итого: {total}₽\n\n"
        f"👤 {order_data.get('name', '?')}\n"
        f"📱 {order_data.get('phone', '?')}\n"
        f"📍 {order_data.get('address', '?')}\n"
        f"📅 {order_data.get('delivery_date', '?')}\n"
        f"💬 {order_data.get('comment', '—')}"
    )

    try:
        import urllib.request
        url = f"{MAX_API}/messages?chat_id={MAX_CHAT_ID}"
        payload = json.dumps({"text": text, "notify": True}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": MAX_BOT_TOKEN,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                log.info("MAX notification sent for order %s", order_uid)
                return True
            else:
                log.error("MAX API returned %d", resp.status)
                return False
    except Exception as e:
        log.error("Failed to send MAX notification: %s", e)
        return False


# ─── HTTP-обработчик ────────────────────────────────────────────

class ShopHandler(BaseHTTPRequestHandler):
    """HTTP-обработчик для API магазина."""

    def log_message(self, format, *args):
        log.info("%s %s", self.address_string(), format % args)

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # CORS для мини-приложения
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Preflight CORS."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/health":
            self._send_json({"status": "ok", "service": "white-dacha-shop", "time": datetime.now(EKAT_TZ).isoformat()})

        elif path == "/api/orders":
            status_filter = parsed.query.split("=")[-1] if "status=" in parsed.query else None
            limit = int(parsed.query.split("=")[-1]) if "limit=" in parsed.query else 50
            orders = get_orders(status=status_filter, limit=limit)
            # Парсим items_json
            for o in orders:
                try:
                    o["items"] = json.loads(o.pop("items_json"))
                except (json.JSONDecodeError, KeyError):
                    o["items"] = []
            self._send_json({"orders": orders, "count": len(orders)})

        elif path.startswith("/api/orders/"):
            uid = path.split("/")[-1]
            orders = get_orders()
            order = next((o for o in orders if o["order_uid"] == uid), None)
            if order:
                try:
                    order["items"] = json.loads(order.pop("items_json"))
                except (json.JSONDecodeError, KeyError):
                    order["items"] = []
                self._send_json(order)
            else:
                self._send_json({"error": "Order not found"}, 404)

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        if path == "/api/orders":
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            # Валидация
            required = ["name", "phone", "address", "delivery_date", "items"]
            missing = [f for f in required if not data.get(f)]
            if missing:
                self._send_json({"error": f"Missing fields: {', '.join(missing)}"}, 400)
                return

            if not isinstance(data["items"], list) or len(data["items"]) == 0:
                self._send_json({"error": "Items must be a non-empty list"}, 400)
                return

            # Сохраняем
            order_uid = save_order(data)

            # Уведомляем в MAX (в отдельном потоке, чтобы не блокировать ответ)
            threading.Thread(
                target=send_max_notification,
                args=(data, order_uid),
                daemon=True,
            ).start()

            self._send_json({
                "success": True,
                "order_uid": order_uid,
                "message": f"Заказ #{order_uid} принят! Мы свяжемся для подтверждения.",
            }, 201)

        elif path.startswith("/api/orders/") and path.endswith("/status"):
            # POST /api/orders/{uid}/status — обновление статуса
            uid = path.split("/")[-2]
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_json({"error": "Invalid JSON"}, 400)
                return

            new_status = data.get("status", "")
            valid_statuses = ["new", "confirmed", "preparing", "delivering", "delivered", "cancelled"]
            if new_status not in valid_statuses:
                self._send_json({"error": f"Invalid status. Valid: {', '.join(valid_statuses)}"}, 400)
                return

            if update_order_status(uid, new_status):
                self._send_json({"success": True, "order_uid": uid, "status": new_status})
            else:
                self._send_json({"error": "Order not found"}, 404)

        else:
            self._send_json({"error": "Not found"}, 404)


# ─── Запуск ──────────────────────────────────────────────────────

def main():
    init_db()
    server = HTTPServer(("0.0.0.0", PORT), ShopHandler)
    log.info("White Dacha Shop server starting on port %d", PORT)
    log.info("Endpoints:")
    log.info("  POST /api/orders           — создать заказ")
    log.info("  GET  /api/orders           — список заказов")
    log.info("  GET  /api/orders/{uid}     — детали заказа")
    log.info("  POST /api/orders/{uid}/status — обновить статус")
    log.info("  GET  /health               — проверка")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Server stopped")
        server.server_close()


if __name__ == "__main__":
    main()
