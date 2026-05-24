# Backup och återställning

Backup körs via [rclone](https://rclone.org) mot Google Drive (men
fungerar mot vilken rclone-remote som helst). Två separata saker
backas upp:

1. **SQLite-databasen** - en snapshot tas via `sqlite3 .backup` och
   laddas upp komprimerad. Snapshots namnges per tidsstämpel, plus en
   alltid-senaste-fil för enkel återställning.
2. **Uppladdade bilder** - inkrementell sync av `data/images/`-mappen.

## Engångsuppsättning

### 1. Installera rclone på värden

```bash
sudo apt install rclone   # Ubuntu/Debian
# eller från https://rclone.org/install/
```

### 2. Konfigurera Google Drive-remote

```bash
rclone config
```

- `n` (new remote)
- Namn: `gdrive` (eller annat - sätt motsvarande i `.env`)
- Storage type: `drive` (Google Drive)
- Klient-ID och secret: lämna tomt för standard (begränsad kvot) eller
  registrera egna i Google Cloud Console för högre throughput
- Scope: `1` (full access) räcker; `2` (file) går bra för isolerad mapp
- Auto config: `y` öppnar webbläsaren för OAuth
- Bekräfta med `y` och `q`

Testa: `rclone ls gdrive:` - ska visa innehållet i ditt Drive.

### 3. Skapa en mapp i Drive för backupen

Antingen i Drive-webben (rekommenderat - då vet du var den ligger) eller:
```bash
rclone mkdir gdrive:notarkiv-backup
```

### 4. Sätt env-variabler

I `.env`:
```
BACKUP_RCLONE_REMOTE=gdrive
BACKUP_RCLONE_PATH=notarkiv-backup
```

### 5. Testa backup-skriptet manuellt

```bash
./scripts/backup.sh
```

Borde säga något i stil med:
```
[backup] Snapshot av DB till /tmp/...
[backup] Komprimerar
[backup] Laddar upp till gdrive:notarkiv-backup
[backup] Synkar bilder
[backup] Rensar gamla DB-snapshots
[backup] Klar
```

Verifiera i Drive att filerna dyker upp under `notarkiv-backup/db/`
och `notarkiv-backup/images/`.

### 6. Schemalägg via cron

På värden, redigera crontab:
```bash
crontab -e
```

Lägg till (varje natt 03:00):
```
0 3 * * * cd /home/rasmus/workspace/notförråd && ./scripts/backup.sh >> /var/log/notarkiv-backup.log 2>&1
```

Eller om du föredrar systemd timer - skriv en `.service` + `.timer`.

## Vad som lagras i Drive

```
notarkiv-backup/
  db/
    notarkiv-latest.db.gz              # Alltid senaste
    notarkiv-2026-05-24_0300.db.gz     # Per körning, 30 dagar
    notarkiv-2026-05-25_0300.db.gz
    ...
  images/
    covers/
      <uuid>.jpg
      ...
    thumbnails/
      <uuid>.jpg
      ...
```

Snapshots äldre än 30 dagar rensas automatiskt av backup-skriptet.
Bilderna synkas inkrementellt så Drive-mappen speglar lokala
images/-mappen.

## Återställning

Om data är borta - DB-filen raderad, disken död, hela VM:en borta -
återställ från Drive.

### Senaste tillgängliga

```bash
# Stoppa appen och workern först
docker-compose down
./scripts/restore.sh
docker-compose up -d
```

### Specifik snapshot

```bash
./scripts/restore.sh 2026-05-24_0300
```

Lista tillgängliga snapshots:
```bash
rclone ls gdrive:notarkiv-backup/db
```

## Testning av återställning

Innan systemet anses produktionsklart: testa hela kedjan.

1. Skapa några noter och ladda upp bilder
2. Notera vad som finns
3. Stoppa appen
4. Flytta `data/` till `data.bak/` (simulera dataförlust)
5. Kör `./scripts/restore.sh`
6. Starta appen, verifiera att allt finns
7. Ta bort `data.bak/`

Dokumentera datumet och resultatet i CHANGELOG eller annan loggbok.

## Varför inte litestream?

Litestream replikerar WAL-segment kontinuerligt och ger nästan
realtid-backup, men kräver en S3-kompatibel bucket. Google Drive är
inte S3-kompatibelt. Pragmatisk lösning: nattliga snapshots via rclone
räcker för en katalog där den största risken är hårddiskhaveri, inte
sub-minut-precision på återställning.

## Vad jag inte hanterat

- `.env`-filen (innehåller API-nycklar). Backa upp den separat till
  en password manager eller säker förvaring.
- Konfigfiler (`docker-compose.yml` m.fl.) lever i git - pusha
  regelbundet till en remote.
- rclone-konfigurationen (`~/.config/rclone/rclone.conf`) - säkerhets-
  kopiera den också separat.
