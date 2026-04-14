# Yayına alma rehberi (yeni başlayanlar için)

Bu sayfa, projeyi **internette çalışır hale getirme** sürecini uçtan uca anlatır. Teknik terimleri mümkün olduğunca sade kullandım; yine de ilk bölümde kısa bir “sözlük” var.

---

## 0a. En basit otomatik deploy (önerilen başlangıç)

**AWS ECR / IAM şart değil.** GitHub’da yeterli olanlar:

- **Secrets:** `SSH_PRIVATE_KEY`, `EC2_HOST`, `EC2_USER`
- **Variable:** `EC2_DEPLOY_PATH` = sunucuda repo klasörü (örn. `/home/ubuntu/Project`)

Akış: `main`’e push → CI yeşil → iş akışı **[`.github/workflows/deploy-ec2-simple.yml`](.github/workflows/deploy-ec2-simple.yml)** EC2’ye bağlanır, `git pull` + `docker compose -f docker-compose.prod.yml up -d --build` çalıştırır.

Sunucuda: bir kez `git clone`, `.env.production`, `NEXT_PUBLIC_API_URL` için kök `.env`, özel repoda **pull** izni (deploy key vb.).

İleri seviye **ECR + imaj push** için **[`deploy-aws.yml`](.github/workflows/deploy-aws.yml)** yalnızca **manuel** (Actions → Run workflow) çalışır; otomatik tetiklenmez, karışıklık olmaz.

---

## 0. Bu projenin canlı adresi

Üretim alan adı: **`www.acusmartassistant.com.tr`** (kök **`acusmartassistant.com.tr`** için de DNS yönlendirmesi ve `ALLOWED_HOSTS` içinde ikisini tanımlayın).  
HTTPS kurulunca `NEXT_PUBLIC_API_URL`, `CSRF_TRUSTED_ORIGINS_EXTRA` ve `CORS_ALLOWED_ORIGINS_EXTRA` değerlerinin **`https://www...`** ve gerekiyorsa **`https://acusmartassistant.com.tr`** olması gerekir. Ayrıntılı env örneği: [`deploy/env.production.example`](deploy/env.production.example).

---

## 1. Önce şunu anlayalım: sistem nasıl işliyor?

Projede üç parça var:

1. **Backend (Django)** — API, veritabanı konuşması, yapay zekâya istek.
2. **Frontend (Next.js)** — Kullanıcının gördüğü web arayüzü.
3. **Veritabanı (PostgreSQL + pgvector)** — Veriler burada durur.

Yerelde `docker compose` ile bunları bir arada çalıştırıyorsunuz. **Internet’e açarken** benzer mantık geçer: bu parçaları bulutta ayrı servisler veya konteynerler olarak kurarsınız; kullanıcılar genelde tek bir adresten (ör. `https://siteniz.com`) siteye girer, arka planda istekler doğru yere yönlendirilir.

**GitHub’ın rolü:** Kodunuz GitHub’da durur. Her `main` branch’e gönderimde testler çalışır (CI). İsterseniz otomatik olarak projeyi **Docker imajına** çevirip AWS’deki **ECR** adlı “özel Docker kayıt defterine” yükleyen bir adım da vardır (deploy workflow’u).

**Sizin yapacaklarınızın özeti:** AWS hesabında veritabanı + uygulamayı çalıştıracak ortam + (çok önerilir) alan adı ve güvenli bağlantı (HTTPS) kurmak. Bunların çoğu tıklamalı konsol işleri veya ayrı bir “altyapı kodu” (Terraform vb.); bu repo içinde hepsi otomatik değildir.

---

## 2. Kısa sözlük

| Terim | Ne demek? |
|--------|-----------|
| **Deploy / yayına alma** | Uygulamanızı geliştirme bilgisayarından çıkarıp kullanıcıların erişebileceği sunucu ortamına koymak. |
| **Docker imajı** | Uygulamanızı çalıştırmak için paketlenmiş “kutu”; içinde kod + gereken kütüphaneler var. |
| **ECR** | AWS’nin Docker imajlarını sakladığı yer. GitHub’daki deploy iş akışı imajları buraya iter. |
| **CI** | Sürekli entegrasyon: push yapınca test ve lint çalışır. |
| **RDS** | AWS’nin yönetilen PostgreSQL (veya başka veritabanı) hizmeti. |
| **ECS / Fargate** | Konteynerleri AWS’de çalıştırmanın yaygın yollarından biri (bu projede CD, isteğe bağlı olarak ECS servisini yenilemeye uygun). |
| **Migrasyon** | Veritabanı tablolarını Django modellerine göre güncellemek (`migrate`). İlk kurulumda mutlaka bir kez yapılır. |
| **OIDC** | GitHub Actions’ın AWS’ye şifre paylaşmadan, kısa süreli yetkiyle bağlanma yöntemi (tercih edilir). |

---

## 3. İki farklı yol (hangisi sizin için?)

### Yol A — Sadece “prod’a benzeyen” deneme (bilgisayarımda)

AWS kullanmadan, üretim imajlarıyla denemek isterseniz:

1. `deploy/env.production.example` dosyasını okuyun.
2. Proje **kök klasöründe** `.env.production` oluşturup değerleri doldurun (`SECRET_KEY`, `ALLOWED_HOSTS`, `POSTGRES_*` şifreleri vb.). Bu compose dosyası **PostgreSQL ve Ollama konteynerlerini de** ayağa kaldırır; varsayılan olarak backend `db` ve `ollama` hizmet adlarına bağlanır (harici RDS istemiyorsanız `POSTGRES_HOST` / `OLLAMA_BASE_URL` satırlarını örnekteki gibi yorumda bırakabilirsiniz).
3. Kökte bir `.env` dosyasında en az şu satır olsun (frontend derlemesi bunu kullanır):  
   `NEXT_PUBLIC_API_URL=http://localhost:8080`  
   (Portu değiştirdiyseniz aynı porta göre yazın.)
4. Komut:  
   `docker compose -f docker-compose.prod.yml up -d --build`  
5. Tarayıcıda: `http://localhost:8080` (varsayılan Nginx portu 8080 ise).

Bu, **gerçek internet yayını değildir**; ama üretim imajları + veritabanı + Ollama + Nginx’in birlikte çalışmasını test etmeye yarar. İlk seferde Ollama model indirebilir; bir süre bekleyin.

### Yol B — Gerçekten internete açmak (AWS)

Aşağıdaki bölüm bunun için. Adımlar sırayla gider; hepsini bir günde bitirmek zorunda değilsiniz.

---

## Domain yokken: sadece IP ile ilk yayın

Alan adı satın almadan da dışarıdan erişim kurabilirsiniz; kullanıcılar tarayıcıya **`http://SUNUCU_IP`** (gerekirse port ile, örn. `:80` veya `:8080`) yazar. Aşağıdakileri aynı IP + port kombinasyonuna göre doldurun (hepsinde **http** kullanın; sertifika olmayan IP için tarayıcı “Güvenli değil” uyarısı gösterebilir, ilk denemelerde bu normaldir).

### Hangi adresi yazacağım?

- **Tek bir EC2 / Lightsail sunucusunda** Docker veya Nginx dinliyorsa: O makinenin **Elastic IP** veya **public IPv4** adresi (mümkünse Elastic IP atayın; makineyi kapatıp açınca normal IP değişir, her seferinde ayarı güncellemeniz gerekir).
- **Application Load Balancer (ALB)** kullanıyorsanız: Genelde **`http://xxx.region.elb.amazonaws.com`** şeklinde bir adres verilir (bu da satın alınmış domain değil, yine de tarayıcıda çalışır). İlk aşamada çoğu kişi EC2 + tek IP ile başlar.

### Django (backend) ortam değişkenleri

- **`ALLOWED_HOSTS`:** Public IP’yi virgülle ekleyin. Örnek: `127.0.0.1,localhost,3.120.45.100`  
  ALB kullanıyorsanız o DNS adını da ekleyin: `my-app.eu-central-1.elb.amazonaws.com`
- **`CSRF_TRUSTED_ORIGINS_EXTRA`:** Tarayıcının gördüğü tam kök adres. Örnekler:  
  `http://3.120.45.100`  
  Standart HTTP portu **80** ise port yazmayın; Nginx’i 8080’de dışarı açtıysanız: `http://3.120.45.100:8080`
- **`CORS_ALLOWED_ORIGINS_EXTRA`:** Aynı kök adresi tekrar (frontend ile API aynı IP:port’tan servis ediliyorsa bazen gerek kalmayabilir; sorun yaşarsanız ekleyin).

### Frontend derlemesi (`NEXT_PUBLIC_API_URL`)

Tarayıcı API’ye hangi adresten gidecekse **aynısı** olmalı: örn. `http://3.120.45.100` veya `http://3.120.45.100:8080`.  
GitHub’da deploy variable olarak da **aynı değeri** verin; frontend imajı bunu build sırasında içine gömer.

### Güvenlik grubu (firewall)

- İlk test için genelde **80** (HTTP) için gelen trafiğe izin verilir. Uygulamanız başka host portunda ise (ör. 8080) o portu açın.  
- İleride domain + HTTPS ekleyince **443** açılır; IP ile “ücretsiz geçerli sertifika” tarafı domain gerektirdiği için ilk aşamada çoğu ekip **HTTP** ile başlar.

### Domain alınca ne değişir?

- `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS_EXTRA`, `CORS_ALLOWED_ORIGINS_EXTRA` ve **`NEXT_PUBLIC_API_URL`** içinde **https://siteniz.com** kullanırsınız; frontend’i **yeniden build edip** yeniden deploy edersiniz (sadece backend güncellemek yetmez).

---

## 4. AWS ile ilk kez yayına alırken mantıklı sıra

Aşağıdaki sıra, “önce ne, sonra ne” kargaşasını azaltmak içindir.

### Adım 1 — AWS hesabı ve bölge

- Bir **bölge** seçin (ör. `eu-central-1`) ve işlemleri hep orada yapın; kaynaklar bölgeye bağlıdır.

### Adım 2 — Veritabanı (PostgreSQL + pgvector)

- Bu proje **PostgreSQL** ve **pgvector** kullanıyor (vektör araması için).
- AWS’de buna uygun bir seçenek kurmanız gerekir (çoğu ekip **RDS** veya pgvector destekli başka bir Postgres kullanır).
- Veritabanı **şifresini**, **adresini** (host), **portu**, **veritabanı adını** ve **kullanıcı adını** not edin; bunları daha sonra Django’ya ortam değişkeni olarak vereceksiniz (`deploy/env.production.example` içindeki isimlerle uyumlu).

### Adım 3 — Yapay zekâ (Ollama veya başka servis)

- Yerelde **Ollama** konteyneri kullanıyorsunuz. Bulutta ya Ollama’yı erişilebilir bir sunucuda çalıştırırsınız ya da ileride başka bir API’ye (ör. bulut üretici modeli) geçersiniz.
- Django tarafında **`OLLAMA_BASE_URL`** gibi değişkenler bu adresi gösterir. Uygulama bu adrese ulaşamazsa sohbet tarafı çalışmaz.

### Adım 4 — Docker imajlarını saklamak (ECR)

- İki ayrı “repo” açın: biri **backend**, biri **frontend** imajı için (isimleri not edin).
- GitHub’daki deploy iş akışı bu isimleri **secret** olarak bekler.

### Adım 5 — GitHub tarafı (secrets ve ayarlar)

Repoda `.github/workflows/deploy-aws.yml` var. Çalışması için GitHub’da kabaca şunlar gerekir:

- **Repository → Settings → Secrets and variables → Actions**
  - Örnek isimler (workflow’daki yorumlarla aynı): AWS bağlantısı için rol ARN, bölge, iki ECR repo adı.
- **Variables** kısmında özellikle **`NEXT_PUBLIC_API_URL`**: Tarayıcının göreceği tam kök adres. **Domain yokken** örn. `http://1.2.3.4` veya `http://1.2.3.4:8080`. **Domain varken** örn. `https://siteniz.com`.
- **Environments** içinde **`production`** adında bir ortam oluşturun (workflow bunu kullanıyor). İsterseniz buraya onay kuralı ekleyebilirsiniz.

**Not:** GitHub’ın AWS’ye şifresiz bağlanması için genelde **OIDC + IAM rolü** kurulur. Repoda hazır **JSON taslakları** var:

- `deploy/github-oidc-trust-policy.json` — Rolün GitHub’a güvenmesi için (içindeki `YOUR_*` yerlerini kendi hesap ve repo bilginizle değiştirmeniz gerekir).
- `deploy/iam-ecr-ecs-deploy-policy.json` — O role verilecek izinlerin taslağı (ECR’a itme; ECS kullanacaksanız restart vb.).

Bu JSON’ları olduğu gibi yapıştırmayın; içindeki örnek ARN’leri **kendi** hesap ve kaynaklarınızla değiştirin veya AWS dokümantasyonuyla birlikte uygulayın.

### Adım 6 — Uygulamayı AWS’de çalıştıracak “çerçeve”

- **ECS Fargate**, **EC2 üzerinde Docker**, **Elastic Beanstalk** gibi seçenekler vardır. Bu repo size hazır bir “tıkla kur” ECS cluster’ı vermez; AWS konsolunda veya altyapı kodunuzda **servis**, **ağ (VPC, alt ağ, güvenlik grubu)**, **yük dengeleyici (ALB)** gibi parçaları oluşturmanız gerekir.
- Konteynere vereceğiniz ortam değişkenleri, `deploy/env.production.example` dosyasındaki mantıkla uyumlu olmalı (en azından Django için: `DEBUG=False`, güçlü `SECRET_KEY`, `ALLOWED_HOSTS`, veritabanı bilgileri, `OLLAMA_BASE_URL`).

### Adım 7 — Veritabanı tablolarını oluşturma (migrate)

İlk seferde veritabanında tablo yoksa uygulama düzgün çalışmaz.

- **Basit senaryo (tek kopya backend):** Konteyner ortamına bir kez `RUN_MIGRATIONS=1` (veya `true`) verip ayağa kaldırabilirsiniz; sonra **aynı anda birden fazla kopyada açık bırakmayın** (yarış sorunu çıkar).
- **Birden fazla kopya:** Migrasyonu ayrı bir “tek seferlik görev” olarak çalıştırmak daha doğrudur. Örnek komut dosyası: `deploy/ecs-migrate-run-task.example.sh` — içini kendi cluster, subnet ve güvenlik grubu bilginize göre doldurmanız gerekir.

### Adım 8 — Alan adı ve HTTPS (domain alınca)

- İlk denemede **sadece IP** kullanıyorsanız bu adımı atlayın; **`http://IP`** ile devam edin (yukarıda “Domain yokken” bölümü).
- Domain hazırsa sertifika kurulumu için aşağıdaki **[Sertifika ve HTTPS (SSL)](#sertifika-ve-https-ssl)** bölümüne bakın.
- Django’da **`ALLOWED_HOSTS`** ve CSRF/CORS için **`https://www.acusmartassistant.com.tr`** (ve kök domain) adresleri `deploy/env.production.example` ile uyumlu olmalı; **`NEXT_PUBLIC_API_URL`** **https** olacak şekilde ayarlanıp frontend **yeniden build** edilmelidir.

### Adım 9 — Kod değişince ne olur?

- `main` branch’e push → **CI** testleri çalışır.
- CI başarılı olursa **`deploy-ec2-simple`** iş akışı (ayarlıysa) EC2’ye **SSH** açar: `git pull` + `docker-compose.prod.yml` ile **`--build`**. Gerekli: `SSH_PRIVATE_KEY`, `EC2_HOST`, `EC2_USER`, Variable **`EC2_DEPLOY_PATH`**. **ECR / AWS IAM şart değil.**
- İsterseniz ileride **`deploy-aws`** ile (manuel) ECR push + `docker-compose.ec2.yml` kullanabilirsiniz; ECS variable’ları doluysa o iş akışında rolling restart da vardır.

---

## Sertifika ve HTTPS (SSL)

İki yaygın yol var. Hangisini seçeceğiniz, sunucuda sadece **Docker + Nginx** mi çalıştırdığınıza ve AWS’de **yük dengeleyici** kullanıp kullanmadığınıza bağlıdır.

### Yol 1 — AWS Certificate Manager (ACM) + Application Load Balancer (önerilen, AWS’de)

Bu yolda **SSL, Nginx konteynerinin içinde değil**, genelde **ALB** üzerinde biter.

1. **Route 53** (veya domain’in hangi firmadaysa) DNS’te **`www.acusmartassistant.com.tr`** ve isteniyorsa **`acusmartassistant.com.tr`** kayıtlarını ileride ALB’ye yönlendirecek şekilde hazırlayın.
2. AWS konsolunda **Certificate Manager (ACM)** açın; sertifikayı **ilişkilendirilmiş bölgenizde** isteyin (ör. `eu-central-1`).  
   - Alan adlarını ekleyin: `www.acusmartassistant.com.tr`, `acusmartassistant.com.tr`.  
   - Doğrulama: genelde **DNS** kaydı ekleyerek (ACM size CNAME verir) veya e-posta (nadiren).
3. **Application Load Balancer** oluşturun (internet-facing).  
   - **Listener 443 (HTTPS)** ekleyin ve az önce onaylanan **ACM sertifikasını** seçin.  
   - **Hedef grup**, Nginx’in dinlediği porta (ör. 8080 veya 80) işaret etsin (sunucudaki Docker Nginx bu porta map edilmeli).  
   - İsterseniz **80 → 443 yönlendirme** kuralı ekleyin (HTTP’yi HTTPS’e zorlamak).
4. Güvenlik gruplarında **443** (ve gerekirse **80**) internete açık olsun; EC2 üzerinde sadece ALB’den gelen trafiğe izin verecek şekilde sıkılaştırabilirsiniz.
5. Uygulama tarafı: Tarayıcı **her zaman `https://www.acusmartassistant.com.tr`** görür. Bu yüzden **`.env.production`** ve **`NEXT_PUBLIC_API_URL`** değerlerini **https** yapın; frontend’i yeniden derleyin / yeni imaj alın.  
6. Django arka planda **HTTP** üzerinden ALB’den konuşuyorsa, ileride `SECURE_PROXY_SSL_HEADER` gibi ayarlar gerekebilir (şimdilik çoğu API/JSON senaryosunda `ALLOWED_HOSTS` + CSRF kökleri yeterlidir; sorun yaşarsanız bu başlığı ayrıntılandırırız).

**Özet:** Sertifika ACM’de; şifre çözümü ALB’de; Nginx konteyneriniz çoğunlukla **içeride HTTP** dinlemeye devam eder.

### Yol 2 — Let’s Encrypt (Certbot) doğrudan sunucuda veya Nginx’te

ALB kullanmıyor, tek **EC2 + Docker** ile dış dünyaya **doğrudan Nginx** açıyorsanız:

1. Sunucuda **Certbot** kurulumu yapılır (işletim sistemi paketleri veya resmi talimatlar).
2. Sertifikalar genelde `/etc/letsencrypt/live/www.acusmartassistant.com.tr/` altında üretilir.
3. **Nginx’in gerçekten 443 dinlemesi** gerekir; repodaki [`nginx/nginx.conf`](nginx/nginx.conf) şu an yalnızca **80** dinliyor. Üretimde ayrı bir `nginx.ssl.conf` veya aynı dosyada `listen 443 ssl`, `ssl_certificate` / `ssl_certificate_key` yolları ve bu dosyaların konteynere **volume** ile bağlanması gerekir. Port **443**’ü Docker’da host’ta map edin (`443:443`).
4. Let’s Encrypt sertifikaları **90 günde bir** yenilenir; **cron** veya **certbot renew** otomasyonu şarttır; yenileme sonrası Nginx’i reload etmek gerekir (`docker compose exec nginx nginx -s reload` gibi).
5. DNS’te **`www`** (ve kök) A kaydı sunucunun public IP’sine (veya Elastic IP) işaret etmeli; sertifika doğrulaması bunun üzerinden yapılır.

**Özet:** Sertifika sunucuda dosya olarak durur; Nginx TLS’i kendisi sonlandırır; compose ve nginx ayarını buna göre genişletmeniz gerekir.

### Hangisini seçmeliyim?

| Durum | Pratik öneri |
|--------|----------------|
| AWS’de kalıcı prod, ölçeklenebilir yapı | **ACM + ALB** |
| Tek küçük EC2, ALB maliyeti/ayarı istemiyorum | **Let’s Encrypt + Nginx 443** (compose + nginx conf güncellemesi) |

---

## 5. Sık karışan noktalar

- **`NEXT_PUBLIC_API_URL`:** Next.js bunu **derleme sırasında** gömer. URL değiştiyse frontend’i **yeniden derleyip** yeni imaj üretmeniz gerekir; sadece backend’i güncellemek yetmez.
- **`DEBUG`:** Canlı ortamda **`False`** olmalı (`deploy/env.production.example` böyle önerir).
- **`SECRET_KEY`:** Tahmin edilemez, uzun bir değer kullanın; repoya yazmayın.
- **Yerel `docker-compose.prod.yml` ile GitHub `deploy` farklı şeyler:** Birincisi bilgisayarınızda çalıştırır; ikincisi imajları AWS ECR’a gönderir. İkisini birbirine karıştırmayın.

---

## 6. Dosyalar nerede? (hızlı harita)

| Ne işe yarar? | Dosya / klasör |
|----------------|----------------|
| Otomatik test ve lint | `.github/workflows/ci.yml` |
| Otomatik deploy (SSH, sunucuda build) | `.github/workflows/deploy-ec2-simple.yml` |
| ECR / manuel deploy | `.github/workflows/deploy-aws.yml` |
| Üretim backend imajı | `backend/Dockerfile.prod` ve `backend/docker-entrypoint.sh` |
| Üretim frontend imajı | `frontend/Dockerfile.prod` |
| Örnek ortam değişkenleri | `deploy/env.production.example` |
| OIDC / IAM taslağı | `deploy/github-oidc-trust-policy.json`, `deploy/iam-ecr-ecs-deploy-policy.json` |
| Prod docker (sunucuda build) | `docker-compose.prod.yml` |
| Prod docker (ECR imajı, Actions SSH) | `docker-compose.ec2.yml` |
| Daha teknik notlar | `deploy/README.md` |

---

## 7. Özet cümle

Bu repo, **test + imaj üretme + ECR’a itme** konusunda yola çıkmanızı kolaylaştırır. **İlk kez** yapıyorsanız asıl zaman alan kısım genelde AWS’de: veritabanı, ağ ve uygulama sunucusu (veya konteyner servisi). **Domain şart değil:** önce **public IP + HTTP** ile açıp, her şey çalışınca domain ve HTTPS ekleyebilirsiniz. Takıldığınız adımı tek tek sormak en az stresli yöntemdir.
