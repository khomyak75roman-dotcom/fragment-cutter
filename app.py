# -*- coding: utf-8 -*-
"""Fragment Cutter 1.1 — нарізка фрагмента з YouTube за таймкодами.

Один файл, нуль API-ключів: yt-dlp і ffmpeg завантажуються автоматично
при першому використанні. Результати зберігаються у папці
«Відео → Fragments» у профілю користувача. Стиль: CustomTkinter, iOS dark.
"""

import os
import re
import sys
import time
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
APP_VERSION = "1.1"
UA_STRING = "FragmentCutter/1.1 (Windows)"

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


def video_id(url):
    """ID відео з посилання YouTube (для імені файлу)."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return time.strftime("%Y%m%d_%H%M%S")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE + " " + APP_VERSION)
        self.configure(fg_color=BG)
        w, h = 900, 620
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry("{}x{}+{}+{}".format(w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        self.minsize(840, 560)

        self.q = queue.Queue()
        self.running = False

        self.url = ctk.StringVar()
        self.t1 = ctk.StringVar()
        self.t2 = ctk.StringVar()

        self._build()
        self.after(120, self._poll)
        self.bind("<Control-Return>", lambda e: self.start())

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(24, 4))
        ctk.CTkLabel(head, text="✂️ Fragment Cutter", font=("Segoe UI", 26, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(head, text="Нарізка фрагмента з YouTube за таймкодами • без API-ключів",
                     font=("Segoe UI", 13), text_color=MUTED).pack(anchor="w")

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=24)
        card.pack(fill="x", padx=28, pady=(14, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=22, pady=18)

        ctk.CTkLabel(inner, text="🔗 Посилання на відео YouTube", font=("Segoe UI", 15, "bold"),
                     text_color=TEXT).pack(anchor="w")
        row_url = ctk.CTkFrame(inner, fg_color="transparent")
        row_url.pack(fill="x", pady=(6, 12))
        e_url = ctk.CTkEntry(row_url, textvariable=self.url, height=44, corner_radius=12,
                             fg_color=INSET, border_color=SEP, font=("Segoe UI", 14))
        e_url.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row_url, text="📋", width=52, height=44, corner_radius=12, fg_color=INSET,
                      hover_color=HOVER, command=lambda: self._paste(self.url)).pack(side="left", padx=(8, 0))

        row_time = ctk.CTkFrame(inner, fg_color="transparent")
        row_time.pack(fill="x")
        col1 = ctk.CTkFrame(row_time, fg_color="transparent")
        col1.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(col1, text="⏱ Час початку (MM:SS)", font=("Segoe UI", 15, "bold"),
                     text_color=TEXT).pack(anchor="w")
        r1 = ctk.CTkFrame(col1, fg_color="transparent")
        r1.pack(fill="x", pady=(6, 0))
        e1 = ctk.CTkEntry(r1, textvariable=self.t1, height=44, corner_radius=12,
                          fg_color=INSET, border_color=SEP, justify="center", font=("Segoe UI", 14))
        e1.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r1, text="📋", width=52, height=44, corner_radius=12, fg_color=INSET,
                      hover_color=HOVER, command=lambda: self._paste(self.t1)).pack(side="left", padx=(8, 0))
        col2 = ctk.CTkFrame(row_time, fg_color="transparent")
        col2.pack(side="left", fill="both", expand=True, padx=(14, 0))
        ctk.CTkLabel(col2, text="🏁 Час завершення (MM:SS)", font=("Segoe UI", 15, "bold"),
                     text_color=TEXT).pack(anchor="w")
        r2 = ctk.CTkFrame(col2, fg_color="transparent")
        r2.pack(fill="x", pady=(6, 0))
        e2 = ctk.CTkEntry(r2, textvariable=self.t2, height=44, corner_radius=12,
                          fg_color=INSET, border_color=SEP, justify="center", font=("Segoe UI", 14))
        e2.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r2, text="📋", width=52, height=44, corner_radius=12, fg_color=INSET,
                      hover_color=HOVER, command=lambda: self._paste(self.t2)).pack(side="left", padx=(8, 0))

        ctk.CTkLabel(inner, text="Формат часу: MM:SS або H:MM:SS • Ctrl+Enter = нарізати",
                     font=("Segoe UI", 12), text_color=MUTED).pack(anchor="w", pady=(10, 0))

        frow = ctk.CTkFrame(inner, fg_color="transparent")
        frow.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(frow, text="📂 Папка результатів", height=34, corner_radius=17,
                      fg_color=INSET, hover_color=HOVER, command=self.open_out).pack(side="left")
        ctk.CTkLabel(frow, text="📁 " + OUT_DIR, font=("Segoe UI", 12), text_color=MUTED).pack(side="right")

        bottom = ctk.CTkFrame(self, fg_color=CARD, corner_radius=24)
        bottom.pack(fill="x", padx=28, pady=(14, 0))
        binner = ctk.CTkFrame(bottom, fg_color="transparent")
        binner.pack(fill="x", padx=22, pady=16)

        self.status = ctk.CTkLabel(binner, text="Готово до нарізки", font=("Segoe UI", 13), text_color=MUTED)
        self.status.pack(anchor="w")
        self.bar = ctk.CTkProgressBar(binner, height=6, corner_radius=3, fg_color=INSET, progress_color=ACCENT)
        self.bar.pack(fill="x", pady=(8, 12))
        self.bar.set(0)
        self.go = ctk.CTkButton(binner, text="✂️ НАРІЗАТИ ФРАГМЕНТ", height=52, corner_radius=26,
                                font=("Segoe UI", 17, "bold"), fg_color=GREEN, hover_color="#28B14C",
                                text_color="#000000", command=self.start)
        self.go.pack(fill="x")

        self.journal = ctk.CTkTextbox(self, height=150, fg_color=CARD, corner_radius=16, font=("Consolas", 12))
        self.journal.pack(fill="x", padx=28, pady=(14, 24))
        self.journal.configure(state="disabled")

    def _paste(self, var):
        """Вставка з буфера обміну у поле."""
        try:
            t = self.clipboard_get()
        except Exception:
            self._journal("📋 Буфер обміну порожній")
            return
        var.set(t.strip())
        self._journal("📋 Вставлено: " + t.strip())

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
        url = self.url.get().strip()
        t1 = self.t1.get().strip()
        t2 = self.t2.get().strip()
        if not url:
            messagebox.showwarning(APP_TITLE, "Встав посилання на відео YouTube.")
            return
        if not url.lower().startswith("http"):
            messagebox.showwarning(APP_TITLE, "Посилання має починатися з http")
            return
        s1 = parse_time(t1)
        s2 = parse_time(t2)
        if s1 is None or s2 is None or s2 <= s1:
            messagebox.showwarning(APP_TITLE, "Перевір час: Початок < Кінець, формат MM:SS.")
            return
        name = "frag_{}_{}-{}".format(video_id(url), t1.replace(":", "-"), t2.replace(":", "-"))
        self.running = True
        self.go.configure(state="disabled")
        self.status.configure(text="● Нарізаю…", text_color="#F59E0B")
        self.bar.configure(mode="indeterminate")
        self.bar.start()
        threading.Thread(target=self._worker, args=(url, t1, t2, name), daemon=True).start()

    def _worker(self, url, t1, t2, name):
        q = self.q

        def say(t):
            q.put(("log", t))

        ok = False
        out = os.path.join(OUT_DIR, name + ".mp4")
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            say("Перевіряю інструменти…")
            ffp = ensure_ffmpeg(say)
            ytdlp = ensure_ytdlp(say)
            say("✂️ Нарізаю {} ({}–{})…".format(name, t1, t2))
            if os.path.exists(out):
                try:
                    os.remove(out)
                except Exception:
                    pass
            cmd = [
                ytdlp,
                "--ffmpeg-location", os.path.dirname(ffp),
                "--download-sections", "*{}-{}".format(t1, t2),
                "--force-keyframes-at-cuts",
                "-f", "bv*+ba/b",
                "--no-playlist",
                "--windows-filenames",
                "-o", out,
                url,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode == 0 and os.path.isfile(out):
                ok = True
                say("✅ Готово: " + out)
            else:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                say("❌ " + (tail[-1] if tail else "не вдалося завантажити фрагмент"))
        except Exception as e:
            say("❌ Помилка: {}".format(e))
        q.put(("done", ok, out))

    def _finish(self, ok, out):
        self.running = False
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(1 if ok else 0)
        self.go.configure(state="normal")
        if ok:
            self.status.configure(text="● Готово", text_color=GREEN)
            messagebox.showinfo(APP_TITLE, "Фрагмент нарізано.\nПапку відкрито.")
            try:
                os.startfile(OUT_DIR)
            except Exception:
                pass
        else:
            self.status.configure(text="● Не вдалося нарізати", text_color="#FF453A")
            messagebox.showerror(APP_TITLE, "Фрагмент не нарізано — подивіться журнал.")

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
