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

Set the `SECRET_KEY` environment variable in production (Vercel → Settings → Environment Variables).
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

### Test Case 2: Overbooking Prevention (FR-03 & TC-01)
1. In the schedule, locate session **`SES-106` (Boxing Sparring & Technique)**, shown as `8/8 booked • Full`.
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

### Test Case 3: Reception Desk Check-in (Week 5)
1. Switch to the **"2. Reception Desk"** tab.
2. Enter the booking code from Test Case 1 into the input field and click **"Confirm Check-in"**.
3. **Result:** Member status changes from `CONFIRMED` to `CHECKED_IN` (green badge).

### Test Case 4: Automatic Data Backup (Week 6)
1. Switch to the **"4. Security & Backup"** tab.
2. Click **"Create Snapshot Backup Now"**.
3. **Result:** A physical copy of `gym.db` is generated with a timestamp in the `backups/` directory and logged in the audit table.

### Test Case 5: Architecture & Telecom Diagnostics (Week 10)
1. Switch to the **"5. Architecture Map"** tab.
2. Review real-time roundtrip database latency (typically 1–3 ms) and protocol telemetry.

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
