# ACU Smart Assistant

Django API, Next.js chat UI, and RAG over crawled university pages using **PostgreSQL + pgvector** and local embeddings.

## Requirements

- Python 3.12+
- Node.js 20+ (for Next.js)
- PostgreSQL **15+** with the **pgvector** extension (Docker image `pgvector/pgvector:pg15` or a local install with `CREATE EXTENSION vector`)
- Optional: [Ollama](https://ollama.com/) for local LLM inference

## Geliştirme ortamı kurulumu

Python sanal ortamı için `backend/.venv` kullanın.

1. Create and activate virtual environment:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install backend dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Install frontend dependencies:

   ```bash
   cd ../frontend
   npm install
   ```

4. Return to backend for migrations and API run:

   ```bash
   cd ../backend
   python manage.py migrate
   python manage.py runserver
   ```

## Quick start

1. Clone the repo and enter the project directory.

2. Copy environment template and adjust values (never commit `.env`):

   ```bash
   cp .env.example .env
   ```

3. Install dependencies:

   ```bash
   cd backend && pip install -r requirements.txt
   cd ../frontend && npm install
   ```

4. Run migrations:

   ```bash
   cd ../backend && python manage.py migrate
   ```

5. Load corpus and build embeddings (one command):

   ```bash
   python manage.py refresh_rag --max-pages 60 --depth 2
   ```

   Operational details, manual crawl → embed order, and **`refresh_rag` data-deletion defaults**: see [RAG_VERI_PIPELINE_REHBERI.md](RAG_VERI_PIPELINE_REHBERI.md).

   Or inspect index stats:

   ```bash
   python manage.py rag_stats
   ```

6. Start services:

   ```bash
   # Terminal 1 — API
   cd backend && python manage.py runserver

   # Terminal 2 — UI (set NEXT_PUBLIC_API_URL in frontend/.env.local if needed)
   cd frontend && npm run dev
   ```

## Database

Pick **one** of the paths below (Docker, bare Postgres, or host-only dev).

### A) Docker Compose (full stack)

From the project root:

```bash
docker compose up -d
```

Use `.env` so that **host** tools (optional `manage.py` on the machine) point at the published DB port (often `5433` → set `POSTGRES_PORT=5433`, `POSTGRES_HOST=localhost`). Containers use `POSTGRES_HOST=db` and port `5432` internally.

### B) Local PostgreSQL + pgvector

Install Postgres and pgvector, create a database and role, then:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Set `POSTGRES_HOST=localhost` and `POSTGRES_PORT=5432` (or your local port) in `.env`.

### C) Local development without Docker (typical dev workflow)

Use this when you run Django and Next.js on the host and only use local services (no `docker compose`).

1. **PostgreSQL + pgvector** — Postgres must have the `vector` extension (`CREATE EXTENSION vector;` on your database). This project is PostgreSQL-only; SQLite is **not supported**.

2. **Env file** — From the **project root** (`Project/`), run `cp .env.example .env`. Django loads **`Project/.env`** (not `backend/.env`). Match `POSTGRES_*` to your local DB. For Ollama on the same machine, set:
   - `OLLAMA_BASE_URL=http://127.0.0.1:11434`
   - `OLLAMA_MODEL=llama3.2:3b` (or uncomment / add these lines if they are commented in your copy)

3. **Ollama** — Install from [ollama.com](https://ollama.com), start it (macOS menu app or `ollama serve`), then once: `ollama pull llama3.2:3b`.

4. **Backend** — `cd backend && pip install -r requirements.txt && python manage.py migrate && python manage.py runserver`

5. **RAG index (first time or after crawl changes)** — `cd backend && python manage.py refresh_rag --max-pages 60 --depth 2` (or `rag_stats` to inspect). See [RAG_VERI_PIPELINE_REHBERI.md](RAG_VERI_PIPELINE_REHBERI.md) before running `refresh_rag` without `--keep-existing` (it clears existing pages/chunks by default).

6. **Frontend** — Ensure `frontend/.env.local` contains `NEXT_PUBLIC_API_URL=http://localhost:8000`, then `cd frontend && npm install && npm run dev`.

Open the Next.js URL (usually http://localhost:3000). The browser talks to Django on port 8000 via `NEXT_PUBLIC_API_URL`.

## Crawling `acibadem.edu.tr`

[`scrape_acibadem`](backend/core/management/commands/scrape_acibadem.py) uses **requests + BeautifulSoup** by default. If the extracted text is too short (typical for JS-rendered pages such as Bologna-style catalogs), it falls back once to **Selenium** with headless Chrome—so **Chrome or Chromium** must be installed on the machine that runs the command (`selenium` is listed in `backend/requirements.txt`).

Example:

```bash
cd backend && python manage.py scrape_acibadem --crawl --max-pages 40 --delay 1.5
```

For end-to-end crawl → chunk → embed, use `refresh_rag` (see Quick start) or the pipeline notes in [RAG_VERI_PIPELINE_REHBERI.md](RAG_VERI_PIPELINE_REHBERI.md).

## Environment variables

See [`.env.example`](.env.example) for all options: DB credentials, Ollama/Claude, `EMBEDDING_MODEL`, `RAG_TOP_K`, `RAG_MAX_DISTANCE`, and frontend `NEXT_PUBLIC_*` URLs.

## What not to commit

- `.env` (secrets and machine-specific ports)
- Python `venv` / `.venv`
- `frontend/node_modules`, `frontend/.next`

## License

Add your license here if you publish the repository publicly.
