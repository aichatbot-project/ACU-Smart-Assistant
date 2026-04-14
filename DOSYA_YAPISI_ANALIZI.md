# Dosya Yapisi Analizi

Bu dokuman, projenin mevcut klasor yapisini hizlica anlamak ve gelistirme surecinde yol gosterici bir referans sunmak icin hazirlanmistir.

## Genel Mimari

Proje temel olarak iki ana katmana ayriliyor:

- `backend/`: Django tabanli API ve is kurallari
- `frontend/`: Next.js tabanli kullanici arayuzu

Ek olarak kok seviyede ortak yapilandirma ve dokumantasyon dosyalari bulunuyor:

- `.env.example`: Ortam degiskenleri sablonu
- `docker-compose.yml`: Servis orkestrasyonu (4 konteyner)
- `scripts/`: Yardimci scriptler (Ollama entrypoint)
- `pyrightconfig.json`: Python tip denetimi ayarlari
- Dokumantasyon: `README.md`, `ARCHITECTURE.md`, `RAG_ANALIZ.md`, `RAG_ANALİZ2.md`, `RAG_VERI_PIPELINE_REHBERI.md`

## Tam Dosya Agaci

```
ACU-Smart-Assistant/
├── .env.example                    # Ortam degiskenleri sablonu
├── .gitignore                      # Git disinda tutulan dosyalar
├── docker-compose.yml              # 4 servis: ollama, db, backend, frontend
├── pyrightconfig.json              # Python tip denetimi
├── scripts/
│   └── ollama-entrypoint.sh        # Ollama baslatma ve model indirme
│
├── backend/
│   ├── Dockerfile                  # Python 3.12-slim imaji
│   ├── .dockerignore               # Docker build disinda tutulanlar
│   ├── manage.py                   # Django yonetim giris noktasi
│   ├── requirements.txt            # Python bagimliliklari
│   │
│   ├── config/                     # Django proje yapilandirmasi
│   │   ├── __init__.py
│   │   ├── settings.py             # DB, CORS, CSRF, installed apps
│   │   ├── urls.py                 # Ana URL routing (admin + chat API)
│   │   ├── asgi.py                 # ASGI giris noktasi
│   │   └── wsgi.py                 # WSGI giris noktasi
│   │
│   ├── chat/                       # Sohbet uygulamasi (moduler yapi)
│   │   ├── __init__.py
│   │   ├── apps.py                 # Django app config
│   │   ├── models.py               # ChatSession, ChatMessage modelleri
│   │   ├── admin.py                # Django admin kaydi
│   │   ├── urls.py                 # Chat API route tanimlari
│   │   ├── views.py                # Uyumluluk katmani (re-export)
│   │   ├── completion_views.py     # POST /api/chat/ endpoint
│   │   ├── session_views.py        # GET/DELETE /api/chat/sessions/ endpoint
│   │   ├── chat_logic.py           # Is mantigi: session, mesaj, RAG koordinasyonu
│   │   ├── rag_service.py          # RAG sorgu hazirlama ve prompt olusturma
│   │   ├── llm_service.py          # LLM saglayici soyutlamasi (Ollama/Claude)
│   │   ├── message_utils.py        # UUID ayristirma, mesaj kirpma yardimcilari
│   │   ├── tests.py                # Chat endpoint testleri
│   │   └── migrations/             # Veritabani migration dosyalari
│   │
│   └── core/                       # RAG ve ortak domain mantigi
│       ├── __init__.py
│       ├── apps.py                 # Django app config
│       ├── models.py               # Page, DocumentChunk (pgvector) modelleri
│       ├── admin.py                # Django admin kaydi (Page, DocumentChunk)
│       ├── views.py                # (bos — ileride API endpoint eklenebilir)
│       ├── embeddings.py           # Embedding modeli yukle, chunk_text, embed_texts
│       ├── rag_config.py           # RAG parametreleri (env'den okunur)
│       ├── rag_keywords.py         # Anahtar kelime cikarma ve intent tespiti
│       ├── rag_retrieval.py        # Cok adimli RAG pipeline (vektor arama + rerank)
│       ├── tests.py                # Chunking, URL, scraper testleri (40 test)
│       ├── migrations/             # Veritabani migration dosyalari
│       └── management/
│           └── commands/
│               ├── scrape_acibadem.py        # ACU web scraper (BFS crawler)
│               ├── build_page_embeddings.py  # Sayfalari chunk'la ve embedding olustur
│               ├── refresh_rag.py            # Uctan uca: scrape → embed
│               ├── rag_stats.py              # Corpus istatistikleri
│               ├── rag_diagnose_coverage.py  # RAG kapsam analizi
│               └── rag_index_audit.py        # Vektor index sagligi denetimi
│
├── frontend/
│   ├── Dockerfile                  # Node.js 22 imaji
│   ├── .dockerignore               # Docker build disinda tutulanlar
│   ├── .gitignore                  # Git disinda tutulanlar
│   ├── package.json                # NPM bagimliliklari ve scriptler
│   ├── tsconfig.json               # TypeScript ayarlari (strict mode)
│   ├── next.config.ts              # Next.js yapilandirmasi
│   ├── postcss.config.mjs          # PostCSS (Tailwind CSS)
│   ├── eslint.config.mjs           # ESLint ayarlari
│   ├── README.md                   # Frontend dokumantasyonu
│   │
│   ├── app/                        # Next.js App Router
│   │   ├── layout.tsx              # Ana yerlesim (fontlar, metadata)
│   │   ├── page.tsx                # Giris sayfasi → /chat yonlendirmesi
│   │   ├── globals.css             # Global stiller, animasyonlar
│   │   └── chat/
│   │       └── page.tsx            # Ana sohbet ekrani (614+ satir)
│   │
│   └── public/                     # Statik varliklar
│       ├── logo.svg                # ACU logosu
│       └── kampus.png.webp         # Kampus arka plan gorseli
│
└── docs/                           # Dokumantasyon (kok seviye)
    ├── ARCHITECTURE.md             # Mimari genel bakis
    ├── RAG_ANALIZ.md               # RAG analiz raporu
    ├── RAG_ANALİZ2.md              # RAG analiz raporu (devam)
    └── RAG_VERI_PIPELINE_REHBERI.md # RAG veri pipeline rehberi
```

> Not: docs/ klasoru aslinda kok seviyede yer aliyor; yukaridaki agacta mantiksal gruplama icin gosterilmistir.

## Katman Bazli Sorumluluklar

### Backend — HTTP Katmani (`chat/`)

| Dosya | Sorumluluk |
|-------|-----------|
| `completion_views.py` | POST /api/chat/ endpoint'ini kabul eder |
| `session_views.py` | Session listeleme, detay ve silme endpoint'leri |
| `views.py` | Geriye uyumluluk icin re-export (urls.py'nin import ettigi isimler) |
| `urls.py` | `/api/chat/` altindaki route tanimlari |

### Backend — Is Mantigi Katmani (`chat/`)

| Dosya | Sorumluluk |
|-------|-----------|
| `chat_logic.py` | Ana koordinator: istek ayristirma, gecmis yukleme, RAG/LLM cagirma, DB yazma |
| `rag_service.py` | RAG sorgu zenginlestirme, smalltalk tespiti, prompt olusturma |
| `llm_service.py` | Ollama ve Claude API soyutlamasi, cikti temizleme |
| `message_utils.py` | UUID dogrulama, mesaj uzunluk kirpma |

### Backend — RAG Katmani (`core/`)

| Dosya | Sorumluluk |
|-------|-----------|
| `rag_retrieval.py` | Cok adimli pipeline: embedding → vektor arama → rerank → esik filtreleme → baglam olusturma |
| `rag_config.py` | Tum RAG parametreleri (.env'den okunur, varsayilan degerlerle) |
| `rag_keywords.py` | Anahtar kelime cikarma, STEM/fakulte intent tespiti, boost terimleri |
| `embeddings.py` | SentenceTransformer modeli yukleme, metin parcalama, vektor olusturma |

### Backend — Veri Katmani

| Dosya | Sorumluluk |
|-------|-----------|
| `chat/models.py` | ChatSession, ChatMessage — sohbet veri modelleri |
| `core/models.py` | Page, DocumentChunk — RAG veri modelleri (pgvector) |

### Backend — Management Komutlari

| Komut | Sorumluluk |
|-------|-----------|
| `scrape_acibadem` | ACU web sitesini tara, sayfalari DB'ye kaydet |
| `build_page_embeddings` | Sayfa iceriklerini chunk'la ve embedding olustur |
| `refresh_rag` | Uctan uca pipeline: scrape → embed |
| `rag_stats` | Corpus istatistiklerini raporla |
| `rag_diagnose_coverage` | RAG kapsam bosluk analizi |
| `rag_index_audit` | Vektor index sagligi denetimi |

### Frontend

| Dosya | Sorumluluk |
|-------|-----------|
| `app/layout.tsx` | Uygulama kabugu: fontlar (Geist, Syne), metadata |
| `app/page.tsx` | Giris noktasi, `/chat`'e yonlendirir |
| `app/chat/page.tsx` | Ana sohbet ekrani: mesajlasma, session yonetimi, RAG kaynak onizleme |
| `app/globals.css` | Tailwind CSS, animasyonlar (fade-in, float, glow-pulse) |

## Yapilacaklar (Aksiyon Plani)

### Tamamlanan Adimlar

- [x] `chat/views.py` moduerlestirme — `completion_views.py`, `session_views.py`, `chat_logic.py`, `rag_service.py`, `llm_service.py`, `message_utils.py` olarak ayrildi
- [x] Tek sanal ortam standardi — `backend/.venv` kullanilacak
- [x] `.gitignore` guncellendi — `.next/`, `node_modules/`, `__pycache__/`, `.DS_Store` disarida
- [x] `README.md` guncellendi — kurulum adimlari netlestirildi
- [x] `ARCHITECTURE.md` olusturuldu — katmanlar, veri akisi, RAG pipeline, API specleri
- [x] Test altyapisi eklendi — `core/tests.py` (40 test), `chat/tests.py`
- [x] RAG pipeline dokumante edildi — `ARCHITECTURE.md` icinde detayli akis diyagrami
- [x] Vektor index eklendi — HNSW index (cosine ops) DocumentChunk.embedding uzerinde

### Devam Eden / Planlanan Adimlar

- [ ] Kod sahipligi ve klasor bazli sorumluluklari dokumante et
- [ ] Teknik borc listesi olustur ve onceliklendirme matrisi belirle
- [ ] CI adimlarina (lint/test/build) kalite kapilari ekleyip zorunlu hale getir
- [ ] Uretim ortami icin guvenlik iyilestirmeleri (HTTPS, rate limiting, kimlik dogrulama)

## Ozet

Proje, tam yiginli (Django + Next.js + PostgreSQL + Ollama) bir sohbet/RAG uygulamasi icin saglam bir temel sunuyor. Backend modullestirmesi tamamlanmis, RAG pipeline cok adimli (embedding → vektor arama → rerank → baglam olusturma) bir yapiya sahip. Siradaki adimlar: CI/CD, guvenlik iyilestirmeleri ve teknik borc yonetimi.
