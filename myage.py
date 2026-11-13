#!/usr/bin/env python3
"""My Age — auto initData lewat Telethon, lalu klaim semua misi.

Alur:
  1. Buka Mini App bot di akun Telegram (RequestWebView) -> dapat tgWebAppData (initData valid)
  2. POST /api/session  (header: authorization: tma <initData>)
  3. GET  /api/tasks
  4. POST /api/tasks {taskId}  (wajib idempotency-key)

Catatan: initData hanya bisa didapat kalau api_id/api_hash asli (my.telegram.org)
dan akun sudah login. Session disimpan per akun di folder sessions/.

Pakai:
  python3 myage.py --init-only              # cek initData + session saja
  python3 myage.py                          # klaim misi sekali
  python3 myage.py --watch 900              # ulangi tiap 900 detik
  python3 myage.py --account main --account alt
"""

import argparse
import asyncio
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

try:
    from telethon import TelegramClient
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import RequestWebViewRequest
except ImportError:  # pragma: no cover - dicek saat dipakai
    TelegramClient = None
    RequestWebViewRequest = None
    JoinChannelRequest = None


def need_telethon():
    if TelegramClient is None:
        print("Modul kurang: telethon. Jalankan: pip install telethon")
        sys.exit(1)

BASE = "https://myageapp.xyz"

# Kalau config.json tidak berisi tg_api_id/tg_api_hash, pakai kredensial
# publik Telegram Desktop. Cukup untuk login pakai nomor HP + kode OTP,
# tapi berisiko dibatasi Telegram karena dipakai banyak orang.
# Idealnya isi dari my.telegram.org (API development tools).
PUBLIC_API_ID = 2040
PUBLIC_API_HASH = "b18441a1ff607e10a989891a5462e627"

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
SESSION_DIR = HERE / "sessions"

DEFAULT_CONFIG = {
    "bot": "MyAgeApp",
    "app_url": BASE,
    "channels": [],
    "accounts": [],
}


def resolve_credentials(config):
    """Kembalikan (api_id, api_hash, sumber)."""
    api_id = config.get("tg_api_id")
    api_hash = config.get("tg_api_hash")
    if api_id and api_hash and "isi_dari" not in str(api_hash):
        return int(api_id), str(api_hash), "config"
    return PUBLIC_API_ID, PUBLIC_API_HASH, "publik"


def load_config():
    """Baca config.json. Kalau belum ada, pakai default (tanpa keluar)."""
    if not CONFIG_PATH.is_file():
        print(f"config.json belum ada di {CONFIG_PATH}; pakai default")
        return dict(DEFAULT_CONFIG)
    with CONFIG_PATH.open() as fh:
        config = json.load(fh)
    for key, value in DEFAULT_CONFIG.items():
        config.setdefault(key, value)
    return config


def write_config_template(phone=None):
    """Bikin config.json awal supaya tinggal diisi."""
    config = dict(DEFAULT_CONFIG)
    if phone:
        config["accounts"] = [{"name": "main", "phone": phone}]
    else:
        config["accounts"] = [{"name": "main", "phone": "+62812xxxx"}]
    config["tg_api_id"] = PUBLIC_API_ID
    config["tg_api_hash"] = PUBLIC_API_HASH
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n")
    return CONFIG_PATH

def pick_init_data(url):
    """Ambil tgWebAppData dari URL WebView lalu URL-decode."""
    if "tgWebAppData=" not in url:
        return None
    raw = url.split("tgWebAppData=", 1)[1]
    for stop in ("&tgWebAppVersion", "&tgWebAppPlatform", "&tgWebAppThemeParams"):
        if stop in raw:
            raw = raw.split(stop, 1)[0]
    return urllib.parse.unquote(raw)


async def open_webview(client, bot_username, app_url):
    """Coba beberapa varian RequestWebView, ambil yang berhasil."""
    bot = await client.get_input_entity(bot_username)
    me = await client.get_input_entity("me")

    attempts = [
        # WebView harus dibuka di chat bot-nya (peer=bot). peer=me -> BOT_WEBVIEW_DISABLED.
        {"peer": bot, "from_bot_menu": True},
        {"peer": bot},
        {"peer": me, "from_bot_menu": True},
    ]

    last_error = None
    for extra in attempts:
        kwargs = {"bot": bot, "platform": "android", "url": app_url, **extra}
        try:
            result = await client(RequestWebViewRequest(**kwargs))
        except Exception as exc:  # noqa: BLE001 - semua varian boleh gagal
            last_error = exc
            continue
        init_data = pick_init_data(getattr(result, "url", "") or "")
        if init_data:
            return init_data
        last_error = RuntimeError("URL WebView tanpa tgWebAppData")

    raise RuntimeError(f"Gagal ambil initData: {last_error}")


def _sync_request(method, url, headers, body, timeout=30):
    """urllib sinkron; dipanggil di thread biar tidak blok event loop."""
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001 - jaringan
        return 0, json.dumps({"error": type(exc).__name__})


class Api:
    """Klien API My Age. Auth pakai header tma, POST pakai idempotency-key."""

    def __init__(self, init_data):
        self.init_data = init_data

    def _headers(self, post=False):
        headers = {
            "authorization": f"tma {self.init_data}",
            "user-agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36",
            "accept": "application/json",
        }
        if post:
            headers["content-type"] = "application/json"
            headers["idempotency-key"] = str(uuid.uuid4())
        return headers

    async def get(self, path):
        return await self._request("GET", path)

    async def post(self, path, body=None):
        return await self._request("POST", path, {} if body is None else body)

    async def _request(self, method, path, body=None):
        url = f"{BASE}/api/{path}"
        status, text = 0, ""
        for attempt in range(3):
            status, text = await asyncio.to_thread(
                _sync_request, method, url, self._headers(method == "POST"), body
            )
            if status == 429 and attempt < 2:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if status >= 500 and attempt < 2 and method == "GET":
                await asyncio.sleep(3)
                continue
            break

        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {"error": text[:200]}
        return {"status": status, "ok": 200 <= status < 300, "data": data}


async def process_account(init_data, account, config, init_only=False):
    """Bagian setelah initData didapat: session -> tasks -> claim."""
    name = account.get("name") or "main"
    print(f"[{name}] initData didapat ({len(init_data)} char)")

    if init_only:
        snippet = init_data[:60] + "..." if len(init_data) > 60 else init_data
        print(f"[{name}] preview: {snippet}")
        return {"account": name, "init_data": True}

    api = Api(init_data)

    session_res = await api.post("session")
    print(f"[{name}] session -> {session_res['status']}")
    if not session_res["ok"]:
        print(f"[{name}] gagal: {session_res['data']}")
        return {"account": name, "error": session_res["data"]}

    data = session_res["data"]
    user = data.get("user") or data.get("me") or {}
    if isinstance(user, dict) and user.get("id"):
        print(f"[{name}] akun: {user.get('id')} {user.get('username') or ''}".strip())

    tasks_res = await api.get("tasks")
    if not tasks_res["ok"]:
        print(f"[{name}] tasks gagal: {tasks_res['data']}")
        return {"account": name, "error": tasks_res["data"]}

    tasks = tasks_res["data"].get("tasks") or []
    print(f"[{name}] misi terdeteksi: {len(tasks)}")

    claimed = 0
    for task in tasks:
        if task.get("claimed"):
            continue
        wait = int(task.get("waitLeftMs") or 0)
        if wait > 0:
            await asyncio.sleep(min(wait / 1000, 60))

        res = await api.post("tasks", {"taskId": task.get("id")})
        label = task.get("title") or task.get("id")

        if res["status"] == 401:
            print(f"[{name}] initData ditolak, berhenti. Buka ulang Mini App.")
            break
        if res["status"] == 409 and (res["data"] or {}).get("reopen"):
            print(f"[{name}] 409 reopen -> refresh session")
            session_res = await api.post("session")
            if not session_res["ok"]:
                break
            res = await api.post("tasks", {"taskId": task.get("id")})

        if res["ok"]:
            claimed += 1
            print(f"[{name}] claim ok: {label}")
        else:
            print(f"[{name}] claim gagal: {label} -> {res['status']} {str(res['data'])[:120]}")

        await asyncio.sleep(random.uniform(2, 4))

    print(f"[{name}] selesai, {claimed} misi diklaim")
    return {"account": name, "claimed": claimed, "total": len(tasks)}


async def run_account(account, config, init_only=False, init_data=None):
    """Login Telegram (kalau perlu), ambil initData, lalu proses."""
    name = account.get("name") or "main"
    if init_data:
        return await process_account(init_data, account, config, init_only)

    need_telethon()
    SESSION_DIR.mkdir(exist_ok=True)
    api_id, api_hash, source = resolve_credentials(config)
    if source == "publik":
        print(f"[{name}] tg_api_id kosong, pakai kredensial publik Telegram Desktop")

    client = TelegramClient(
        str(SESSION_DIR / name),
        api_id,
        api_hash,
        device_model="Android",
        system_version="13",
    )

    print(f"[{name}] login Telegram...")
    await client.start(phone=account.get("phone") or (lambda: input("Kode OTP: ")))
    try:
        for channel in config.get("channels") or []:
            try:
                entity = await client.get_input_entity(channel)
                await client(JoinChannelRequest(entity))
                print(f"[{name}] join {channel}")
            except Exception as exc:  # noqa: BLE001
                print(f"[{name}] skip {channel}: {type(exc).__name__}: {str(exc)[:120]}")
            await asyncio.sleep(random.uniform(1.5, 3))

        data = await open_webview(
            client, config.get("bot", "MyAgeApp"), config.get("app_url", BASE)
        )
        return await process_account(data, account, config, init_only)
    finally:
        await client.disconnect()


async def main_async(args):
    config = load_config()
    wanted = args.account
    accounts = config.get("accounts") or []

    manual = getattr(args, "init_data", None)
    if manual:
        accounts = [{"name": "manual"}]
        if wanted:
            accounts[0]["name"] = wanted[0]

    if wanted and not manual:
        accounts = [a for a in accounts if (a.get("name") or "main") in wanted]
        if not accounts:
            print(f"Akun tidak ada di config: {', '.join(wanted)}")
            return 1

    if not accounts:
        print("Belum ada akun di config.json")
        print("Isi accounts[].phone, atau pakai --init-data '<tgWebAppData>'")
        return 1

    results = []
    for account in accounts:
        try:
            results.append(
                await run_account(
                    account, config, init_only=args.init_only, init_data=manual
                )
            )
        except Exception as exc:  # noqa: BLE001 - satu akun gagal jangan stop semua
            print(f"[{account.get('name', 'main')}] error: {type(exc).__name__}: {exc}")
            results.append({"account": account.get("name"), "error": str(exc)})

    if not args.quiet:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


def main():
    parser = argparse.ArgumentParser(description="My Age auto claim via Telethon")
    parser.add_argument("--account", action="append", help="nama akun di config.json (bisa diulang)")
    parser.add_argument("--init-only", action="store_true", help="hanya ambil initData")
    parser.add_argument("--init-data", dest="init_data", help="pakai initData siap pakai (tgWebAppData)")
    parser.add_argument("--init-data-file", help="baca initData dari file")
    parser.add_argument("--setup", nargs="?", const="", metavar="NOMOR_HP", help="buat config.json awal")
    parser.add_argument("--watch", type=int, metavar="DETIK", help="ulangi tiap N detik")
    parser.add_argument("--quiet", action="store_true", help="jangan print JSON hasil")
    args = parser.parse_args()

    if args.setup is not None:
        path = write_config_template(args.setup or None)
        print(f"config.json dibuat: {path}")
        return 0

    if args.init_data_file:
        args.init_data = Path(args.init_data_file).read_text().strip()

    try:
        if args.watch:
            if args.watch < 60:
                print("Interval minimal 60 detik")
                return 1
            while True:
                asyncio.run(main_async(args))
                print(f"--- tunggu {args.watch}s ---")
                time.sleep(args.watch)
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("stop")
        return 130


if __name__ == "__main__":
    sys.exit(main())
