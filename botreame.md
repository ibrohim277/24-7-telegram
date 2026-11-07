# 24/7 Online boti (ko'p foydalanuvchili)

Foydalanuvchilar botga akkauntini ulaydi, bot esa ularning profilini doim "online" qilib turadi.

## Ishga tushirish

1. `@BotFather` da bot yarating va tokenni oling.
2. https://my.telegram.org dan `api_id` va `api_hash` oling.
3. Shifrlash kalitini yarating (uni yo'qotmang, yo'qolsa saqlangan sessiyalar ochilmaydi):
   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
4. `.env.example` ni `.env` ga nusxalab, hamma qiymatlarni yozing.
5. O'rnating va ishga tushiring:
   ```
   pip install -r bot_requirements.txt
   python bot.py
   ```

## Tugma rangi va premium emoji

`bot.py` boshidagi ikki lug'atni tahrirlang:

```python
STYLES = {"connect": "success", "stop": "danger", "status": "primary", ...}
EMOJI_IDS = {"connect": "5310076249404621168", ...}
```

- `style`: `primary` (ko'k), `success` (yashil), `danger` (qizil) yoki `None` (oddiy).
- `EMOJI_IDS`: premium (custom) emoji ID. Bo'sh qoldirilsa emoji qo'yilmaydi.
- Custom emoji tugmada faqat bot egasida Telegram Premium bo'lsa ishlaydi.
- Emoji ID ni olish: @getidsbot kabi botga premium emoji yuboring.

## 24/7 server (systemd)

```
[Unit]
Description=Online bot
After=network.target

[Service]
WorkingDirectory=/home/USER/online-bot
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Xavfsizlik (juda muhim)

Sessiya fayli akkauntga to'liq kirish beradi, shuning uchun siz boshqalarning akkauntlari uchun javobgarsiz:

- `.env` va `users.db` ni hech kimga bermang, GitHub'ga yuklamang (`.gitignore` ga qo'shing).
- Serverni himoyalang (SSH kalit, yangilanishlar).
- Foydalanuvchilarga botning nima qilishini ochiq ayting (bot buni «Roziman» ekranida qiladi).
- Faqat o'zingiz va foydalanuvchilar ruxsat bergan maqsadda ishlating.
- Telegram userbot'larni qat'iy cheklashi mumkin, juda ko'p akkaunt bitta `api_id` bilan ishlasa, cheklov xavfi oshadi.