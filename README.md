# İstasyon Vardiya Puantaj Defteri

Windows üzerinde haftalık vardiyaları planlamak, aylık puantajı kontrol etmek ve Excel çıktısı almak için masaüstü uygulaması. Personel ve çalışma kayıtları yerel SQLite veritabanında saklanır. GitHub paketi boş kayıtlarla ve kuruma özel bilgi içermeyen örnek formlarla gelir.

## Windows için indir

1. [Son sürümden Windows x64 paketini indir](https://github.com/eaydinenes12/istasyon-vardiya-puantaj-defteri/releases/latest). **Assets** altında `VardiyaPuantaj-Windows-x64.zip` dosyasını seç. “Source code” bağlantıları geliştiriciler içindir.
2. ZIP'in **tamamını** yazma iznin olan yeni bir klasöre çıkar. Arşivin içinden çalıştırma. Kurulum sihirbazı veya Python gerekmez; uygulama Microsoft Edge WebView2 Runtime kullanır.
3. `VardiyaPuantaj.exe` dosyasını aç. **Personel** bölümünden personeli ekle, **Vardiya** sayfasında çalışma saatlerini gir ve kaydet. Ardından **Puantaj** sayfasından ayı inceleyebilir ve Excel çıktısı alabilirsin.

`data` klasörü kayıtlarını içerir; silme, paylaşma veya Git’e ekleme. Var olan kurulumu güncellemeden önce uygulamayı kapat ve kayıtlarını yedekle. Boş dağıtım klasörünü mevcut `data` üzerine kopyalama. Örnek formları kullanmadan önce kendi işyerine uygunluğunu kontrol et.

## Geliştirme ve katkı

Depoyu **Fork** ile hesabına kopyala, klonla ve proje kökünü editöründe aç. Windows x64 için doğrulanmış bağımlılık sürümleri `constraints-3.8.0-windows.txt` dosyasındadır. Windows PowerShell ile, Python 3.15 ve WebView2 Runtime kurulu bir ortamda:

```powershell
py -3.15 -m venv .venv
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -r requirements.txt -c constraints-3.8.0-windows.txt
$env:VARDIYA_PUANTAJ_DATA_DIR = Join-Path $env:TEMP ("vardiya-deneme-" + [guid]::NewGuid())
& $py prototype_launcher.py
```

```powershell
& $py -B validate_build_inputs.py
& $py -B -m unittest discover -s tests
& $py -m PyInstaller --workpath (Join-Path $env:TEMP ("vardiya-build-" + [guid]::NewGuid())) VardiyaPuantaj.spec
```

Yeni EXE `dist\VardiyaPuantaj.exe` altında oluşur. Windows arayüz sınamaları `tests\acceptance_*.py`, paket denetimleri `araclar\`, Android istemci kaynakları `android\` altındadır. Masaüstü ZIP’i bir Android kurulum paketi değildir.

Değişikliğini ayrı bir dalda yap, ilgili testleri çalıştır ve bir **Pull request** aç. Hata bildirirken **Issues** kullan; çalışan adlarını, puantaj dosyalarını, veritabanını, parolaları veya erişim kodlarını yükleme. Kullanım ve katkı izinleri için [LICENSE](LICENSE) dosyasına bak.
