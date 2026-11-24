-- =====================================================================
-- Gym Booking System — PostgreSQL schema for Supabase
-- Run once in: Supabase Dashboard -> SQL Editor -> New query -> Run
-- Mirrors api/_database.py (SQLite) and enforces the same business rules
-- directly in the database, so they hold even if the API has a bug.
-- =====================================================================

-- ---------- Members (accounts) ----------
create table if not exists public.users (
  id            bigint generated always as identity primary key,
  full_name     text        not null check (char_length(full_name) >= 2),
  email         text        not null unique,
  phone         text        not null,
  password_hash text        not null,           -- werkzeug hash, never plain text
  created_at    timestamptz not null default now(),
  role          text        not null default 'member' check (role in ('member', 'admin')),
  -- Gamification: +1 per booking, resets if no booking within 24 hours
  streak          integer   not null default 0,
  best_streak     integer   not null default 0,
  last_booking_at timestamptz
);
-- Note: an existing database created with the first version of this file gets the
-- role / streak columns automatically when the app starts (api/_pg.py -> _migrate).
-- The admin account (admin@gym.kz) is created by the app from the ADMIN_PASSWORD variable.

-- ---------- Training sessions (Week 7: SES-### rule) ----------
create table if not exists public.sessions (
  id           bigint generated always as identity primary key,
  session_code text        not null unique check (session_code ~ '^SES-[0-9]{3}$'),
  title        text        not null,
  trainer      text        not null,
  session_date date        not null,
  session_time time        not null,
  capacity     int         not null check (capacity > 0),
  booked_count int         not null default 0,
  category     text        not null default 'Fitness',
  created_at   timestamptz not null default now(),
  -- FR-03: no overbooking
  constraint booked_within_capacity check (booked_count >= 0 and booked_count <= capacity)
);

-- ---------- Bookings ----------
create table if not exists public.bookings (
  id           bigint generated always as identity primary key,
  booking_code text        not null unique check (booking_code ~ '^BK-[0-9]{5}$'),
  session_id   bigint      not null references public.sessions(id) on delete cascade,
  user_id      bigint      references public.users(id) on delete set null,
  member_name  text        not null,
  member_phone text        not null,
  status       text        not null default 'CONFIRMED'
                           check (status in ('CONFIRMED', 'CHECKED_IN', 'CANCELLED')),
  created_at   timestamptz not null default now(),
  cancelled_at timestamptz,
  source       text        not null default 'DIRECT'   -- 'WAITLIST' = spot given automatically from the queue
                           check (source in ('DIRECT', 'WAITLIST'))
);

-- ---------- Smart waitlist: queue for full sessions (first come, first served) ----------
create table if not exists public.waitlist (
  id         bigint generated always as identity primary key,
  session_id bigint      not null references public.sessions(id) on delete cascade,
  user_id    bigint      not null references public.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (session_id, user_id)
);
create index if not exists waitlist_queue_idx on public.waitlist (session_id, created_at, id);
-- (An existing database gets bookings.source and the waitlist table automatically
--  when the app starts: api/_pg.py -> _migrate.)

-- One active booking per member per session
create unique index if not exists bookings_one_active_per_session
  on public.bookings (session_id, user_id)
  where status <> 'CANCELLED';

create index if not exists bookings_user_idx on public.bookings (user_id);

-- ---------- Backup audit log (Week 6) ----------
create table if not exists public.backup_logs (
  id           bigint generated always as identity primary key,
  filename     text        not null,
  file_path    text        not null,
  size_bytes   bigint      not null,
  created_at   timestamptz not null default now(),
  trigger_type text        not null default 'MANUAL'
);

-- =====================================================================
-- BR-04: max 2 active (CONFIRMED) bookings per member
-- =====================================================================
create or replace function public.enforce_booking_limit()
returns trigger
language plpgsql
as $$
begin
  if new.status = 'CONFIRMED' and new.user_id is not null then
    perform pg_advisory_xact_lock(new.user_id);  -- serialize concurrent bookings of one member
    if (select count(*) from public.bookings
        where user_id = new.user_id and status = 'CONFIRMED' and id <> new.id) >= 2 then
      raise exception 'Booking limit reached: max 2 active bookings per member'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_booking_limit on public.bookings;
create trigger trg_booking_limit
  before insert or update of status on public.bookings
  for each row execute function public.enforce_booking_limit();

-- =====================================================================
-- BR-05: a booking can be cancelled only within 24 hours after creation
-- =====================================================================
create or replace function public.enforce_cancel_window()
returns trigger
language plpgsql
as $$
begin
  if new.status = 'CANCELLED' and old.status <> 'CANCELLED' then
    if old.status <> 'CONFIRMED' then
      raise exception 'Only CONFIRMED bookings can be cancelled' using errcode = 'check_violation';
    end if;
    if now() > old.created_at + interval '24 hours' then
      raise exception 'Cancellation window closed (24 hours after booking)' using errcode = 'check_violation';
    end if;
    new.cancelled_at := now();
  end if;
  return new;
end;
$$;

drop trigger if exists trg_cancel_window on public.bookings;
create trigger trg_cancel_window
  before update of status on public.bookings
  for each row execute function public.enforce_cancel_window();

-- =====================================================================
-- Security: the Flask server connects with the database password
-- (bypasses RLS). Enabling RLS with no policies blocks the public
-- anon/authenticated REST keys from reading users/password hashes.
-- =====================================================================
alter table public.users       enable row level security;
alter table public.sessions    enable row level security;
alter table public.bookings    enable row level security;
alter table public.backup_logs enable row level security;
alter table public.waitlist    enable row level security;

-- =====================================================================
-- Seed data (demo account password: demo1234)
-- Generate the hash locally:
--   python -c "from werkzeug.security import generate_password_hash as h; print(h('demo1234'))"
-- and paste it instead of <PASTE_HASH_HERE>.
-- =====================================================================
-- insert into public.users (full_name, email, phone, password_hash)
-- values ('Aidar Bekzhan', 'demo@gym.kz', '+7 701 555 1234', '<PASTE_HASH_HERE>');

insert into public.sessions (session_code, title, trainer, session_date, session_time, capacity, booked_count, category) values
  ('SES-101', 'CrossFit Morning Blast',        'Daniyar Bolat',    current_date,     '08:30', 12, 0, 'CrossFit'),
  ('SES-102', 'Strength: Upper Body Focus',    'Timur Aliyev',     current_date,     '09:30', 10, 0, 'Strength'),
  ('SES-103', 'Boxing Fundamentals & Bagwork', 'Alexey Smirnov',   current_date,     '10:30', 10, 0, 'Boxing'),
  ('SES-104', 'Pilates Core & Flexibility',    'Aruzhan Sadykova', current_date,     '11:30', 15, 0, 'Pilates'),
  ('SES-105', 'HIIT Cardio Extreme',           'Daniyar Bolat',    current_date,     '13:00', 16, 0, 'HIIT Cardio'),
  ('SES-106', 'Boxing Sparring & Technique',   'Alexey Smirnov',   current_date,     '15:00',  8, 8, 'Boxing'),
  ('SES-112', 'Morning HIIT Cardio',           'Aruzhan Sadykova', current_date + 1, '09:00', 16, 0, 'HIIT Cardio'),
  ('SES-113', 'Strength: Deadlift & Squat',    'Timur Aliyev',     current_date + 1, '11:00',  8, 0, 'Strength')
on conflict (session_code) do nothing;
