# -*- coding: utf-8 -*-
"""Fragment Cutter 1.0 — нарізка фрагментів з YouTube за таймкодами.

Один файл, нуль API-ключів: yt-dlp і ffmpeg завантажуються автоматично
при першому використанні. Результати зберігаються у папці
«Відео → Fragments» у профілю користувача. Стиль: CustomTkinter, iOS dark.
"""

import os
import re
import sys
import queue
import shutil
import zipfile
import threading
import subprocess

import requests
import tkinter
import customtkinter as ctk
from tkinter import messagebox

APP_TITLE = "Fragment Cutter"
APP_VERSION = "1.0"
UA_STRING = "FragmentCutter/1.0 (Windows)"

OUT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "Fragments")

_BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(_BASE, "tools")
try:
    os.makedirs(TOOLS_DIR, exist_ok=True)
    _WRITABLE = os.access(TOOLS_DIR, os.W_OK)
except Exception:
    _WRITABLE = False
if not _WRITABLE:
    TOOLS_DIR = os.path.join(OUT_DIR, "tools")

BG = "#000000"
CARD = "#1C1C1E"
INSET = "#2C2C2E"
HOVER = "#3A3A3C"
ACCENT = "#0A84FF"
MUTED = "#8E8E93"
SEP = "#38383A"
GREEN = "#30D158"
TEXT = "#F5F5F7"

FF_ZIP_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

DEFAULT_FRAGMENTS = [
    ("https://www.youtube.com/watch?v=DW2w97ccmMY", "0:47", "1:04", "clip1_powerbank_gabarity"),
    ("https://www.youtube.com/watch?v=A881JRCGc7c", "0:20", "0:34", "clip2_korobka_100W"),
    ("https://www.youtube.com/watch?v=Xnfu0MpIStE", "1:56", "2:10", "clip3_baseus_noutbuky"),
]


def download_file(url, dest, say):
    """Завантаження файлу з прогресом у журнал."""
    r = requests.get(url, stream=True, timeout=(15, 90), headers={"User-Agent": UA_STRING})
    r.raise_for_status()
    total = int(r.headers.get("Content-Length") or 0)
    done = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            done += len(chunk)
            if total and done % (5 * 1024 * 1024) < len(chunk):
                say("⬇️ {}%…".format(int(done * 100 / total)))
    return dest


def _ffmpeg_candidate():
    p = shutil.which("ffmpeg")
    if p:
        return p
    cand = os.path.join(TOOLS_DIR, "ffmpeg.exe")
    if os.path.isfile(cand):
        return cand
    return None


def ensure_ffmpeg(say):
    """ffmpeg або з PATH, або з TOOLS_DIR, або завантажуємо (один раз)."""
    p = _ffmpeg_candidate()
    if p:
        return p
    say("⬇️ Завантажую ffmpeg (~100 МБ, тільки перший раз)…")
    zdest = os.path.join(TOOLS_DIR, "ffmpeg.zip")
    download_file(FF_ZIP_URL, zdest, say)
    say("📦 Розпаковую ffmpeg…")
    exdir = os.path.join(TOOLS_DIR, "ff")
    with zipfile.ZipFile(zdest) as z:
        for m in z.namelist():
            base = os.path.basename(m).lower()
            if base in ("ffmpeg.exe", "ffprobe.exe"):
                z.extract(m, exdir)
    for root, dirs, files in os.walk(exdir):
        for f in files:
            if f.lower() in ("ffmpeg.exe", "ffprobe.exe"):
                shutil.copy2(os.path.join(root, f), os.path.join(TOOLS_DIR, f))
    try:
        os.remove(zdest)
        shutil.rmtree(exdir, ignore_errors=True)
    except Exception:
        pass
    p = _ffmpeg_candidate()
    if not p:
        raise RuntimeError("ffmpeg не вдалося встановити")
    say("✅ ffmpeg готовий")
    return p


def ensure_ytdlp(say):
    """yt-dlp.exe з TOOLS_DIR, або завантажуємо (один раз)."""
    cand = os.path.join(TOOLS_DIR, "yt-dlp.exe")
    if os.path.isfile(cand) and os.path.getsize(cand) > 1000000:
        return cand
    say("⬇️ Завантажую yt-dlp (~18 МБ, тільки перший раз)…")
    download_file(YTDLP_URL, cand, say)
    return cand


def parse_time(s):
    """«1:04» → 64 секунди; None якщо формат хибний."""
    s = (s or "").strip().replace(" ", "")
    if not s:
        return None
    parts = s.split(":")
    if len(parts) < 1 or len(parts) > 3:
        return None
    sec = 0
    for p in parts:
        if not p.isdigit():
            return None
        sec = sec * 60 + int(p)
    return sec


def sanitize_name(s, fallback):
    """Ім'я файлу без сторонніх символів."""
    s = re.sub(r"[^\w\-. ]", "_", (s or "").strip())
    return s or fallback


def cut_fragment(ytdlp, ffdir, it, say):
    """Нарізати один фрагмент через yt-dlp --download-sections."""
    out = os.path.join(OUT_DIR, it["name"] + ".mp4")
    if os.path.exists(out):
        try:
            os.remove(out)
        except Exception:
            pass
    cmd = [
        ytdlp,
        "--ffmpeg-location", ffdir,
        "--download-sections", "*{}-{}".format(it["start"], it["end"]),
        "--force-keyframes-at-cuts",
        "-f", "bv*+ba/b",
        "--no-playlist",
        "--windows-filenames",
        "-o", out,
        it["url"],
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not os.path.isfile(out):
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "не вдалося завантажити фрагмент")
    say("✅ Готово: " + out)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE + " " + APP_VERSION)
        self.configure(fg_color=BG)
        w, h = 960, 700
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry("{}x{}+{}+{}".format(w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        self.minsize(880, 620)

        self.q = queue.Queue()
        self.rows = []
        self.running = False

        self._build()
        for url, t1, t2, name in DEFAULT_FRAGMENTS:
            self.add_fragment(url, t1, t2, name)
        self.after(120, self._poll)
        self.bind("<Control-Return>", lambda e: self.start())

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(24, 4))
        ctk.CTkLabel(head, text="✂️ Fragment Cutter", font=("Segoe UI", 26, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(head, text="Нарізка фрагментів з YouTube за таймкодами • без API-ключів",
                     font=("Segoe UI", 13), text_color=MUTED).pack(anchor="w")

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=24)
        card.pack(fill="both", expand=True, padx=28, pady=(14, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=16)

        ctk.CTkLabel(inner, text="🔗 Фрагменти", font=("Segoe UI", 17, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(inner, text="Час у форматі MM:SS або H:MM:SS • Ctrl+Enter = нарізати",
                     font=("Segoe UI", 12), text_color=MUTED).pack(anchor="w")

        self.list_wrap = ctk.CTkScrollableFrame(inner, fg_color="transparent", height=290)
        self.list_wrap.pack(fill="x", pady=(10, 0))

        hdr = ctk.CTkFrame(self.list_wrap, fg_color="transparent")
        hdr.pack(fill="x")
        for col, txt in enumerate(("Посилання YouTube", "Початок", "Кінець", "Ім'я файлу", "")):
            lab = ctk.CTkLabel(hdr, text=txt, font=("Segoe UI", 12, "bold"), text_color=MUTED, anchor="w")
            lab.grid(row=0, column=col, padx=6, sticky="w")

        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(fill="x", pady=(12, 0))
        ctk.CTkButton(btns, text="➕ Додати фрагмент", height=36, corner_radius=18,
                      fg_color=INSET, hover_color=HOVER,
                      command=lambda: self.add_fragment()).pack(side="left")
        ctk.CTkButton(btns, text="📂 Папка результатів", height=36, corner_radius=18,
                      fg_color=INSET, hover_color=HOVER,
                      command=self.open_out).pack(side="left", padx=(8, 0))
        self.out_lbl = ctk.CTkLabel(btns, text="📁 " + OUT_DIR, font=("Segoe UI", 12), text_color=MUTED)
        self.out_lbl.pack(side="right")

        bottom = ctk.CTkFrame(self, fg_color=CARD, corner_radius=24)
        bottom.pack(fill="x", padx=28, pady=(14, 0))
        binner = ctk.CTkFrame(bottom, fg_color="transparent")
        binner.pack(fill="x", padx=20, pady=14)

        self.status = ctk.CTkLabel(binner, text="Готово до нарізки", font=("Segoe UI", 13), text_color=MUTED)
        self.status.pack(anchor="w")
        self.bar = ctk.CTkProgressBar(binner, height=6, corner_radius=3, fg_color=INSET, progress_color=ACCENT)
        self.bar.pack(fill="x", pady=(8, 12))
        self.bar.set(0)
        self.go = ctk.CTkButton(binner, text="✂️ НАРІЗАТИ ВСЕ", height=52, corner_radius=26,
                                font=("Segoe UI", 17, "bold"), fg_color=GREEN, hover_color="#28B14C",
                                text_color="#000000", command=self.start)
        self.go.pack(fill="x")

        self.journal = ctk.CTkTextbox(self, height=150, fg_color=CARD, corner_radius=16, font=("Consolas", 12))
        self.journal.pack(fill="x", padx=28, pady=(14, 24))
        self.journal.configure(state="disabled")

    def add_fragment(self, url="", t1="", t2="", name=""):
        row = ctk.CTkFrame(self.list_wrap, fg_color=INSET, corner_radius=12)
        row.pack(fill="x", pady=4, ipady=2)
        u = ctk.StringVar(value=url)
        a = ctk.StringVar(value=t1)
        b = ctk.StringVar(value=t2)
        n = ctk.StringVar(value=name)
        e1 = ctk.CTkEntry(row, textvariable=u, height=34, corner_radius=10, fg_color=BG, border_color=SEP)
        e1.grid(row=0, column=0, padx=6, pady=6, sticky="ew")
        e2 = ctk.CTkEntry(row, textvariable=a, width=64, height=34, corner_radius=10, fg_color=BG,
                          border_color=SEP, justify="center")
        e2.grid(row=0, column=1, padx=6, pady=6)
        e3 = ctk.CTkEntry(row, textvariable=b, width=64, height=34, corner_radius=10, fg_color=BG,
                          border_color=SEP, justify="center")
        e3.grid(row=0, column=2, padx=6, pady=6)
        e4 = ctk.CTkEntry(row, textvariable=n, width=170, height=34, corner_radius=10, fg_color=BG, border_color=SEP)
        e4.grid(row=0, column=3, padx=6, pady=6)
        btn = ctk.CTkButton(row, text="✕", width=36, height=34, fg_color="transparent",
                            hover_color=HOVER, text_color=MUTED, command=lambda: self._del_row(row))
        btn.grid(row=0, column=4, padx=6, pady=6)
        row.grid_columnconfigure(0, weight=1)
        self.rows.append({"frame": row, "url": u, "start": a, "end": b, "name": n})

    def _del_row(self, row):
        row.destroy()
        self.rows = [x for x in self.rows if x["frame"] is not row]

    def open_out(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        try:
            os.startfile(OUT_DIR)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def _journal(self, text):
        self.journal.configure(state="normal")
        self.journal.insert("end", text + "\n")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def start(self):
        if self.running:
            return
        items = []
        for i, r in enumerate(self.rows, 1):
            url = r["url"].get().strip()
            t1 = r["start"].get().strip()
            t2 = r["end"].get().strip()
            name = sanitize_name(r["name"].get(), "frag{:02d}".format(i))
            if not url:
                continue
            if not url.lower().startswith("http"):
                messagebox.showwarning(APP_TITLE, "Рядок {}: посилання має починатися з http".format(i))
                return
            s1 = parse_time(t1)
            s2 = parse_time(t2)
            if s1 is None or s2 is None or s2 <= s1:
                messagebox.showwarning(APP_TITLE, "Рядок {}: перевір час (Початок < Кінець, формат MM:SS).".format(i))
                return
            items.append({"url": url, "start": t1, "end": t2, "name": name})
        if not items:
            messagebox.showwarning(APP_TITLE, "Додай хоча б один фрагмент з посиланням.")
            return
        self.running = True
        self.go.configure(state="disabled")
        self.status.configure(text="● Нарізаю…", text_color="#F59E0B")
        self.bar.configure(mode="indeterminate")
        self.bar.start()
        threading.Thread(target=self._worker, args=(items,), daemon=True).start()

    def _worker(self, items):
        q = self.q

        def say(t):
            q.put(("log", t))

        ok = 0
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            say("Перевіряю інструменти…")
            ffp = ensure_ffmpeg(say)
            ytdlp = ensure_ytdlp(say)
            for idx, it in enumerate(items, 1):
                say("[{}/{}] ✂️ {} ({}–{})…".format(idx, len(items), it["name"], it["start"], it["end"]))
                try:
                    cut_fragment(ytdlp, os.path.dirname(ffp), it, say)
                    ok += 1
                except Exception as e:
                    msg = str(e).strip()
                    last = msg.splitlines()[-1] if msg else "невідома помилка"
                    say("❌ {}: {}".format(it["name"], last))
        except Exception as e:
            say("❌ Помилка підготовки: {}".format(e))
        q.put(("done", ok, len(items)))

    def _finish(self, ok, total):
        self.running = False
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(1 if ok else 0)
        self.go.configure(state="normal")
        if ok:
            self.status.configure(text="● Готово: {}/{} фрагментів".format(ok, total), text_color=GREEN)
            messagebox.showinfo(APP_TITLE, "Нарізано {} з {} фрагментів.\nПапку відкрито.".format(ok, total))
            try:
                os.startfile(OUT_DIR)
            except Exception:
                pass
        else:
            self.status.configure(text="● Не вдалося нарізати жодного фрагмента", text_color="#FF453A")
            messagebox.showerror(APP_TITLE, "Жодного фрагмента не нарізано — подивіться журнал.")

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self._journal(msg[1])
                elif msg[0] == "done":
                    self._finish(msg[1], msg[2])
        except queue.Empty:
            pass
        self.after(120, self._poll)


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
