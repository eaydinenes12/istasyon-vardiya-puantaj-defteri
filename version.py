"""Uygulama sürümü ve 'Yenilikler' listesi."""

APP_VERSION = "3.8.0"

WHATS_NEW = [
    {
        "version": APP_VERSION,
        "items": [
            "Vardiya çizelgesi istasyon müdürünün imzasında biter. Ek personel kendi bölümünde, imzadan önce listelenir.",
            "Biçimlendirme modunda seçili satırı veya sütunu onayla kaldırabilirsiniz. Geri al, sıradaki işlemi belirtir; Excel çıktısı ekrandaki düzeni izler.",
            "Puantaj gün başlıklarında iş günleri, hafta sonları, resmî ve dinî tatiller ayrı gösterilir. Excel'de tatiller siyah-beyaz baskıda da işaretlidir.",
            "Araçlar menüsünde klavyeyle gezinebilirsiniz. Toplu giriş ve vardiya döngüsü, değişecek personeli kaydetmeden önce gösterir.",
            "Devir defterinde okumak, üstlenmek ve tamamlamak ayrı kaydedilir. Eski döneme ait bekleyen e-postalar kendiliğinden gönderilmez; nedeni geçmişte gösterilir.",
        ],
        "tour": [
            {
                "id": "find-handover",
                "title": "Devir defterini bulun",
                "description": "“Devir” aramasında Yerini göster ile görev panosunu bulun. Notu okuduğunuzu işaretlemek işi üstlenmez veya tamamlamaz.",
                "action": "tool-search",
                "query": "devir",
            },
            {
                "id": "named-undo",
                "title": "Puantajda sıradaki geri alma işlemini görün",
                "description": "Geri al düğmesinin açıklaması, hangi düzenlemenin geri alınacağını gösterir. Tur hiçbir hücreyi değiştirmez.",
                "action": "view-control",
                "view": "attendance",
                "target": "undo-attendance",
            },
        ],
    },
    {
        "version": "3.7.3",
        "items": [
            "Tüm araçlar aramasında Yerini göster, aracı çalıştırmadan kendi menüsünde bulur.",
            "Puantajı kaydettikten sonra tek başınıza yaptığınız son hücre düzenlemesini Geri al ile taslağa geri getirebilir, kontrol edip yeniden kaydedebilirsiniz.",
            "Sunucunun son onayı alınamayan veya yalnız bazı alıcılara kabul edilen e-postalar ayrı gösterilir; aynı gönderim otomatik yinelenmez.",
            "Plan kalitesi tek aktif personelde kayıtlı görev yetkinliklerini gösterir. Yönetici özetinde farklı uzunluktaki aylar için personel, takvim günü ve normal iş günü birlikte okunabilir.",
            "Sorun raporunu kaydetmeden önce her dosyanın içeriğini görebilirsiniz. Paket personel adları, dosya yolları veya özel ayar metinleri içermez.",
            "Yedeğin geri dönüş provasını vardiya ve puantaj için ayrı ayrı seçtiğiniz aylarda yapabilirsiniz.",
            "Canlı asistana hangi kişi ve dönemin gönderileceğini mesajı yollamadan önce görebilirsiniz; tüm personel listesi otomatik verilmez.",
        ],
        "tour": [
            {
                "id": "tool-location",
                "title": "Aracı çalıştırmadan yerini bulun",
                "description": "Tüm araçlarda “yedek” arayın. Sonuçtaki “Yerini göster” seçeneği aracı çalıştırmadan menüdeki konumunu açar.",
                "action": "tool-search",
                "query": "yedek",
            },
            {
                "id": "attendance-undo",
                "title": "Son puantaj düzenlemesini geri alın",
                "description": "Puantaj araç şeridindeki Geri al, kaydedilmiş hücre düzenlemesini yalnız taslağa döndürür; kalıcı olması için yeniden kaydetmeniz gerekir.",
                "action": "view-control",
                "view": "attendance",
                "target": "undo-attendance",
            },
        ],
    },
    {
        "version": "3.7.2",
        "items": [
            "Vardiya ekranına dönünce seçilen hafta korunur. Sonuç bulunmadığında arama ve filtreleri tablodan temizleyebilirsiniz.",
            "Puantajda FM hücresine çift tıklayınca iki aya yayılan gece vardiyasının her aya düşen dakikaları gösterilir.",
            "Kişisel takvim dosyasında vardiya saatleri değişse de aynı kaydın kimliği korunur. Takvim dosyası otomatik eşitleme yapmaz.",
            "E-posta otomasyonunda yedi günlük gönderim provasıyla alıcı, konu, rapor ve dönem gönderim yapmadan incelenebilir.",
            "Yedek doğrulaması ek ve şablon kapsamını gösterir. Uygulama bilgisi ekranında açık şubeyi görebilirsiniz.",
        ],
    },
    {
        "version": "3.7.1",
        "items": [
            "Yardım ve güncelleme menüsünde çalışan EXE/kaynak klasörü, sürüm ve etkin veritabanı konumunu gösteren Uygulama bilgisi.",
        ],
    },
    {
        "version": "3.7.0",
        "items": [
            "Ay sonu kontrol sihirbazı: veri sağlığı, plan, puantaj, izin onayı ve yedek adımlarını denetler; hatasız ay tek tıkla kilitlenir ve çıktılar alınır.",
            "Şablon kitaplığında hazır biçimler (A4 yatay tek sayfa, okunaklı ekran, sade görünüm); kopyalanıp kişisel şablon olarak saklanabilir.",
            "Tutanak metni, başlığı ve imza alanları düzenlenebilir; özel tutanak biçimi şablon olarak kaydedilir.",
            "Personel kartında eksik bilgi göstergesi; izin akışında bekleyen izni hemen onaylama.",
            "Puantajda yer almayan aktif personel için uyarı ve tek tıkla puantaja ekleme.",
            "E-posta: birden çok rapor eki, gönderilemeyen postayı 5/15/60 dakika sonra yeniden deneme, kaçırılan zamanlı gönderimi açılışta tamamlama ve gönderim geçmişi.",
            "Haftalık yasal sınır net 45 saate göre denetlenir; bulut eşitleme klasöründeki veritabanı için uyarı.",
            "Ana menüde sığmayan başlıklar 'Daha fazla' menüsünde; anlaşılır hata iletileri ve kaydedilmemiş değişiklik onayı.",
        ],
    },
    {
        "version": "3.6.0",
        "items": [
            "Canlı asistan, bu bilgisayardaki Ollama modeliyle yazılı sohbet ve onaylı vardiya/puantaj işlemleri sunar.",
            "Mikrofon girişi kullanıcı tarafından başlatılır; tarayıcı konuşma tanımanın çevrimiçi işlenebileceği belirtilir.",
        ],
    },
    {
        "version": "3.5.1",
        "items": [
            "Kolay Kullanım ve mobil HTTPS bağlantısı ana uygulamanın mevcut veritabanını birlikte kullanır.",
            "Mobil bağlantı yalnız açıkça yapılandırılan, TLS doğrulamalı yerel ağ hizmetiyle açılır.",
        ],
    },
    {
        "version": "3.4.0",
        "items": [
            "Ana menü başlıkları ve kişisel düzen korunarak araç arama, sabitleme ve Gelişmiş alt gruplar.",
            "Seçili vardiya ve puantaj günleri için tablo yanında işlem paneli ve odağa dönüş.",
            "Çoklu gün seçimi, hafta kopyalama ve Excel kod yapıştırma için onaylı ortak önizleme; tek adımlı geri alma.",
            "Hesaba özel, kaynak sürümü denetlenen kalıcı kurtarma taslakları ve ayrı kayıt/yayın/kilit göstergesi.",
            "Birleştirilmiş, önem sıralı uyarılar ve ilgili kişi/gün hücresine güvenli geçiş.",
            "Kayıtlı görev ilerlemesi ile gerçek kaynak doğrulamasının ayrı gösterimi.",
            "Ekrana özel sabit personel/tarih başlıkları; yerel süre/tıklama ölçümü ve daha hızlı tablo çizimi.",
        ],
    },
    {
        "version": "3.3.0",
        "items": [
            "Boş kayıt alanına önizlemeli veri aktarımı, kaynak/hedef değişim denetimi ve güvenli geri alma.",
            "Seçili dönem yedeğini hücre farklarıyla karşılaştırma ve onaylı kurtarma.",
            "Personel eşleştirmeli, tekrar uygulanmayan ve çakışma çözen çevrimdışı çalışma paketleri.",
            "Sicil/bordro koduyla kimlik incelemesi ve açık eşleştirmeli PDKS mutabakatı.",
            "Ayrıntılı kullanıcı izinleri, süreli kişisel talep alanı ve teslim/okundu ayrımlı yerel bildirimler.",
            "Veri kalitesi ve iş akışı merkezi, tarihli uygunluk ve kadro kuralları.",
            "Acil personel eksikliği adayları, yayımlama farkları ve sürüme bağlı dönem kapanışı.",
            "Kullanıcı oranlarıyla maliyet senaryosu, eğilim/şube karşılaştırması ve yorgunluk örüntüleri.",
            "Geçmişi koruyan tarihli personel başlangıç, ayrılış ve bölüm değişimi.",
        ],
    },
    {
        "version": "3.2.0",
        "items": [
            "Kolay Kullanım: kayıtlı çalışma düzenleri, hücre odak göstergesi, işlem menüsü ve günlük ifadelerle araç araması.",
            "Kaydetmeden önce hücre bazında değişiklik özeti ve seçili değişikliği geri alma.",
            "İlerlemesi saklanan ayrıntılı başlangıç rehberi ve gerçek kayıtlardan ayrı örnek çalışma.",
            "Puantajda tekli, ayrık ve dikdörtgen seçimden personele ve gerçek günlere özel belge taslakları.",
            "Onaylı tekil ve tekrarlayan izinlerin boş vardiya günlerine güvenli otomatik aktarımı ve bağlantılı geri alma.",
            "İzin yerine adaylar, adil dağılım önerileri ve çakışma nedenleri.",
            "Belge dosyası, imzalı nüsha takibi, bağlı kayıtların güncellik denetimi ve hesap açıklaması.",
            "Süreli kişisel yayımlanmış vardiya paylaşımı ve geri yüklemeden salt okunur yedek inceleme.",
            "Excel/PDF çıktılarında belge kodu, tarih, sürüm ve kaynak izi; kodla güncellik sorgulama.",
        ],
    },
    {
        "version": "3.1.0",
        "items": [
            "Personel listesi sayfası: süzme, kayıtlı görünümler, sütun seçimi ve toplu işlemler (Ctrl+Shift+P).",
            "Boş bölüm satırları gizlenir; ad eklendikçe satırlar kendiliğinden açılır. Sıkı / rahat görünüm.",
            "Şablon kitaplığı: varsayılan, favori ve sık kullanılan şablonlar.",
            "Hızlı girişler düzenlenebilir; vardiya türleri (sabah, akşam, gece, ara, yarım gün) ve renkleri.",
            "Bitiş saati 24:00 yazılabilir; Excel çıktısında 24:00 olarak görünür.",
            "Ctrl+fare tekerleği ile yakınlaştırma, sürüklerken tablonun kendiliğinden kayması.",
            "Kişiselleştirilebilir klavye kısayolları ve kapatılabilir ipuçları.",
            "Doldurma tutamacı, seçim toplamları, Tab ile geçen haftadan öneri, biçimi şablona sıfırlama.",
            "Uyarı merkezi ve ay durumu rozeti; uyarılar kaydı engellemez, gizlenebilir.",
            "Bölüm başına günlük asgari kadro uyarısı; 'Puantajı plana döndür'.",
            "Yazdırma sihirbazı, Excel çıktı önizlemesi, üst/alt bilgi, imza satırı, dosya adı şablonu.",
            "Puantaj Excel çıktısında kod açılır listesi ve renklendirme.",
            "Bordro ekleri: avans listesi, kesinti açıklaması, yemek/yol günleri; resmî tatil ücret / telafi izni seçimi.",
            "Denkleştirme raporu; 2025-2030 dinî bayramlar hazır; çıkış tarihi geçen personel otomatik pasif.",
            "Personel kartında belge geçerlilik tarihleri ve kişiye ait değişiklik geçmişi.",
            "Görev panosu, vardiya devir notları, ay özeti grafikleri, kişisel vardiya kartı (PNG).",
            "Yeni ay sihirbazı: dizilimi kopyalama, son haftayı taslak uygulama, önceki ayı kilitleme.",
            "Komut paleti: Ctrl+K ile personel ve tüm komutlarda arama.",
            "Şubeler: her şube için ayrı veri dosyası.",
            "Telefondan salt okunur haftalık vardiya görüntüleme (yerel ağ, gizli anahtarlı).",
            "Ayarları dışa/içe aktarma, sorun raporu ve örnek veri yükleme.",
            "Yüksek karşıtlık / renk körlüğü renkleri; özelleştirilebilir Bugünün özeti kartları.",
            "Son kalınan yere dönme, ilerleme çubuğu, 'Kaydedildi' göstergesi, anlaşılır bağlantı hataları.",
        ],
    },
    {
        "version": "3.0.0",
        "items": [
            "Bordro, raporlar ve yönetici özeti tek fazla mesai kuralını (günlük sınırı aşan süre) kullanır.",
            "Mola düşümü kuralı ve kişiye özel günlük sınır / mola muafiyeti eklendi.",
            "Puantajda FM hücresine çift tıklayınca gün gün hesap dökümü açılır.",
            "Veri sağlığı denetimi ve tek tıkla güvenli onarım (Araçlar › Veri sağlığı).",
            "Sürüm değişince otomatik yedek; güncelleme sonrası önceki sürüme dönüş.",
            "Kaydedilmemiş değişiklikler taslak olarak saklanır; beklenmedik kapanıştan sonra geri yüklenebilir.",
        ],
    },
]
