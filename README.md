# Dream Enterprise AI Hub

An autonomous, multi-model Enterprise AI platform integrated with Oracle Autonomous Database (ORDS), live meteorological intelligence, automated barcode/QR vision decoding, Google Drive cloud backup, and multi-table document export (PDF, Excel, Word, PowerPoint, CSV, JSON).

---

## 📁 Two-Folder Project Architecture (`client/` & `server/`)

The codebase is organized into decoupled frontend and backend layers:

```
AI Hub Root/
│
├── 🚀 start_hub.bat                 <-- One-Click Master Launcher (starts server & opens browser)
│
├── 📂 client/                       <-- Frontend Presentation Layer
│   ├── index.html                   <-- Modern AI Chat Interface (Themes, KPIs, ECharts, Google Weather)
│   └── static/
│       ├── css/                     <-- Responsive stylesheet overrides
│       ├── js/                      <-- Export engines (export_pdf.js, export_excel.js, export_word.js, export_ppt.js)
│       ├── lib/                     <-- Standalone vendor libraries (docx.js, jszip.min.js, pptxgen.bundle.js)
│       ├── fonts/                   <-- Custom Unicode & Arabic typography (Amiri-Regular.ttf)
│       ├── icons/                   <-- PWA application icons & touch badges
│       ├── manifest.json            <-- Progressive Web App manifest
│       └── sw.js                    <-- Service Worker offline shell
│
└── 📂 server/                       <-- Backend Engine Layer
    ├── ui_app.py                    <-- Core FastAPI Enterprise Server & Chat Orchestrator
    ├── start_server.bat             <-- Direct server launcher (starts uvicorn on port 8000)
    ├── requirements.txt             <-- Python package dependencies
    ├── app_config.json              <-- System configurations & upload limits (10MB image, 25MB doc)
    ├── .env.example                 <-- Environment variable template
    ├── agent/                       <-- Intelligent AI & Storage Modules
    │   ├── query_engine.py          <-- Safe in-memory query execution & KPI computation
    │   ├── barcode_helper.py        <-- High-speed zxing-cpp Barcode & QR Code decoding engine
    │   ├── weather_helper.py        <-- Open-Meteo live weather & timeline generator
    │   └── gdrive_storage.py        <-- Google Drive automated OAuth & Cloud One backup
    └── db/                          <-- Oracle Database & ORDS REST Handler SQL Definitions
        ├── 01_upgrade_safe.sql
        ├── 02_security_lockdown.sql
        ├── 03_providers_apikey_fix.sql
        ├── 04_fix_providers_handler.sql
        └── 05_chat_attachments.sql
```

---

## ⚡ How to Run

### Method 1: One-Click Master Launcher (Recommended)
Double-click `start_hub.bat` in the root folder. It will:
1. Check for Python 3.10+ in PATH.
2. Automatically switch into the `server/` directory.
3. Verify and install dependencies from `requirements.txt`.
4. Launch the Uvicorn server on `http://127.0.0.1:8000`.
5. Open your default web browser to the chat application.

### Method 2: Direct Server Launch
Open the `server/` folder and double-click `start_server.bat`.

---

## 📊 Scale & Enterprise Standards (1M+ Records)

### Do I need to change my SQL queries in Oracle?
* **Auto-REST Tables (`ORDS.ENABLE_OBJECT`)**: **NO.** Only the agent backend code needs to be updated. ORDS natively translates URL filter/sort parameters (`?order=col:desc&limit=10`) into `ORDER BY ... FETCH FIRST N ROWS` inside the database kernel.
* **Custom Static Handlers (`ORDS.DEFINE_HANDLER` with static `SELECT * FROM table`)**:
  * **Option A (Recommended)**: Use Oracle's REST-Enabled SQL Service (`/_/sql`), where the agent writes the SQL statement dynamically and executes it across all 1M+ rows.
  * **Option B**: Add parameter placeholders (`ORDER BY :orderby`) to the existing ORDS handler.
