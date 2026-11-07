# Telegram "24/7 Online" bot

Profilingizni doim online ko'rsatib turadi.

## O'rnatish

1. API kalitlarni oling: https://my.telegram.org -> **API development tools** -> `api_id` va `api_hash`.
2. Kutubxonani o'rnating:
   ```
   pip install -r requirements.txt
   ```
3. Kalitlarni o'rnating:

   Linux / macOS:
   ```
   export TG_API_ID=12345678
   export TG_API_HASH=abcdef1234567890abcdef1234567890
   ```
   Windows (PowerShell):
   ```
   $env:TG_API_ID="12345678"
   $env:TG_API_HASH="abcdef1234567890abcdef1234567890"
   ```
4. Ishga tushiring:
   ```
   python main.py
   ```
   Birinchi marta telefon raqamingiz (+998...) va Telegram yuborgan kod so'raladi.
   Keyin `online_session.session` fayli yaratiladi, keyingi safar kod so'ralmaydi.

## 24/7 ishlashi uchun

Kompyuteringiz o'chsa, skript ham to'xtaydi. Uni VPS yoki doim yoqiq serverda ishga tushiring.

Linux serverda `systemd` bilan (`/etc/systemd/system/keep-online.service`):

```
[Unit]
Description=Telegram keep online
After=network.target

[Service]
WorkingDirectory=/home/USER/keep-online
Environment=TG_API_ID=12345678
Environment=TG_API_HASH=abcdef1234567890abcdef1234567890
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl enable --now keep-online
```

Yoki oddiygina: `nohup python main.py &` yoki `screen` / `tmux`.

## Muhim eslatmalar

- `.session` faylini hech kimga bermang, u akkauntingizga to'liq kirish beradi.
- `api_id` va `api_hash` ni ham sir saqlang.
- `PING_INTERVAL` (standart 30 soniya) ni juda kichik qilmang, Telegram cheklov qo'yishi mumkin.
- Userbot'lar Telegram qoidalarida cheklangan zonada, shuning uchun faqat shaxsiy akkaunt uchun va me'yorida ishlating.# 24-7-telegram
