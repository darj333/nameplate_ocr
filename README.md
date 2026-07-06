# Nameplate OCR Export

Upload photos of industrial equipment nameplates (motors, pumps, transformers, etc.), automatically extract all attribute/value pairs using OCR + Claude LLM, review and correct results, and export to CSV or Excel.

---

## Architecture

```
Browser (SPA)
      │
      ▼
FastAPI backend
      ├─► EasyOCR → raw text
      ├─► Groq API (llama-3.3-70b-versatile) → JSON {attribute: value}
      ├─► PostgreSQL (SQLAlchemy + Alembic)
      ├─► Railway Volume (/data/uploads) → image files
      └─► Export endpoints → CSV & Excel (pandas/openpyxl)
```

---

## Local Development

### Prerequisites

- Python 3.11+
- PostgreSQL running locally (or use Docker: `docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres`)
- A free Groq API key (sign up at https://console.groq.com — no credit card required)

### Setup

```bash
# 1. Clone and enter the project
cd nameplate_ocr_export

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and fill in DATABASE_URL and ANTHROPIC_API_KEY

# 5. Create upload directory (local run)
mkdir -p /data/uploads   # or set UPLOAD_DIR=./uploads in .env

# 6. Run database migrations
alembic upgrade head

# 7. Start the development server
uvicorn app.main:app --reload
```

Open http://localhost:8000 for the upload UI, or http://localhost:8000/docs for the interactive API docs.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/nameplate_ocr` | PostgreSQL connection string |
| `GROQ_API_KEY` | *(required)* | Free Groq API key from console.groq.com |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Model to use; `llama-3.1-8b-instant` is faster/lighter |
| `UPLOAD_DIR` | `/data/uploads` | Where uploaded images are stored |
| `MAX_UPLOAD_SIZE_MB` | `20` | Maximum upload file size |
| `OCR_LANGUAGES` | `en` | Comma-separated EasyOCR language codes |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/nameplates/upload` | Upload an image; returns `{id, status}` |
| `GET` | `/nameplates` | List all nameplates (paginated) |
| `GET` | `/nameplates/{id}` | Get one nameplate with extracted attributes |
| `PATCH` | `/nameplates/{id}/attributes` | Bulk upsert attributes (manual correction) |
| `DELETE` | `/nameplates/{id}/attributes/{attr_id}` | Delete one attribute row |
| `DELETE` | `/nameplates/{id}` | Delete a nameplate and all its attributes |
| `POST` | `/nameplates/{id}/reprocess` | Re-run OCR + LLM extraction |
| `GET` | `/export/csv?ids=1,2,3` | Export to CSV (all records if no ids given) |
| `GET` | `/export/xlsx?ids=1,2,3` | Export to Excel |

---

## Deploy on Railway

1. Push this repo to GitHub.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Add a **PostgreSQL** plugin — Railway sets `DATABASE_URL` automatically.
4. Add a **Volume** mounted at `/data`.
5. Set environment variables: `GROQ_API_KEY`, optionally `GROQ_MODEL` and `OCR_LANGUAGES`.
6. Railway picks up `railway.toml` and builds from the `Dockerfile`.
7. Migrations run automatically on each deploy via the start command.

---

## Project Structure

```
nameplate_ocr_export/
├── app/
│   ├── main.py              # FastAPI app, CORS, static mount
│   ├── config.py            # Pydantic settings (reads .env)
│   ├── database.py          # SQLAlchemy engine + session factory
│   ├── schemas.py           # Pydantic request/response models
│   ├── models/
│   │   └── nameplate.py     # ORM models: Nameplate, NameplateAttribute
│   ├── routers/
│   │   ├── nameplates.py    # Upload, list, get, patch, delete, reprocess
│   │   └── export.py        # CSV and Excel export
│   └── services/
│       ├── ocr.py           # EasyOCR wrapper (Tesseract fallback)
│       ├── llm.py           # Claude API structuring
│       └── pipeline.py      # Background task: OCR → LLM → DB
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_initial_schema.py
├── static/                  # Single-page frontend
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── alembic.ini
├── Dockerfile
├── railway.toml
├── requirements.txt
└── .env.example
```
