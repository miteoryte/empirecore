# EmpireCore

Административная панель для управления сетью Telegram-каналов.

## Деплой на Railway

### 1. Загрузите код в GitHub репозиторий

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/empirecore.git
git push -u origin main
```

### 2. Создайте проект на Railway

- Зайдите на [railway.app](https://railway.app)
- New Project → Deploy from GitHub repo → выберите репозиторий

### 3. Настройте переменные окружения

В Railway → Variables добавьте:

| Переменная | Значение | Описание |
|---|---|---|
| `SECRET_KEY` | `какой-то-длинный-случайный-ключ` | Flask secret key |
| `ADMIN_USER` | `admin` | Логин для входа |
| `ADMIN_PASSWORD` | `ваш-пароль` | Пароль для входа |
| `TOTP_SECRET` | _(см. ниже)_ | Секрет для Google Authenticator |

### 4. Настройте Google Authenticator (2FA)

Для генерации TOTP secret используйте Python:

```python
import pyotp
secret = pyotp.random_base32()
print(secret)
# Например: JBSWY3DPEHPK3PXP

# QR-код для сканирования в Google Authenticator:
totp = pyotp.TOTP(secret)
print(totp.provisioning_uri(name="admin@empirecore", issuer_name="EmpireCore"))
```

Запустите этот скрипт локально, скопируйте секрет в `TOTP_SECRET`, 
и добавьте аккаунт в Google Authenticator вручную или через QR-код.

### 5. Подключите свой домен

В Railway → Settings → Domains → Custom Domain → добавьте ваш домен.
Пропишите CNAME запись на ваш домен согласно инструкции Railway.

## Локальный запуск

```bash
pip install -r requirements.txt
python app.py
```

Откройте: http://localhost:5000

По умолчанию:
- Логин: `admin`
- Пароль: `admin123`  
- 2FA код: используйте TOTP secret `JBSWY3DPEHPK3PXP` в Google Authenticator

## Структура проекта

```
empirecore/
├── app.py              # Основное Flask-приложение
├── requirements.txt    # Зависимости
├── Procfile           # Для Railway/Heroku
├── railway.json       # Конфиг Railway
├── templates/
│   ├── base.html      # Базовый шаблон с сайдбаром
│   ├── login.html     # Страница авторизации
│   ├── parser.html    # Раздел Parser
│   ├── settings.html  # Раздел Settings
│   └── analytics.html # Раздел Analytics (заглушка)
└── static/
    ├── css/main.css   # Все стили (тёмная + светлая тема)
    └── js/main.js     # JS утилиты
```
