#!/usr/bin/env python3
"""Phrase Deck server: serves the web app and stores cards in SQLite.

Runs on the standard library alone. Pronunciation audio is generated
offline with Piper TTS when `piper-tts` is installed and PIPER_MODEL
points to a voice model (the Docker image does both).

Env vars:
  PORT          port to listen on (default 8080)
  DB_PATH       SQLite file path (default data/phrasedeck.db)
  AUDIO_DIR     where generated audio is cached (default: <DB folder>/audio)
  APP_PASSWORD  optional; if set, the app asks for a login (any username)
  PIPER_MODEL   path to a Piper .onnx voice model (enables server audio)
"""
import base64
import hashlib
import hmac
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("PORT", "8080"))
DB_PATH = os.environ.get("DB_PATH", "data/phrasedeck.db")
AUDIO_DIR = Path(os.environ.get("AUDIO_DIR") or Path(DB_PATH).parent / "audio")
PASSWORD = os.environ.get("APP_PASSWORD", "")
PIPER_MODEL = os.environ.get("PIPER_MODEL", "")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 5 * 1024 * 1024  # 5 MB
MAX_TTS_CHARS = 500

TEXT_FIELDS = ("front", "back", "example", "tag")
NUM_FIELDS = {"ease": float, "interval": float, "reps": int, "lapses": int, "due": int}
ALL_FIELDS = ("id",) + TEXT_FIELDS + tuple(NUM_FIELDS) + ("created", "updated")

SEED = [
    ("Just to follow up on…", "Sekadar menindaklanjuti…", "Just to follow up on my email from Monday, have you had a chance to review the proposal?", "Email"),
    ("I'm writing to…", "Saya menulis email ini untuk… (pembuka formal)", "I'm writing to confirm our meeting on Thursday.", "Email"),
    ("Please find attached…", "Terlampir…", "Please find attached the updated budget for Q4.", "Email"),
    ("Could you please clarify…?", "Bisakah Anda menjelaskan lebih lanjut…?", "Could you please clarify what you mean by 'phase two'?", "Email"),
    ("I'd appreciate it if you could…", "Saya akan sangat berterima kasih jika Anda bisa…", "I'd appreciate it if you could send the report by Friday.", "Email"),
    ("Apologies for the delayed response.", "Maaf atas keterlambatan balasan.", "Apologies for the delayed response. I've been out of office.", "Email"),
    ("Let me know if you have any questions.", "Kabari saya kalau ada pertanyaan (penutup).", "I've shared the draft. Let me know if you have any questions.", "Email"),
    ("Let's get started.", "Mari kita mulai (buka meeting).", "Okay, everyone's here, so let's get started.", "Meeting"),
    ("The purpose of today's meeting is…", "Tujuan meeting hari ini adalah…", "The purpose of today's meeting is to agree on the launch date.", "Meeting"),
    ("Just to give you a quick update…", "Sekadar update singkat…", "Just to give you a quick update, we're about 80% done with testing.", "Meeting"),
    ("I see your point, but…", "Saya paham maksud Anda, tapi… (beda pendapat dengan sopan)", "I see your point, but I think the timeline is too tight.", "Meeting"),
    ("Could you walk me through…?", "Bisa jelaskan langkah demi langkah…?", "Could you walk me through how you got these numbers?", "Meeting"),
    ("Let's circle back on this.", "Kita bahas lagi nanti.", "We're running out of time. Let's circle back on this next week.", "Meeting"),
    ("To sum up…", "Singkatnya / sebagai kesimpulan…", "To sum up, we'll launch in November and review results in January.", "Meeting"),
    ("What are the next steps?", "Apa langkah selanjutnya?", "Great discussion. What are the next steps?", "Meeting"),
    ("Can we push this back to…?", "Bisakah kita undur ke…?", "Can we push this back to next Tuesday? I have a conflict.", "Negosiasi"),
    ("Would it be possible to…?", "Apakah memungkinkan untuk…? (permintaan halus)", "Would it be possible to extend the deadline by two days?", "Negosiasi"),
    ("I'm afraid I can't…", "Mohon maaf, saya tidak bisa… (menolak sopan)", "I'm afraid I can't take on another project this month.", "Negosiasi"),
    ("That works for me.", "Itu cocok buat saya / saya setuju.", "Friday at 3 p.m.? That works for me.", "Negosiasi"),
    ("I'm open to suggestions.", "Saya terbuka untuk masukan.", "This is my first draft, so I'm open to suggestions.", "Negosiasi"),
    ("Let's find a middle ground.", "Mari cari jalan tengah.", "You want two weeks, I need one. Let's find a middle ground.", "Negosiasi"),
    ("I'd like to start by…", "Saya ingin memulai dengan… (buka presentasi)", "I'd like to start by giving you some background.", "Presentasi"),
    ("As you can see from this chart…", "Seperti terlihat di grafik ini…", "As you can see from this chart, sales grew steadily.", "Presentasi"),
    ("This brings me to my next point.", "Ini membawa saya ke poin berikutnya.", "Costs went down. This brings me to my next point: pricing.", "Presentasi"),
    ("The key takeaway is…", "Poin utamanya adalah…", "The key takeaway is that retention matters more than acquisition.", "Presentasi"),
    ("I'll take ownership of…", "Saya akan bertanggung jawab atas…", "I'll take ownership of the client follow-up.", "Kerja tim"),
    ("I'm on it.", "Segera saya kerjakan.", "Need the slides by noon? I'm on it.", "Kerja tim"),
    ("Keep me in the loop.", "Tolong kabari saya perkembangannya.", "Go ahead with the vendor, but keep me in the loop.", "Kerja tim"),
    ("How was your weekend?", "Gimana weekend-mu? (small talk)", "Morning! How was your weekend?", "Small talk"),
    ("It's been a hectic week.", "Minggu ini sibuk banget.", "It's been a hectic week, but we're almost there.", "Small talk"),
]

write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Text-to-speech (Piper, fully offline). Audio is cached per text on disk, so
# each phrase is generated once and edits simply produce a new file.
# ---------------------------------------------------------------------------
class TTS:
    def __init__(self, model_path):
        self.model_path = model_path
        self.voice = None
        self.error = None
        self.lock = threading.Lock()
        self.jobs = queue.Queue()
        self.enabled = False
        self.voice_name = Path(model_path).stem if model_path else ""
        if not model_path:
            self.error = "PIPER_MODEL belum diatur"
        elif not Path(model_path).is_file():
            self.error = f"Model suara tidak ditemukan: {model_path}"
        else:
            try:
                import piper  # noqa: F401
                self.enabled = True
            except ImportError:
                self.error = "Paket piper-tts belum terpasang"

    @staticmethod
    def normalize(text):
        text = (text or "").replace("…", "").replace("...", "")
        return re.sub(r"\s+", " ", text).strip()

    def path_for(self, text):
        key = hashlib.sha1(f"{self.voice_name}\n{text}".encode("utf-8")).hexdigest()
        return AUDIO_DIR / f"{key}.wav"

    def _load(self):
        if self.voice is None:
            from piper import PiperVoice
            self.voice = PiperVoice.load(self.model_path)
        return self.voice

    def get(self, text):
        """Return the path of a WAV file for `text`, generating it if needed."""
        text = self.normalize(text)
        if not text:
            raise BadRequest("Teks kosong")
        if len(text) > MAX_TTS_CHARS:
            raise BadRequest("Teks terlalu panjang untuk dibacakan")
        path = self.path_for(text)
        if path.is_file():
            return path
        with self.lock:  # one synthesis at a time; model isn't thread-safe
            if path.is_file():
                return path
            voice = self._load()
            AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with wave.open(str(tmp), "wb") as wav:
                voice.synthesize_wav(text, wav)
            tmp.replace(path)
        return path

    def prewarm(self, *texts):
        """Queue texts so their audio is ready before anyone presses play."""
        if self.enabled:
            for t in texts:
                if self.normalize(t):
                    self.jobs.put(t)

    def worker(self):
        while True:
            text = self.jobs.get()
            try:
                self.get(text)
            except Exception as e:
                print("TTS gagal:", repr(e))


tts = TTS(PIPER_MODEL)


def now_ms():
    return int(time.time() * 1000)


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row):
    return {k: row[k] for k in ALL_FIELDS}


class BadRequest(Exception):
    pass


def clean(data, partial=False):
    """Validate incoming card fields. partial=True for updates."""
    if not isinstance(data, dict):
        raise BadRequest("Body harus berupa objek JSON")
    out = {}
    for f in TEXT_FIELDS:
        if f in data:
            v = data[f]
            if v is None:
                v = ""
            if not isinstance(v, str):
                raise BadRequest(f"'{f}' harus teks")
            v = v.strip()
            if len(v) > 2000:
                raise BadRequest(f"'{f}' terlalu panjang")
            out[f] = v
    for f, typ in NUM_FIELDS.items():
        if f in data and data[f] is not None:
            try:
                out[f] = typ(data[f])
            except (TypeError, ValueError):
                raise BadRequest(f"'{f}' harus angka")
    if not partial:
        if not out.get("front") or not out.get("back"):
            raise BadRequest("'front' dan 'back' wajib diisi")
    else:
        for f in ("front", "back"):
            if f in out and not out[f]:
                raise BadRequest(f"'{f}' tidak boleh kosong")
    return out


def insert_card(conn, fields, card_id=None, created=None):
    ts = now_ms()
    card = {
        "id": card_id or uuid.uuid4().hex[:12],
        "front": "", "back": "", "example": "", "tag": "",
        "ease": 2.5, "interval": 0.0, "reps": 0, "lapses": 0, "due": 0,
        "created": created or ts, "updated": ts,
    }
    card.update(fields)
    cols = ",".join(f'"{k}"' for k in ALL_FIELDS)
    marks = ",".join("?" for _ in ALL_FIELDS)
    conn.execute(f"INSERT INTO cards ({cols}) VALUES ({marks})", [card[k] for k in ALL_FIELDS])
    return card


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS cards (
                "id" TEXT PRIMARY KEY,
                "front" TEXT NOT NULL,
                "back" TEXT NOT NULL,
                "example" TEXT NOT NULL DEFAULT '',
                "tag" TEXT NOT NULL DEFAULT '',
                "ease" REAL NOT NULL DEFAULT 2.5,
                "interval" REAL NOT NULL DEFAULT 0,
                "reps" INTEGER NOT NULL DEFAULT 0,
                "lapses" INTEGER NOT NULL DEFAULT 0,
                "due" INTEGER NOT NULL DEFAULT 0,
                "created" INTEGER NOT NULL,
                "updated" INTEGER NOT NULL
            )"""
        )
        if conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0:
            base = now_ms()
            for i, (front, back, example, tag) in enumerate(SEED):
                insert_card(conn, {"front": front, "back": back, "example": example, "tag": tag}, created=base + i)
            print(f"Database baru dibuat dengan {len(SEED)} kartu awal.")


CARD_PATH = re.compile(r"^/api/cards/([A-Za-z0-9_-]{1,64})$")


class Handler(BaseHTTPRequestHandler):
    server_version = "PhraseDeck/1.0"

    # ---------- helpers ----------
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise BadRequest("Data terlalu besar")
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BadRequest("JSON tidak valid")

    def authorized(self):
        if not PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                _, _, pw = decoded.partition(":")
                if hmac.compare_digest(pw, PASSWORD):
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Phrase Deck"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def handle_api(self, fn):
        if not self.authorized():
            return
        try:
            fn()
        except BadRequest as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:  # keep the server alive on unexpected errors
            print("Error:", repr(e))
            self.send_json(500, {"error": "Server error"})

    def serve_static(self, name, ctype):
        path = STATIC_DIR / name
        if not path.is_file():
            self.send_json(404, {"error": "Not found"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ---------- routes ----------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            if self.authorized():
                self.serve_static("index.html", "text/html; charset=utf-8")
        elif path == "/api/health":
            self.send_json(200, {"ok": True})
        elif path == "/api/cards":
            self.handle_api(self.list_cards)
        elif path == "/api/config":
            self.handle_api(lambda: self.send_json(200, {
                "tts": tts.enabled, "voice": tts.voice_name, "ttsError": tts.error}))
        elif path == "/api/tts":
            self.handle_api(self.speak)
        else:
            self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/cards":
            self.handle_api(self.create_card)
        elif path == "/api/import":
            self.handle_api(self.import_cards)
        else:
            self.send_json(404, {"error": "Not found"})

    def do_PUT(self):
        m = CARD_PATH.match(self.path.split("?", 1)[0])
        if m:
            self.handle_api(lambda: self.update_card(m.group(1)))
        else:
            self.send_json(404, {"error": "Not found"})

    def do_DELETE(self):
        m = CARD_PATH.match(self.path.split("?", 1)[0])
        if m:
            self.handle_api(lambda: self.delete_card(m.group(1)))
        else:
            self.send_json(404, {"error": "Not found"})

    # ---------- handlers ----------
    def list_cards(self):
        with connect() as conn:
            rows = conn.execute("SELECT * FROM cards ORDER BY created").fetchall()
        self.send_json(200, [row_to_dict(r) for r in rows])

    def create_card(self):
        fields = clean(self.read_json())
        with write_lock, connect() as conn:
            card = insert_card(conn, fields)
        tts.prewarm(card["front"], card["example"])
        self.send_json(201, card)

    def update_card(self, card_id):
        fields = clean(self.read_json(), partial=True)
        if not fields:
            raise BadRequest("Tidak ada yang diubah")
        fields["updated"] = now_ms()
        sets = ",".join(f'"{k}"=?' for k in fields)
        with write_lock, connect() as conn:
            cur = conn.execute(f"UPDATE cards SET {sets} WHERE id=?", [*fields.values(), card_id])
            if cur.rowcount == 0:
                self.send_json(404, {"error": "Kartu tidak ditemukan"})
                return
            row = conn.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        if "front" in fields or "example" in fields:
            tts.prewarm(row["front"], row["example"])
        self.send_json(200, row_to_dict(row))

    def delete_card(self, card_id):
        with write_lock, connect() as conn:
            cur = conn.execute("DELETE FROM cards WHERE id=?", (card_id,))
        if cur.rowcount == 0:
            self.send_json(404, {"error": "Kartu tidak ditemukan"})
        else:
            self.send_json(200, {"ok": True})

    def import_cards(self):
        data = self.read_json()
        if not isinstance(data, list):
            raise BadRequest("File import harus berupa daftar kartu")
        cleaned = []
        for i, item in enumerate(data):
            try:
                fields = clean(item)
            except BadRequest as e:
                raise BadRequest(f"Kartu #{i + 1}: {e}")
            cid = item.get("id") if isinstance(item.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item["id"]) else None
            created = item.get("created") if isinstance(item.get("created"), int) else None
            cleaned.append((fields, cid, created))
        with write_lock, connect() as conn:
            conn.execute("DELETE FROM cards")
            seen = set()
            for fields, cid, created in cleaned:
                if cid in seen:
                    cid = None
                card = insert_card(conn, fields, card_id=cid, created=created)
                seen.add(card["id"])
            rows = conn.execute("SELECT * FROM cards ORDER BY created").fetchall()
        for r in rows:
            tts.prewarm(r["front"], r["example"])
        self.send_json(200, [row_to_dict(r) for r in rows])

    def speak(self):
        if not tts.enabled:
            self.send_json(503, {"error": tts.error or "Audio server tidak aktif"})
            return
        text = (parse_qs(urlsplit(self.path).query).get("text") or [""])[0]
        data = tts.get(text).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)


def main():
    init_db()
    if tts.enabled:
        threading.Thread(target=tts.worker, daemon=True).start()
        with connect() as conn:  # make sure every existing card has audio
            for r in conn.execute("SELECT front, example FROM cards"):
                if not tts.path_for(tts.normalize(r["front"])).is_file() or \
                   (tts.normalize(r["example"]) and not tts.path_for(tts.normalize(r["example"])).is_file()):
                    tts.prewarm(r["front"], r["example"])
        print(f"Audio: Piper aktif ({tts.voice_name})")
    else:
        print(f"Audio: pakai suara browser ({tts.error})")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Phrase Deck jalan di http://0.0.0.0:{PORT}  (DB: {DB_PATH}, password: {'ON' if PASSWORD else 'OFF'})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
