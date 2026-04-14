#!/usr/bin/env bash
# Yerel geliştirme: Docker yok. Önkoşullar: PostgreSQL 15+ + pgvector, Node 20+, Python 3.12+, Ollama (opsiyonel ama sohbet için gerekli).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "Hata: $*" >&2; exit 1; }

[[ -f "$ROOT/.env" ]] || die "Project/.env yok. Örnek: cp .env.example .env"

echo "== 1/5 Python sanal ortam ve pip =="
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck source=/dev/null
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "== 2/5 npm (frontend) =="
cd "$ROOT/frontend"
npm install

echo "== 3/5 Veritabanı bağlantısı ve migrate =="
cd "$ROOT/backend"
source .venv/bin/activate
python manage.py migrate --noinput

echo "== 4/5 Ollama =="
if curl -sf "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  echo "Ollama erişilebilir (127.0.0.1:11434)."
else
  echo "Uyarı: Ollama yanıt vermiyor. Sohbet için https://ollama.com kurun; sonra: ollama serve ve ollama pull llama3.2:3b"
fi

echo "== 5/5 RAG indeks özeti =="
python manage.py rag_stats

echo ""
echo "Sunucuları ayrı terminallerde başlatın:"
echo "  A) cd \"${ROOT}/backend\" && source .venv/bin/activate && python manage.py runserver"
echo "  B) cd \"${ROOT}/frontend\" && npm run dev"
echo ""
echo "Arayüz: http://localhost:3000/chat  |  API: http://localhost:8000/api/schema/"
echo ""
cd "$ROOT/backend"
source .venv/bin/activate
PAGES=$(python manage.py shell -c "from core.models import Page; print(Page.objects.count())")
CHUNKS=$(python manage.py shell -c "from core.models import DocumentChunk; print(DocumentChunk.objects.count())")
if [[ "${PAGES:-0}" -eq 0 ]] || [[ "${CHUNKS:-0}" -eq 0 ]]; then
  echo "RAG boş; örnek doldurma:"
  echo "  cd \"${ROOT}/backend\" && source .venv/bin/activate && python manage.py refresh_rag --max-pages 60 --depth 2"
fi
