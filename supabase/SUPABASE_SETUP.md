# Подключение Supabase (PostgreSQL)

Зачем: на Vercel сейчас данные хранятся в `/tmp/gym_store.json`. Этот файл стирается при каждом
холодном старте функции, поэтому аккаунты и брони там «живут» недолго. Supabase даёт постоянную
PostgreSQL-базу, которой одинаково пользуются и локальная версия, и Vercel.

## 1. Создать проект

1. Зайдите на https://supabase.com → **New project**.
2. Укажите имя (например `gym-booking`), придумайте **Database Password** и сохраните его.
3. Region: ближайший, например **Frankfurt (eu-central-1)**.

## 2. Создать таблицы

1. В проекте откройте **SQL Editor → New query**.
2. Вставьте содержимое файла [`schema.sql`](schema.sql) и нажмите **Run**.

Скрипт создаёт таблицы `users`, `sessions`, `bookings`, `backup_logs` и правила на уровне базы:

| Правило | Как реализовано в Postgres |
| :--- | :--- |
| SES-### | `check (session_code ~ '^SES-[0-9]{3}$')` |
| Нет овербукинга (FR-03) | `check (booked_count <= capacity)` |
| Максимум 2 активные брони (BR-04) | триггер `trg_booking_limit` |
| Отмена только в течение 24 ч (BR-05) | триггер `trg_cancel_window` |
| Одна бронь на тренировку | частичный уникальный индекс `bookings_one_active_per_session` |

Получается двойная защита: правила проверяет и Flask API, и сама база. Это хороший аргумент
на защите.

## 3. Взять строку подключения

**Project → Connect** (кнопка вверху) → вкладка **Connection string → URI**.

* Для Vercel (serverless) выберите **Transaction pooler**, порт **6543**:
  ```
  postgresql://postgres.<project-ref>:<PASSWORD>@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
  ```
* Для локального запуска подойдёт **Session pooler** (порт 5432) или та же строка.

Вместо `<PASSWORD>` подставьте пароль из шага 1.
**Не коммитьте эту строку в git.**

## 4. Переменные окружения

| Переменная | Где задать | Значение |
| :--- | :--- | :--- |
| `DATABASE_URL` | Vercel → Project → Settings → Environment Variables | строка из шага 3 |
| `SECRET_KEY` | там же | длинная случайная строка: `python -c "import secrets; print(secrets.token_hex(32))"` |

Локально (PowerShell):

```powershell
$env:DATABASE_URL = "postgresql://..."
$env:SECRET_KEY = "..."
python app.py
```

`SECRET_KEY` нужен уже сейчас: им подписываются токены входа. Без него используется
dev-ключ из кода, и тогда любой, кто видел репозиторий, может подделать токен.

## 5. Изменения в коде (следующий шаг)

1. Добавить драйвер в `requirements.txt`:
   ```
   psycopg[binary]>=3.2
   ```
2. В `api/_database.py` добавить третий движок рядом с SQLite и JSON:
   ```python
   DATABASE_URL = os.environ.get("DATABASE_URL")
   USE_POSTGRES = bool(DATABASE_URL)

   def _pg_conn():
       import psycopg
       from psycopg.rows import dict_row
       # prepare_threshold=None обязателен для Transaction pooler (порт 6543)
       return psycopg.connect(DATABASE_URL, row_factory=dict_row, prepare_threshold=None)
   ```
3. В каждой публичной функции (`create_booking`, `cancel_booking`, `get_user_bookings`, …)
   добавить ветку `if USE_POSTGRES:` с теми же SQL-запросами. Отличия от SQLite:
   * плейсхолдеры `%s` вместо `?`;
   * блокировка строки сессии при бронировании `SELECT ... FROM sessions WHERE id = %s FOR UPDATE`
     вместо `BEGIN IMMEDIATE`;
   * `INSERT ... RETURNING id` вместо `cursor.lastrowid`;
   * `created_at`, `session_date` и `session_time` возвращаются как `datetime`, `date` и `time`,
     а не строки. Их нужно форматировать в `"%Y-%m-%d %H:%M:%S"`, `"%Y-%m-%d"` и `"%H:%M"`,
     потому что фронтенд ждёт строки;
   * ошибки триггеров (лимит и окно отмены) приходят как `psycopg.errors.CheckViolation`.
     Их надо ловить и возвращать как `{"success": False, "error": str(e.diag.message_primary)}`.
   * `init_db()` для Postgres ничего не создаёт: схему один раз создаёт `schema.sql`.
4. `get_storage_engine_name()` → `"Supabase PostgreSQL"`, чтобы это было видно на вкладке **System**.
5. Бэкапы: на Supabase физическую копию файла сделать нельзя. Кнопку **Create Backup** можно
   переделать на JSON-экспорт всех таблиц (`SELECT *` из каждой). Кроме того, Supabase сам делает
   ежедневные бэкапы на платных тарифах.

## 6. Проверка

1. Откройте `/api/health`, в поле `storage_engine` должно быть `Supabase PostgreSQL`.
2. Зарегистрируйтесь и забронируйте тренировку. В Supabase **Table Editor → bookings** должна
   появиться строка.
3. Попробуйте третью бронь, сервер должен её отклонить.
4. Чтобы проверить окно отмены, в SQL Editor «состарьте» бронь, затем нажмите Cancel в профиле.
   Отмена должна быть отклонена:
   ```sql
   update bookings set created_at = now() - interval '25 hours' where booking_code = 'BK-XXXXX';
   ```

## Альтернатива: Supabase Auth

Вместо своей таблицы `users` можно использовать встроенный **Supabase Auth**
(подтверждение email, сброс пароля, вход через Google). Но тогда токены выдаёт Supabase,
а Flask должен проверять их JWT. Для курсового проекта проще оставить текущую схему
(своя таблица + подписанные токены). Supabase Auth стоит упомянуть на защите как
следующий шаг развития.
