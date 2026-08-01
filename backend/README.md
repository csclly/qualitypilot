# QualityPilot FastAPI backend

## Local development in WSL

From the backend directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
python -m pip install --upgrade pip
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

The PostgreSQL container must be running first:

```bash
cd ~/projects/qualitypilot
docker compose up -d
```

Open the API documentation at <http://localhost:8000/docs>.

## Initial endpoints

- `GET /api/v1/health`: process health check
- `GET /api/v1/ready`: process + database readiness check
- `POST /api/v1/knowledge/documents`: create a knowledge-document record
- `GET /api/v1/knowledge/documents`: list knowledge-document records

The application creates the initial tables and enables the `vector` extension
on startup for local development. Production deployments should later replace
this with versioned Alembic migrations.
