# My Age auto claim (Telethon)

Ambil `initData` sendiri lewat Telegram, lalu klaim misi di `https://myageapp.xyz`.

## Referral

Kalau mau ikut dan pakai script ini, masuk lewat link referral pembuatnya:

https://t.me/MyAgeApp?startapp=r1364293980

## Reset & jadwal

- Misi (`/api/tasks`) tidak reset harian: `repeatHours = 0`, `reopensInMs = null`.
  Task channel selesai sekali; task custom multi-tap sampai `pressesRequired` penuh.
- Daily check-in reset tiap **00:00 UTC = 07:00 WIB** (`daily.resetInMs`).
  Streak: 100, 200, 300, 400, 500, 600, 1000 poin.
- Bonus harian lain (tag di nama 300, bio 1000) reset di jam yang sama.
- Refill spin ~67 menit, league ~6 hari.

## Kenapa bukan script lama

Hasil probe (13 Nov 2026):

- `api.myageapp.xyz` tidak ada (NXDOMAIN). API same-origin: `https://myageapp.xyz/api/...`
- Auth: header `authorization: tma <initData>`. Bukan Bearer token.
- POST wajib `idempotency-key` (uuid).
- Tidak ada `/api/auth/login`. Yang ada `POST /api/session`.
- Klaim misi: `POST /api/tasks` body `{"taskId": id}`.
- Tanpa initData valid: `401 {"error":"Open My Age from Telegram."}`

## Pasang

```text
pip install telethon
python3 myage.py --setup "+628xxxxxxxxxx"   # bikin config.json
```

Isi `config.json`:

- `tg_api_id` / `tg_api_hash` — kalau dibiarkan `2040` / `b18441a1ff607e10a989891a5462e627`
  berarti pakai kredensial publik Telegram Desktop (cukup untuk login OTP, tapi rawan
  dibatasi). Idealnya ganti dengan milik sendiri dari https://my.telegram.org.
- `accounts[].phone` nomor Telegram.
- `channels` daftar channel yang mau di-join (opsional, misal `["@channel1"]`).

## Jalan

```text
python3 myage.py --init-only     # tes ambil initData + cek login
python3 myage.py                 # klaim semua misi sekali
python3 myage.py --watch 900     # ulang tiap 15 menit
python3 myage.py --account main --account alt
```

Tanpa login Telegram sama sekali (kalau initData sudah dipegang):

```text
python3 myage.py --init-data "query_id=...&user=...&auth_date=...&hash=..."
python3 myage.py --init-data-file initdata.txt --watch 900
```

Session Telegram disimpan di `sessions/<nama>`, jadi login cuma sekali per akun.
Saat pertama jalan, Telethon minta kode OTP (dan password 2FA kalau aktif).

## Endpoint yang dipakai

- `POST /api/session` body `{}` -> buka sesi
- `GET /api/tasks` -> daftar misi
- `POST /api/tasks` body `{"taskId": id}` -> klaim

Endpoint lain di app (belum dipakai script): `daily`, `spin`, `mystery`, `boss`, `board`, `vip`, `pass`, `wallet`, `withdraw`, `feed`, `league`, `friends`, `channels/seen`, `achievements`.

## Status uji (13 Nov 2026)

- telethon 1.45.0 terpasang di Python 3.14.10, `py_compile` lolos.
- Konek MTProto ke Telegram: **OK** (`connected=True`, `authorized=False`) pakai api_id publik.
- `POST /api/session` dengan initData palsu dijawab `401` sesuai harapan.
- Yang belum: login akun asli (butuh nomor + kode OTP), jadi claim misi belum pernah sukses.

## Batasan

- Butuh nomor HP Telegram aktif untuk login pertama. Kode OTP hanya bisa dimasukkan user.
- `RequestWebView` wajib pakai `peer` = bot-nya (bukan `me`). Kalau pakai `peer=me` Telegram balas `BOT_WEBVIEW_DISABLED`. Script sekarang coba `peer=bot` dulu.
- Join channel pakai `JoinChannelRequest` (method `client.join_channel` sudah tidak ada di Telethon 1.45).
- Klaim terlalu cepat bisa kena 429; script kasih jeda 2-4 detik per misi.
- Kredensial publik `2040` sebaiknya diganti milik sendiri di `my.telegram.org`.
