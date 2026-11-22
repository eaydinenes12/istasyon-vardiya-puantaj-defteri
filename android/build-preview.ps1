param(
    [Parameter(Mandatory=$true)][string]$Sdk,
    [Parameter(Mandatory=$true)][string]$Java,
    [string]$Output = "build\android-preview"
)
$ErrorActionPreference = 'Stop'
$env:JAVA_HOME = (Resolve-Path $Java).Path
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
$sdkRoot = (Resolve-Path $Sdk).Path
$tools = Join-Path $sdkRoot 'build-tools\35.0.0'
$platform = Join-Path $sdkRoot 'platforms\android-35\android.jar'
$out = (New-Item -ItemType Directory -Force $Output).FullName
$classes = (New-Item -ItemType Directory -Force (Join-Path $out 'classes')).FullName
$dex = (New-Item -ItemType Directory -Force (Join-Path $out 'dex')).FullName
$resources = Join-Path $out 'resources.zip'
$unsigned = Join-Path $out 'unsigned.apk'
$aligned = Join-Path $out 'aligned.apk'
$apk = Join-Path $out 'VardiyaMobil-Onizleme.apk'
$keystore = Join-Path $out 'preview-signing.p12'
function Assert-Native { if ($LASTEXITCODE -ne 0) { throw "Android build command failed: $LASTEXITCODE" } }
& "$tools\aapt2.exe" compile --dir (Join-Path $PSScriptRoot 'res') -o $resources
Assert-Native
& "$tools\aapt2.exe" link -I $platform --manifest (Join-Path $PSScriptRoot 'AndroidManifest.xml') -o $unsigned $resources
Assert-Native
$source = Join-Path $PSScriptRoot 'src\tr\vardiyapuantaj\mobile\MainActivity.java'
& "$env:JAVA_HOME\bin\javac.exe" -encoding UTF-8 --release 8 -classpath $platform -d $classes $source
Assert-Native
$bytecode = @(Get-ChildItem -LiteralPath $classes -Recurse -Filter '*.class' | ForEach-Object FullName)
& "$tools\d8.bat" --min-api 26 --lib $platform --output $dex @bytecode
Assert-Native
Push-Location $dex
try { & "$tools\aapt.exe" add $unsigned classes.dex; Assert-Native } finally { Pop-Location }
& "$tools\zipalign.exe" -f -p 4 $unsigned $aligned
Assert-Native
if (!(Test-Path -LiteralPath $keystore)) {
    & "$env:JAVA_HOME\bin\keytool.exe" -genkeypair -storetype PKCS12 -keyalg RSA -keysize 3072 -validity 3650 `
        -keystore $keystore -alias local-preview -storepass android -keypass android -dname 'CN=Vardiya Mobil Preview'
    Assert-Native
}
& "$tools\apksigner.bat" sign --ks $keystore --ks-key-alias local-preview --ks-pass pass:android --out $apk $aligned
Assert-Native
& "$tools\apksigner.bat" verify --verbose --print-certs $apk
Assert-Native
Get-FileHash -LiteralPath $apk
