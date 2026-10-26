param([ValidateSet("now", "archive")] [string]$Mode = "archive")
# Arşiv işleri. Windows Görev Zamanlayıcı'dan çalıştırılır ki Claude oturumundan bağımsız olsun:
#   "Kaderoglu Arsiv Simdi"  -> -Mode now     : mevcut öğelerde yüz tanıma + çıplaklık (siteye hemen düşsün)
#   "Kaderoglu Arsiv"        -> -Mode archive : kalan videolar + veilox paralel, sonra yüz tanıma, çıplaklık, yüklemeler
# Her adım yarıda kesilirse güvenle yeniden çalıştırılabilir (işlenmiş öğeler atlanır).
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONIOENCODING = "utf-8"
$py = (Resolve-Path ".\.venv\Scripts\python.exe").Path
$log = "logs\pipeline.log"

function Note($text) { "[$(Get-Date -Format 'dd.MM HH:mm')] [$Mode] $text" | Add-Content $log -Encoding utf8 }

function Step($name, $logfile, [string[]]$cmd) {
    Note "başladı: $name"
    & $py -u @cmd *>&1 | Out-File $logfile -Encoding utf8
    Note "bitti ($LASTEXITCODE): $name"
}

if ($Mode -eq "now") {
    Step "yüz tanıma (mevcut öğeler)" "logs\faces-now2.log"  @("tools\faces.py", "--workers", "8")
    Step "çıplaklık (mevcut öğeler)"  "logs\nudity-now2.log" @("tools\nudity.py", "--workers", "8")
    Note "TAMAMLANDI"
    exit
}

# kalan videolar ve ikinci hesabın arşivi aynı anda (farklı işlemler; index.json'a birbirini ezmeden yazarlar)
Note "başladı: kalan videolar + veilox.com.tr (paralel)"
$v = Start-Process $py -ArgumentList "-u", "tools\import_takeout.py", "--only", "video", "D:\kaderoglu" `
    -RedirectStandardOutput "logs\takeout-videos.log" -RedirectStandardError "logs\takeout-videos.err" -NoNewWindow -PassThru
$w = Start-Process $py -ArgumentList "-u", "tools\import_takeout.py", "D:\kaderoglu\veilox.com.tr" `
    -RedirectStandardOutput "logs\takeout-veilox.log" -RedirectStandardError "logs\takeout-veilox.err" -NoNewWindow -PassThru
$v.WaitForExit(); $w.WaitForExit()
Note "bitti: videolar ($($v.ExitCode)), veilox ($($w.ExitCode))"

Step "yüz tanıma (tümü)"             "logs\faces-2.log"   @("tools\faces.py", "--workers", "10")
Step "çıplaklık kontrolü"            "logs\nudity-2.log"  @("tools\nudity.py", "--workers", "10")
Step "eksik profil fotoğrafları"     "logs\avatars-2.log" @("tools\avatars_missing.py")
Step "Duvar yüklemelerini arşive al" "logs\uploads.log"   @("tools\ingest_uploads.py", "--archive", "--force")
Step "yüz eşleştirme (yüklemeler)"   "logs\faces-3.log"   @("tools\faces.py", "--match")
Note "TAMAMLANDI"
