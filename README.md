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
|   - POST /api/book       -> Transactional Booking     |
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
|   - sessions table (SES-### format enforced)          |
|   - bookings table (Foreign Key to sessions.id)       |
|   - backup_logs table (Disaster Recovery Audit)       |
|   - backups/ directory (Physical DB Snapshots)        |
+-------------------------------------------------------+
```

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
1. Open the **"1. Member Booking"** tab.
2. Find session **`SES-101` (CrossFit Conditioning)**. Notice it has available spots (e.g. 4 free places).
3. Click **"Book Spot"**, enter your name and phone, then click **"Confirm Booking"**.
4. **Result:** Booking succeeds instantly. A confirmation ticket appears displaying your **unique booking code (e.g. `BK-88707`) and a scannable QR code**.
5. Close the ticket: notice the free seats on `SES-101` automatically decreased by 1.

### Test Case 2: Overbooking Prevention (FR-03 & TC-01)
1. In the schedule, locate session **`SES-103` (Boxing & Core Technique)**.
2. Notice the badge says **`10 of 10 booked • FULLY BOOKED`**.
3. Click the red button **"Test Overbooking (FR-03)"** and submit the booking.
4. **Result:** The system strictly rejects the request:  
   *`"Session SES-103 is fully booked (10/10 places taken). Acceptance criteria FR-03 enforced."`*

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
