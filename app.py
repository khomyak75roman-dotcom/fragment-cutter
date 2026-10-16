# -*- coding: utf-8 -*-
"""Fragment Cutter 2.0 — пакетна нарізка фрагментів з YouTube зі списку.

Одне вікно: вставляєш список (рядок = посилання + час, напр.
«https://youtu.be/... 0:47-1:04») — програма послідовно нарізає всі
фрагменти, підписує їх порядковими номерами (01.mp4, 02.mp4, …)
і складає в папку «Відео → Fragments». Нуль API-ключів: yt-dlp і ffmpeg
завантажуються автоматично при першому використанні.
Стиль: CustomTkinter, iOS dark. Повний лог — у файлі fragcut.log.
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
APP_VERSION = "2.0"
UA_STRING = "FragmentCutter/2.0 (Windows)"

OUT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "Fragments")
LOG_FILE = os.path.join(OUT_DIR, "fragcut.log")

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


def ensure_ytdlp(say, force=False):
    """yt-dlp.exe з TOOLS_DIR, або завантажуємо (один раз)."""
    cand = os.path.join(TOOLS_DIR, "yt-dlp.exe")
    if force and os.path.isfile(cand):
        try:
            os.remove(cand)
        except Exception:
            pass
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
    """ID відео з посилання YouTube (для журналу)."""
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return url[:40]


def parse_list(text):
    """Розбір вставленого списку. Рядок = URL + два часи.

    «https://youtu.be/VIDEO_ID 0:47-1:04» → [("https://…", "0:47", "1:04")]
    Розділювач часу: дефіс, тире або пробіл. Коментарі (#) і пусті
    рядки ігноруються. Повертає (валідні, [(№рядка, причина), …]).
    """
    items = []
    bad = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.search(r"https?://\S+", line)
        if not m:
            bad.append((i, "немає посилання (https://…)"))
            continue
        url = m.group(0).rstrip(",;")
        rest = line.replace(url, " ", 1)
        times = re.findall(r"\d{1,2}(?::\d{2}){1,2}", rest)
        if len(times) < 2:
            bad.append((i, "потрібно два часи: «0:47-1:04» після посилання"))
            continue
        t1, t2 = times[0], times[1]
        s1 = parse_time(t1)
        s2 = parse_time(t2)
        if s1 is None or s2 is None:
            bad.append((i, "не зрозумів час «{}»".format(t1) if s1 is None else "не зрозумів час «{}»".format(t2)))
            continue
        if s2 <= s1:
            bad.append((i, "початок {} має бути МЕНШИМ за кінець {}".format(t1, t2)))
            continue
        items.append((url, t1, t2))
    return items, bad


def ytdlp_run(ytdlp, ffdir, url, t1, t2, out, extra):
    """Один запуск yt-dlp на нарізку фрагмента."""
    cmd = [
        ytdlp,
        "--ffmpeg-location", ffdir,
        "--download-sections", "*{}-{}".format(t1, t2),
        "--force-keyframes-at-cuts",
        "-f", "bv*+ba/b",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--windows-filenames",
        "-o", out,
    ] + extra + [url]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE + " " + APP_VERSION)
        self.configure(fg_color=BG)
        w, h = 920, 780
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry("{}x{}+{}+{}".format(w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2)))
        self.minsize(860, 700)

        self.q = queue.Queue()
        self.running = False

        self._build()
        self.after(120, self._poll)
        self.bind("<Control-Return>", lambda e: self.start())

    def _build(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=28, pady=(24, 4))
        ctk.CTkLabel(head, text="✂️ Fragment Cutter", font=("Segoe UI", 26, "bold"), text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(head, text="Список фрагментів у одному вікні • один рядок = один фрагмент • без API-ключів",
                     font=("Segoe UI", 13), text_color=MUTED).pack(anchor="w")

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=24)
        card.pack(fill="both", expand=True, padx=28, pady=(14, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=22, pady=18)

        ctk.CTkLabel(inner, text="📋 Список фрагментів (встав сюди)",
                     font=("Segoe UI", 15, "bold"), text_color=TEXT).pack(anchor="w")
        self.source = ctk.CTkTextbox(inner, height=180, corner_radius=12, fg_color=INSET,
                                     font=("Consolas", 13), text_color=TEXT)
        self.source.pack(fill="both", expand=True, pady=(6, 8))

        ctk.CTkLabel(inner, text="Один рядок = один фрагмент: посилання 0:47-1:04\n"
                                 "Розділювач часу: дефіс, тире або пробіл • рядки з # ігноруються • Ctrl+Enter = нарізати\n"
                                 "Напр.: https://www.youtube.com/watch?v=DW2w97ccmMY 0:47-1:04",
                     font=("Segoe UI", 12), text_color=MUTED, justify="left").pack(anchor="w")

        frow = ctk.CTkFrame(inner, fg_color="transparent")
        frow.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(frow, text="📂 Папка результатів", height=34, corner_radius=17,
                      fg_color=INSET, hover_color=HOVER, command=self.open_out).pack(side="left")
        ctk.CTkButton(frow, text="📋 Копіювати журнал", height=34, corner_radius=17,
                      fg_color=INSET, hover_color=HOVER, command=self.copy_journal).pack(side="left", padx=(8, 0))
        ctk.CTkButton(frow, text="📂 Лог-файл", height=34, corner_radius=17,
                      fg_color=INSET, hover_color=HOVER, command=self.open_log).pack(side="left", padx=(8, 0))
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
        self.go = ctk.CTkButton(binner, text="✂️ НАРІЗАТИ ФРАГМЕНТИ", height=52, corner_radius=26,
                                font=("Segoe UI", 17, "bold"), fg_color=GREEN, hover_color="#28B14C",
                                text_color="#000000", command=self.start)
        self.go.pack(fill="x")

        self.journal = ctk.CTkTextbox(self, height=160, fg_color=CARD, corner_radius=16, font=("Consolas", 12))
        self.journal.pack(fill="x", padx=28, pady=(14, 24))
        self.journal.configure(state="disabled")

    def open_out(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        try:
            os.startfile(OUT_DIR)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def open_log(self):
        try:
            if not os.path.isfile(LOG_FILE):
                messagebox.showinfo(APP_TITLE, "Лог-файл з'явиться після першої нарізки:\n" + LOG_FILE)
                return
            os.startfile(LOG_FILE)
        except Exception as e:
            messagebox.showerror(APP_TITLE, str(e))

    def copy_journal(self):
        """Копіювати весь журнал у буфер обміну."""
        text = self.journal.get("1.0", "end").strip()
        if not text:
            self._journal("Журнал порожній")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._journal("📋 Журнал скопійовано — встав його у чат")

    def _journal(self, text):
        self.journal.configure(state="normal")
        self.journal.insert("end", text + "\n")
        self.journal.see("end")
        self.journal.configure(state="disabled")

    def _say(self, text):
        """Повідомлення в журнал (через чергу) + у лог-файл."""
        self.q.put(("log", text))
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _log_raw(self, text):
        """Сирі деталі (повний вивід yt-dlp) — лише у лог-файл."""
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        raw = self.source.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning(APP_TITLE, "Встав список фрагментів.\nОдин рядок: посилання 0:47-1:04")
            return
        items, bad = parse_list(raw)
        for ln, why in bad[:6]:
            self._journal("⚠️ Рядок {}: {}".format(ln, why))
        if bad:
            self._journal("⚠️ Хибних рядків: {} — їх пропущено".format(len(bad)))
        if not items:
            messagebox.showwarning(APP_TITLE, "Не знайшов жодного валідного фрагмента.\n\n"
                                             "Приклад рядка:\nhttps://youtu.be/VIDEO_ID 0:47-1:04")
            return
        self.running = True
        self.go.configure(state="disabled")
        self.status.configure(text="● Наріжаю 1/{}…".format(len(items)), text_color="#F59E0B")
        self.bar.configure(mode="indeterminate")
        self.bar.start()
        self._journal("📋 До нарізки: {} фрагментів".format(len(items)))
        threading.Thread(target=self._worker, args=(items,), daemon=True).start()

    def _worker(self, items):
        q = self.q
        say = self._say
        total = len(items)
        done_ok = 0
        failed = []
        tools = {}
        try:
            os.makedirs(OUT_DIR, exist_ok=True)
            say("Перевіряю інструменти…")
            ffp = ensure_ffmpeg(say)
            tools["ytdlp"] = ensure_ytdlp(say)
            tools["ffdir"] = os.path.dirname(ffp)
            for i, (url, t1, t2) in enumerate(items, 1):
                num = "{:02d}".format(i)
                out = os.path.join(OUT_DIR, num + ".mp4")
                q.put(("stat", "● Наріжаю {}/{}…".format(i, total)))
                say("▶️ Фрагмент {} з {}: {} ({}–{})".format(i, total, video_id(url), t1, t2))
                ok = self._cut_one(say, tools, url, t1, t2, out, num)
                if ok:
                    done_ok += 1
                    say("✅ Готово: {}.mp4".format(num))
                else:
                    failed.append(num)
                    say("⏭️ Далі…")
        except Exception as e:
            say("❌ Помилка: {}".format(e))
        say("=== Готово: {} з {} ===".format(done_ok, total))
        if failed:
            say("❌ Не вдалося: " + ", ".join(failed))
        q.put(("done", done_ok, total, failed))

    def _cut_one(self, say, tools, url, t1, t2, out, num):
        """Нарізка одного фрагмента; True якщо файл готовий."""
        try:
            if os.path.exists(out):
                say("♻️ {} вже є — перезаписую".format(num))
                try:
                    os.remove(out)
                except Exception:
                    pass
            try:
                proc = ytdlp_run(tools["ytdlp"], tools["ffdir"], url, t1, t2, out, [])
            except OSError:
                say("❗ yt-dlp не запускається (ймовірно, антивірус його видалив) — завантажую заново…")
                tools["ytdlp"] = ensure_ytdlp(say, force=True)
                proc = ytdlp_run(tools["ytdlp"], tools["ffdir"], url, t1, t2, out, [])
            if proc is None:
                say("❌ yt-dlp не запустився")
                return False
            if proc.returncode == 0 and os.path.isfile(out):
                return True
            err = (proc.stdout or "") + "\n" + (proc.stderr or "")
            self._log_raw("=== [{}] yt-dlp повний вивід ===\n{}".format(num, err))
            if "Sign in to confirm" in err or "not a bot" in err:
                say("🤖 YouTube просить підтвердження «не робот» — пробую інший спосіб…")
                proc2 = ytdlp_run(tools["ytdlp"], tools["ffdir"], url, t1, t2, out,
                                  ["--extractor-args", "youtube:player_client=tv"])
                if proc2.returncode == 0 and os.path.isfile(out):
                    return True
                err2 = (proc2.stdout or "") + "\n" + (proc2.stderr or "")
                self._log_raw("=== [{}] повторний вивід (player_client=tv) ===\n{}".format(num, err2))
                err = err + "\n" + err2
            lines = [x.strip() for x in err.splitlines() if x.strip()]
            errs = [x for x in lines if x.startswith("ERROR")][:3]
            if errs:
                for line in errs:
                    say("❌ " + line[:200])
            else:
                for line in lines[-3:]:
                    say("ℹ️ " + line[:200])
            left = [f for f in os.listdir(OUT_DIR) if f.startswith(num + ".")]
            if left:
                say("⚠️ У папці лишився файл: " + ", ".join(left)[:200])
            return False
        except Exception as e:
            say("❌ Помилка: {}".format(e))
            return False

    def _finish(self, done_ok, total, failed):
        self.running = False
        self.bar.stop()
        self.bar.configure(mode="determinate")
        self.bar.set(done_ok / total if total else 0)
        self.go.configure(state="normal")
        if not failed:
            self.status.configure(text="● Готово: {} з {}".format(done_ok, total), text_color=GREEN)
            messagebox.showinfo(APP_TITLE, "Нарізано {} з {} фрагментів.\nПапку відкрито.".format(done_ok, total))
            try:
                os.startfile(OUT_DIR)
            except Exception:
                pass
        elif done_ok:
            self.status.configure(text="● Готово: {} з {} (не вдалися: {})".format(done_ok, total, ", ".join(failed)),
                                  text_color="#F59E0B")
            messagebox.showwarning(APP_TITLE, "Нарізано {} з {}.\nНе вдалося: {}.\n\n"
                                           "Натисни «📋 Копіювати журнал» і надішли текст.".format(done_ok, total, ", ".join(failed)))
            try:
                os.startfile(OUT_DIR)
            except Exception:
                pass
        else:
            self.status.configure(text="● Не вдалося нарізати", text_color="#FF453A")
            messagebox.showerror(APP_TITLE,
                                 "Фрагменти не нарізано.\nНатисни «📋 Копіювати журнал» "
                                 "і надішли текст.\nПовні деталі у файлі:\n" + LOG_FILE)

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "log":
                    self._journal(msg[1])
                elif msg[0] == "stat":
                    self.status.configure(text=msg[1], text_color="#F59E0B")
                elif msg[0] == "done":
                    self._finish(msg[1], msg[2], msg[3])
        except queue.Empty:
            pass
        self.after(120, self._poll)


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
