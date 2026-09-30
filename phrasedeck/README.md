# Phrase Deck

Aplikasi flashcard (spaced repetition ala Anki) buat self-host. Data disimpan di SQLite, jadi progres sama di semua perangkat.

## Jalankan pakai Docker (disarankan)

```bash
docker compose up -d --build
```

Buka `http://192.168.1.6:8082` dari perangkat di LAN yang sama. Port 8082 dipakai karena port 8080 sudah digunakan aplikasi lain di server ini. Docker Compose hanya memublikasikan port pada IP LAN `192.168.1.6`; jika IP server berubah, perbarui binding di `docker-compose.yml`. Database ada di folder `./data/phrasedeck.db`.

Secara default tidak ada password: siapa pun di LAN yang bisa mengakses alamat ini dapat melihat dan mengubah kartu. Isi `APP_PASSWORD` di `docker-compose.yml` sebelum menjalankan jika perlu membatasi akses.

## Audio cara baca

Tiap frasa dan contoh kalimat otomatis dibuatkan audionya oleh **Piper TTS**, suara AI yang jalan offline di server lo sendiri (gratis, tanpa API key). Audio disimpan di `data/audio/`, jadi cuma dibuat sekali per kalimat. Kartu baru atau yang diedit langsung dibuatkan audionya di belakang layar.

Ganti suara lewat `PIPER_VOICE` di `docker-compose.yml`, lalu `docker compose up -d --build`:

| Suara | Aksen |
|---|---|
| `en_US-lessac-medium` (default) | Amerika, perempuan |
| `en_US-ryan-medium` | Amerika, laki-laki |
| `en_GB-alba-medium` | British, perempuan |

Daftar lengkap: https://huggingface.co/rhasspy/piper-voices

Kalau Piper tidak aktif, tombol 🔊 otomatis pakai suara bawaan browser.

## Jalankan tanpa Docker

Butuh Python 3.8+. Aplikasinya jalan tanpa package tambahan (audio pakai suara browser). Untuk audio Piper:

```bash
pip install piper-tts
python3 -m piper.download_voices --download-dir voices en_US-lessac-medium
PIPER_MODEL=voices/en_US-lessac-medium.onnx python3 server.py
```

```bash
python3 server.py
# atau dengan pengaturan:
PORT=8080 DB_PATH=data/phrasedeck.db APP_PASSWORD=rahasia python3 server.py
```

## Pengaturan

| Variabel       | Default               | Keterangan |
|----------------|-----------------------|------------|
| `PORT`         | `8080`                | Port server |
| `DB_PATH`      | `data/phrasedeck.db`  | Lokasi file SQLite |
| `APP_PASSWORD` | (kosong)              | Kalau diisi, browser minta login. Username bebas, password sesuai ini. |
| `PIPER_MODEL`  | (kosong)              | Path model suara Piper `.onnx`. Sudah diatur otomatis di Docker. |
| `AUDIO_DIR`    | `data/audio`          | Lokasi cache audio |

## Backup

Cukup salin file `data/phrasedeck.db` (folder `data/audio` tidak perlu, bisa dibuat ulang otomatis), atau pakai tombol **Export JSON** di tab Kartu.

## API

| Method | Path              | Fungsi |
|--------|-------------------|--------|
| GET    | `/api/cards`      | Ambil semua kartu |
| POST   | `/api/cards`      | Tambah kartu (`front`, `back`, `example`, `tag`) |
| PUT    | `/api/cards/{id}` | Edit kartu / simpan hasil review |
| DELETE | `/api/cards/{id}` | Hapus kartu |
| POST   | `/api/import`     | Ganti semua kartu dengan isi file JSON |
| GET    | `/api/tts?text=…` | Audio WAV untuk teks tersebut |
| GET    | `/api/config`     | Cek apakah audio server aktif |
| GET    | `/api/health`     | Cek server hidup |

## Catatan keamanan

Password memakai HTTP Basic Auth, jadi aman kalau hanya di jaringan rumah atau lewat VPN/Tailscale. Kalau dibuka ke internet, taruh di belakang reverse proxy dengan HTTPS (Caddy, Nginx Proxy Manager, Traefik).
