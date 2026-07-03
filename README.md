# Настя (@asnastya_bot) — OpenClaw architecture

Капризная AI-болталка — астрология, психология, тренды, шопинг, BMW M3.
Ведёт Telegram канал @chasnastya, активно общается в группах, понимает фото
и голосовые. Работает в среде OpenClaw, развёрнутого в GitHub Actions 24/7.

## ✨ Возможности

| Функция | Описание |
|---|---|
| 💬 Текст | Общение в личке и группах, память диалога и фактов |
| 📷 Фото (Vision) | Понимает фото через Gemini/GPT-4o |
| 🎤 Голосовые | Транскрипция через Whisper |
| 😀 Стикеры/GIF/Видео | Реагирует и комментирует |
| 🔍 Новости | Развёрнуто дополняет новости инфой из интернета |
| 📺 Канал @chasnastya | Автопостинг: факты, тренды, AI-посты каждые 20 мин |
| 🗣 Proactive | Сам начинает беседу в тихих/активных группах |
| 📝 Память | 30-мин суммаризация обсуждений |
| 🏷 Inline | `@asnastya_bot <вопрос>` в любом чате |
| 🤝 Партнёры | Контекстные ссылки (sochiautoparts.ru/partners.json) |
| 🎀 3 реакции | 3 положительные реакции на каждый пост в каналах |

## 🏗 Архитектура

OpenClaw Gateway (Node.js) → OpenAI API на localhost:18789.
Python aiogram бот → все AI через OpenClaw.

## 🚀 Запуск

### GitHub Actions (24/7)
1. Секреты: `BOT_TOKEN`, `OWNER_ID`, `GH_PAT_TOKEN`, `CHANNEL_ID` (обязательные)
2. AI ключи: `GROQ_API_KEY`, `GEMINI_API_KEY`, `HF_TOKEN`, etc.
3. Бот работает на Pollinations free без ключей.

### Локально
```bash
pip install -r requirements.txt
npm install -g openclaw@latest
cp .env.example .env  # заполнить BOT_TOKEN, CHANNEL_ID
python -m bot.main
```

## ⚙️ Настройки @BotFather
1. Group Privacy → OFF
2. Inline Mode → ON
3. В каналы добавлять как админа

## 📄 Лицензия
MIT
