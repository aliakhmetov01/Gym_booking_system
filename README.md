# Gym Booking System (Information System)

> **Course:** Basics of Information Systems (BIS)  
> **Milestone:** Weekly Project Status Check &rarr; Week 15 End-Term Working Information System  
> **Architecture:** 3-Tier Client / Server System (Browser Client &bull; REST API Server &bull; SQLite Relational Database)

---

## 👥 Team Members

| Student ID | Full Name | Project Role | Individual Focus Area |
| :--- | :--- | :--- | :--- |
| **25B032081** | **Akhmetov Ali** | **Lead Developer** | Backend REST API, validation logic (`SES-###`), and transactional locking |
| **24B031641** | **Amangeldiyev Dulat** | **Security & Access** | Authentication design, role permissions (`member` vs `receptionist`) |
| **25B031167** | **Bekzhan Aidar** | **Frontend & UI/UX** | Responsive schedule interface, capacity progress bars, modal flows |
| **25B032098** | **Damir Kabidulin** | **Database & Backup** | Relational schema design, SQLite engine, automated disaster recovery backups |
| **25B032080** | **Amirlan Akhmetzhan** | **QA & Hardware** | Acceptance testing (`TC-01`), QR code ticket generator, reception check-in |

---

## 🎯 Project Overview & Problem Solved

* **Target Stakeholder:** Gym Member & Reception Desk Staff.
* **The Problem:** Gym members struggle to view accurate seat availability in real time and reserve spots before training sessions fill up. Manual phone calls lead to overbooking, disputed records, and reception bottlenecks.
* **The Solution:** A unified digital web application where members can view real-time capacities, book spots instantly with QR-code receipts, and reception staff can verify arrivals with USB/Camera scanners.

---

## 🧩 Integration with Course Modules

| Course Week | Topic | How it is Implemented in this Project |
| :--- | :--- | :--- |
| **Week 4** | **Requirements & Rules** | **FR-01:** Dynamic schedule listing. <br>**FR-02:** Member booking. <br>**FR-03 & Business Rule:** Strict rejection of bookings when `booked_count == capacity` (overbooking prevention). |
| **Week 5** | **Hardware & Peripherals** | **QR / Barcode Scanner:** Receptionist tab allows checking in guests via booking codes and scanned QR codes. <br>**Plan B Resilience:** If thermal printers fail, confirmations remain preserved and readable on-screen. |
| **Week 6** | **Security & Ransomware** | **Automatic Data Backup:** Selected over firewalls. A dedicated backup engine takes physical SQLite snapshots with timestamped audit logs (`/api/backup`). |
| **Week 7** | **Data Governance & Quality** | **Mini Data Dictionary:** `UpcomingTrainingSessions` dataset. Enforces regex validation `^SES-\d{3}$` (Ali's task), non-null completeness, and foreign keys. |
| **Week 10** | **Telecommunications & Networks** | **Client-Server Architecture:** Demonstrates HTTP/HTTPS request/response packets, REST API, roundtrip database latency metrics, and failure recovery. |

---

## 🏗️ System Architecture

```
+-------------------------------------------------------+
|                 1. CLIENT LAYER                       |
|   Web Browser (HTML5, Tailwind CSS, Vanilla JS)       |
|   - Schedule View & Dynamic Capacity Bars             |
|   - Client Booking Form & Modal                       |
|   - QRCode.js Ticket Generator (Week 5)               |
+---------------------------+---------------------------+
                            |
                     HTTPS / JSON REST
                            |
+---------------------------v---------------------------+
|                 2. APPLICATION SERVER                 |
|   Flask / Python (Vercel Serverless & Local WSGI)     |
|   - GET  /api/sessions   -> Schedule & Free Seats     |
|   - POST /api/auth/register | /api/auth/login         |
|   - GET  /api/auth/me    -> Current member (token)    |
|   - POST /api/book       -> Transactional Booking     |
|   - GET  /api/my/bookings-> Member profile bookings   |
|   - POST /api/cancel     -> Cancel own booking (24h)  |
|   - POST /api/checkin    -> Reception Desk Check-in   |
|   - POST /api/backup     -> Disaster Recovery Engine  |
|   - GET  /api/health     -> Network Latency & Metrics |
+---------------------------+---------------------------+
                            |
                      Atomic Transactions
                            |
+---------------------------v---------------------------+
|                 3. DATA STORAGE LAYER                 |
|   SQLite Relational Database (gym.db)                 |
|   - users table (hashed passwords, unique email)      |
|   - sessions table (SES-### format enforced)          |
|   - bookings table (FK to sessions.id and users.id)   |
|   - backup_logs table (Disaster Recovery Audit)       |
|   - backups/ directory (Physical DB Snapshots)        |
+-------------------------------------------------------+
```

---

## 👤 Member Accounts & Booking Rules

* **Sign up / Log in** from the header. Passwords are stored only as salted hashes (`werkzeug.security`); the API issues a signed token (`itsdangerous`, valid 7 days) sent as `Authorization: Bearer <token>`.
* **My Bookings (profile):** account details, active-booking counter, every booking with its status, QR ticket and cancel button.
* **Demo account:** `demo@gym.kz` / `demo1234` (seeded automatically, owns booking `BK-10101`).

| Rule | Description | Enforced in |
| :--- | :--- | :--- |
| **FR-03** | No overbooking: `booked_count ≤ capacity` | API transaction + DB `CHECK` |
| **BR-04** | A member may hold at most **2 active (CONFIRMED) bookings** at the same time | `create_booking()` |
| **BR-05** | A booking can be cancelled only **within 24 hours after it was made**; cancelling frees the seat | `cancel_booking()` |
| **BR-06** | Only the owner can cancel a booking; cancelled bookings cannot be checked in | API + reception |
| **BR-07** | Staff functions (reception, session management, backups, system, demo reset) are available only to the **admin** role | API `require_admin` (HTTP 403) + hidden UI tabs |

### 🔥 Gamification: booking streak
* Every booking gives the member **one flame** (`streak + 1`).
* If the member makes **no new booking within 24 hours**, the streak is lost (back to 0).
* A cancelled booking takes its flame back, so book/cancel cannot be used to farm flames.
* The header shows the current streak; the profile shows the streak, the personal best and the time left to keep it.
* Why it matters: it rewards regular training, the main reason gym members drop out is irregular attendance.

### 🏆 Gamification: levels, XP and badges
XP and badges are **calculated from the booking history** (`compute_progress()` in `api/_database.py`), so no extra tables
are needed and the same logic works on SQLite, the serverless store and Supabase. Cancelled bookings give nothing.

| Action | XP |
| :--- | :--- |
| Booking (not cancelled) | +20 |
| Attended (checked in at reception) | +30 |
| Badge unlocked | +50 |

| Level | XP needed |
| :--- | :--- |
| 🌱 Rookie | 0 |
| 💪 Regular | 100 |
| 🏆 Athlete | 300 |

| Badge | How to unlock |
| :--- | :--- |
| 🎯 First Workout | Book your first session |
| 🔥 On Fire | Reach a streak of 5 bookings in a row |
| 🧭 Explorer | Try all 5 workout types (CrossFit, Boxing, Strength, Pilates, HIIT Cardio) |
| 🌅 Early Bird | Book 3 morning sessions (before 12:00) |

The header shows the member's level; **My Bookings** shows the XP progress bar to the next level, the level path and
every badge with its progress (e.g. `2/3`). The booking ticket shows the reward: `+70 XP`, *New badge*, *Level up!*.

### 👮 Roles
| Role | Sees | Account |
| :--- | :--- | :--- |
| Guest | Schedule only | — |
| Member | Schedule, My Bookings | Sign up, or demo `demo@gym.kz` / `demo1234` |
| Admin | Everything: Reception, Sessions (create new sessions), Backups, System, demo reset, overbooking test | `admin@gym.kz`, password from `ADMIN_PASSWORD` |

The admin account is created automatically. Its password comes from the `ADMIN_PASSWORD` environment variable;
only a purely local SQLite setup without that variable falls back to `admin1234`. On a public deployment
without `ADMIN_PASSWORD` no admin is created, so there is never a guessable admin login.

Set `SECRET_KEY` and `ADMIN_PASSWORD` in production (Vercel → Settings → Environment Variables).
For a persistent cloud database see [supabase/SUPABASE_SETUP.md](supabase/SUPABASE_SETUP.md).

---

## 🚀 How to Run Locally

### 1. Requirements
* Python 3.10+ (Python 3.13 supported)
* Web browser (Chrome, Edge, Firefox, Safari)

### 2. Setup & Execution
Clone the repository and run:

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the web server
python app.py
```

Open your browser at: **`http://localhost:5000`**

---

## 🧪 How to Demonstrate in Practical Class (7-Minute Presentation)

### Test Case 1: Standard Member Booking (FR-01 & FR-02)
1. Click **Sign up** in the header and create an account (or log in as `demo@gym.kz` / `demo1234`).
2. In the **Schedule** tab find session **`SES-102` (Strength: Upper Body Focus)**.
3. Click **"Book for 09:30"**, check your name in the dialog, then click **"Confirm Booking"**.
4. **Result:** Booking succeeds instantly. A confirmation ticket appears displaying your **unique booking code (e.g. `BK-88707`) and a scannable QR code**.
5. Close the ticket: the free seats on `SES-102` decreased by 1 and the card shows *You're booked*.

### Test Case 1b: Booking Streak (Gamification)
1. After Test Case 1 the header shows **🔥 1** and the ticket says *+1 flame*.
2. Book a second session: the streak becomes **🔥 2** (*personal best*).
3. Open **My Bookings**: the streak card shows the time left to keep the streak (24 hours from the last booking).

### Test Case 1c: Levels, XP and Badges (Gamification)
1. A new member books the morning session `SES-101`: the ticket shows **+70 XP** (20 for the booking + 50 for the
   **🎯 First Workout** badge) and the header shows **🌱 Rookie**.
2. Book `SES-102`, then as admin check in both bookings at **Reception** (+30 XP each).
3. Open **My Bookings**: the member is now **💪 Regular** (150 XP) with a progress bar towards 🏆 Athlete,
   and the badges show progress: *Early Bird 2/3*, *Explorer 2/5*.
4. Cancel a booking: its XP is removed (anti-farming).

### Test Case 2: Overbooking Prevention (FR-03 & TC-01)
1. Log in as the **admin**. In the schedule, locate session **`SES-106` (Boxing Sparring & Technique)**, shown as `8/8 booked • Full`.
   (Members simply see a disabled *Fully booked* button.)
2. Click the red button **"Full (Test Overbooking)"** and submit the booking.
3. **Result:** The system strictly rejects the request:  
   *`"Session SES-106 is fully booked (8/8 places taken). Acceptance criteria FR-03 enforced."`*

### Test Case 2b: Booking Limit (BR-04)
1. While logged in, book two different sessions.
2. Try to book a third one.
3. **Result:** *`"Booking limit reached: you already have 2 active bookings..."`* The profile counter shows `2 / 2`.

### Test Case 2c: Cancellation Window (BR-05)
1. Open **My Bookings** and click **Cancel** on a fresh booking. It becomes `CANCELLED` and the seat is released.
2. The demo booking `BK-10101` becomes non-cancellable 24 hours after the seed was created (*Cancel window closed*).

### Test Case 2d: Role-Based Access (BR-07)
1. Logged in as a member: only **Schedule** and **My Bookings** are visible.
2. Log out and log in as **admin**: **Reception, Sessions, Backups, System** appear, plus an *Admin* badge.
3. In **Sessions** create `SES-150` — it appears in the schedule immediately.

### Test Case 3: Reception Desk Check-in (Week 5)
1. As admin, switch to the **Reception** tab.
2. Enter the booking code from Test Case 1 into the input field and click **"Check in"**.
3. **Result:** Member status changes from `CONFIRMED` to `CHECKED_IN` (green badge).

### Test Case 4: Automatic Data Backup (Week 6)
1. As admin, switch to the **Backups** tab.
2. Click **"Create Backup Now"**.
3. **Result:** A snapshot is generated with a timestamp (SQLite copy locally, JSON export on Supabase) and logged in the audit table.

### Test Case 5: Architecture & Telecom Diagnostics (Week 10)
1. As admin, switch to the **System** tab.
2. Review real-time roundtrip database latency and protocol telemetry.

---

## ☁️ Deployment to Vercel

The project includes `vercel.json` configured for Vercel Serverless Functions:

1. Push this repository to **GitHub**.
2. Go to [vercel.com](https://vercel.com) &rarr; **Add New Project** &rarr; Select your GitHub repository.
3. Keep default settings and click **Deploy**.
4. Vercel will automatically build the Python backend (`api/index.py`) and serve the responsive frontend (`public/index.html`).

---

## 📜 License
Developed for educational evaluation in the **Basics of Information Systems** course, Fall 2026.
