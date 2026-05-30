#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎬 Resolve Converter — deux outils en un :
  • Import → Resolve : transcode n'importe quelle vidéo en intermédiaire
    (DNxHR) lisible par la version gratuite de DaVinci Resolve sous Linux.
  • Export → Compression : ré-encode ta vidéo sortie de Resolve en H.264/H.265
    via NVENC (GPU), avec un aperçu live qui se compresse exactement comme
    la vidéo le sera quand tu bouges le curseur de force.

Dépendances :
    sudo pacman -S pyside6 ffmpeg      # ffmpeg-git de l'AUR marche aussi
    (la compression GPU nécessite un GPU NVIDIA avec NVENC)
Lancement :
    python resolve_converter.py
"""

import os
import sys
import json
import tempfile
import subprocess
from pathlib import Path
from shutil import which, rmtree

from PySide6.QtCore import Qt, QProcess, QThread, QTimer, Signal, QRectF
from PySide6.QtGui import QFont, QImage, QPixmap, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QListWidget, QListWidgetItem,
    QProgressBar, QFileDialog, QFrame, QInputDialog, QTabWidget,
    QSlider, QSizePolicy,
)

# ---------------------------------------------------------------------------
# IMPORT : codecs intermédiaires acceptés par Resolve (codec, profil, pix_fmt)
# ---------------------------------------------------------------------------
PRESETS = {
    "Standard (8-bit)":  ("dnxhd", "dnxhr_hq",  "yuv422p"),
    "10-bit (log/HDR)":  ("dnxhd", "dnxhr_hqx", "yuv422p10le"),
}

# ---------------------------------------------------------------------------
# EXPORT : codecs de livraison via NVENC (GPU NVIDIA)
# ---------------------------------------------------------------------------
DELIVERY = {
    "H.265 (GPU, rapide)":      "hevc_nvenc",
    "H.264 (GPU, compatible)":  "h264_nvenc",
}
QP_MIN, QP_MAX = 18, 40   # quantizer NVENC : bas = léger/propre, haut = fort/petit

VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".flv", ".wmv",
    ".mpg", ".mpeg", ".ts", ".m2ts", ".mts", ".3gp", ".ogv", ".vob",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def missing_bins() -> list[str]:
    return [b for b in ("ffmpeg", "ffprobe") if which(b) is None]


def hms_to_secs(t: str) -> float:
    try:
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0


def probe(path: str) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=20,
        ).stdout
        data = json.loads(out or "{}")
    except Exception:
        return {"has_video": False, "duration": 0.0, "vcodec": "?"}
    vcodec, has_video = "?", False
    for st in data.get("streams", []):
        if st.get("codec_type") == "video":
            has_video = True
            vcodec = st.get("codec_name", "?")
            break
    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    return {"has_video": has_video, "duration": duration, "vcodec": vcodec}


def make_output(src: str, stem: str, ext: str) -> str:
    """dir/stem.ext à côté de la source, sans jamais écraser."""
    d = Path(src).parent
    cand = d / f"{stem}.{ext}"
    i = 1
    while cand.exists():
        cand = d / f"{stem}_{i}.{ext}"
        i += 1
    return str(cand)


def slider_to_qp(v: int) -> int:
    return round(QP_MIN + (v / 100) * (QP_MAX - QP_MIN))


def qp_label(qp: int) -> str:
    if qp <= 25:
        return "Léger"
    if qp <= 33:
        return "Moyen"
    return "Fort"


# ---------------------------------------------------------------------------
# Zone de drop réutilisable
# ---------------------------------------------------------------------------
class DropZone(QFrame):
    BASE = """
        QFrame#drop { background:#222222; border:2px dashed #4A4A4A; border-radius:6px; }
        QFrame#drop QLabel { background:transparent; border:none; }
    """
    HOVER = """
        QFrame#drop { background:#28323C; border:2px dashed #4D8FCC; border-radius:6px; }
        QFrame#drop QLabel { background:transparent; border:none; }
    """

    def __init__(self, on_drop, main="Glisse tes vidéos ici",
                 sub="n'importe quel format · clic pour parcourir"):
        super().__init__()
        self.on_drop = on_drop
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setStyleSheet(self.BASE)
        self.setMinimumHeight(130)

        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        icon = QLabel("🎬"); icon.setAlignment(Qt.AlignCenter); icon.setFont(QFont("", 34))
        txt = QLabel(main); txt.setAlignment(Qt.AlignCenter)
        txt.setStyleSheet("color:#D6D6D6; font-size:15px; font-weight:600;")
        s = QLabel(sub); s.setAlignment(Qt.AlignCenter)
        s.setStyleSheet("color:#7E7E7E; font-size:12px;")
        lay.addWidget(icon); lay.addWidget(txt); lay.addWidget(s)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); self.setStyleSheet(self.HOVER)

    def dragLeaveEvent(self, e):
        self.setStyleSheet(self.BASE)

    def dropEvent(self, e):
        self.setStyleSheet(self.BASE)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.on_drop(paths)

    def mousePressEvent(self, e):
        files, _ = QFileDialog.getOpenFileNames(self, "Choisir des vidéos")
        if files:
            self.on_drop(files)


# ===========================================================================
# ONGLET 1 — IMPORT → RESOLVE (transcode intermédiaire)
# ===========================================================================
class ImportTab(QWidget):
    def __init__(self):
        super().__init__()
        self.proc: QProcess | None = None
        self.queue: list[dict] = []
        self.current: dict | None = None
        self.total = self.done = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(12)

        row = QHBoxLayout()
        row.addWidget(QLabel("Format de sortie :"))
        self.combo = QComboBox(); self.combo.addItems(PRESETS.keys())
        row.addWidget(self.combo, 1)
        lay.addLayout(row)

        lay.addWidget(DropZone(self.add_files))

        hint = QLabel("💡 double-clic sur un fichier en attente pour renommer la sortie")
        hint.setStyleSheet("color:#7E7E7E; font-size:11px;")
        lay.addWidget(hint)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.rename_item)
        lay.addWidget(self.list, 1)

        self.status = QLabel("Prêt — glisse des fichiers pour commencer ✨")
        self.status.setStyleSheet("color:#9A9A9A; font-size:13px;")
        lay.addWidget(self.status)

        self.bar = QProgressBar(); self.bar.setTextVisible(False)
        lay.addWidget(self.bar)

        clear = QPushButton("🧹 Vider la liste"); clear.clicked.connect(self.clear_list)
        lay.addWidget(clear)

        if missing_bins():
            self.status.setText(f"⚠️ Introuvable : {', '.join(missing_bins())}")
            self.status.setStyleSheet("color:#E05A5A; font-weight:600;")

    def fmt(self, job, status):
        return f"{status} {job['name']}  →  {Path(job['dst']).name}"

    def add_files(self, paths):
        for p in paths:
            name = Path(p).name
            info = probe(p)
            item = QListWidgetItem()
            if not info["has_video"]:
                item.setText(f"❌ {name} — pas de piste vidéo")
                self.list.addItem(item); continue
            job = {"src": p, "dst": make_output(p, f"{Path(p).stem}_resolve", "mov"),
                   "dur": info["duration"], "item": item, "name": name}
            item.setText(self.fmt(job, "⏳"))
            self.list.addItem(item); self.queue.append(job); self.total += 1
        self.start()

    def rename_item(self, item):
        job = next((j for j in self.queue if j["item"] is item), None)
        if job is None:
            return
        new, ok = QInputDialog.getText(self, "Renommer la sortie",
                                       "Nom du fichier (sans extension) :",
                                       text=Path(job["dst"]).stem)
        if ok and new.strip():
            job["dst"] = make_output(job["src"], new.strip(), "mov")
            item.setText(self.fmt(job, "⏳"))

    def start(self):
        if self.proc is not None or not self.queue:
            return
        self.combo.setEnabled(False)
        self.current = self.queue.pop(0)
        self.current["item"].setText(self.fmt(self.current, "🔄"))
        self.status.setText(f"Conversion {self.done + 1}/{self.total} — {self.current['name']}")
        codec, profile, pixfmt = PRESETS[self.combo.currentText()]
        args = ["-y", "-i", self.current["src"], "-map", "0:v:0", "-map", "0:a?",
                "-c:v", codec, "-profile:v", profile, "-pix_fmt", pixfmt,
                "-c:a", "pcm_s16le", "-progress", "pipe:1", "-nostats",
                self.current["dst"]]
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self.on_progress)
        self.proc.finished.connect(self.on_finished)
        self.bar.setRange(0, 100) if self.current["dur"] > 0 else self.bar.setRange(0, 0)
        self.bar.setValue(0)
        self.proc.start("ffmpeg", args)

    def on_progress(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "ignore")
        dur = self.current["dur"]
        for line in data.splitlines():
            if line.startswith("out_time=") and dur > 0:
                secs = hms_to_secs(line.split("=", 1)[1].strip())
                self.bar.setValue(min(100, int(secs / dur * 100)))

    def on_finished(self, code, _s):
        ok = code == 0 and Path(self.current["dst"]).exists()
        self.current["item"].setText(self.fmt(self.current, "✅" if ok else "❌"))
        if ok:
            self.done += 1
        self.proc.deleteLater(); self.proc = None; self.current = None
        if self.queue:
            self.start()
        else:
            self.bar.setRange(0, 100); self.bar.setValue(100)
            self.combo.setEnabled(True)
            self.status.setText(f"Terminé 🎉  ({self.done}/{self.total} réussis)")

    def clear_list(self):
        if self.proc is not None:
            return
        self.list.clear(); self.queue.clear()
        self.total = self.done = 0; self.bar.setValue(0)
        self.combo.setEnabled(True)
        self.status.setText("Prêt — glisse des fichiers pour commencer ✨")


# ===========================================================================
# Worker d'aperçu : encode UNE frame au qp choisi, puis la redécode.
# Tourne dans un thread pour ne pas geler l'UI.
# ===========================================================================
class PreviewWorker(QThread):
    done = Signal(QImage)
    fail = Signal(str)

    def __init__(self, src_png, codec, qp, tmpdir):
        super().__init__()
        self.src, self.codec, self.qp, self.tmp = src_png, codec, qp, tmpdir

    def run(self):
        enc = os.path.join(self.tmp, "prev_enc.mp4")
        out = os.path.join(self.tmp, "prev_out.png")
        try:
            # encode la frame avec le VRAI codec, au quantizer choisi
            subprocess.run(
                ["ffmpeg", "-y", "-i", self.src, "-frames:v", "1",
                 "-c:v", self.codec, "-rc", "constqp", "-qp", str(self.qp),
                 "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p", enc],
                capture_output=True, timeout=60, check=True)
            # redécode -> PNG pour afficher les artefacts réels
            subprocess.run(
                ["ffmpeg", "-y", "-i", enc, "-frames:v", "1", out],
                capture_output=True, timeout=60, check=True)
            img = QImage(out)
            if img.isNull():
                self.fail.emit("aperçu illisible"); return
            self.done.emit(img)
        except subprocess.CalledProcessError:
            self.fail.emit("échec encodage — NVENC dispo ?")
        except Exception as e:
            self.fail.emit(str(e))


# ===========================================================================
# Comparateur avant/après : original à gauche, compressé à droite,
# ligne déplaçable à la souris. Le côté compressé se met à jour en live.
# ===========================================================================
class ComparePreview(QFrame):
    def __init__(self):
        super().__init__()
        self.original: QImage | None = None
        self.compressed: QImage | None = None
        self.split = 0.5
        self.qp_text = ""
        self.placeholder = "Charge une vidéo pour voir l'aperçu"
        self.setMinimumHeight(260)
        self.setStyleSheet(
            "background:#161616; border:1px solid #3D3D3D; border-radius:4px;")
        self.setCursor(Qt.SplitHCursor)

    def set_original(self, img):
        self.original = img; self.compressed = None; self.update()

    def set_compressed(self, img):
        self.compressed = img; self.update()

    def set_qp(self, qp):
        self.qp_text = f"qp {qp}"

    def _img_rect(self):
        img = self.original or self.compressed
        if img is None or img.isNull():
            return None
        iw, ih = img.width(), img.height()
        scale = min(self.width() / iw, self.height() / ih)
        dw, dh = iw * scale, ih * scale
        return (self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh

    def _tag(self, p, text, x, y, right=False):
        fm = p.fontMetrics()
        w = fm.horizontalAdvance(text) + 14
        h = fm.height() + 6
        bx = x - w if right else x
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(QRectF(bx, y, w, h), 6, 6)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(QRectF(bx, y, w, h), Qt.AlignCenter, text)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self._img_rect()
        if rect is None:
            p.setPen(QColor("#7E7E7E"))
            p.drawText(self.rect(), Qt.AlignCenter, self.placeholder)
            p.end(); return
        ox, oy, dw, dh = rect
        target = QRectF(ox, oy, dw, dh)
        split_x = ox + dw * self.split
        comp = self.compressed or self.original
        # gauche = original
        if self.original and not self.original.isNull():
            p.save(); p.setClipRect(QRectF(ox, oy, dw * self.split, dh))
            p.drawImage(target, self.original); p.restore()
        # droite = compressé (ou original tant que pas encore rendu)
        if comp and not comp.isNull():
            p.save(); p.setClipRect(QRectF(split_x, oy, ox + dw - split_x, dh))
            p.drawImage(target, comp); p.restore()
        # ligne de séparation
        pen = QPen(QColor("#FFFFFF")); pen.setWidth(2)
        p.setPen(pen); p.drawLine(int(split_x), int(oy), int(split_x), int(oy + dh))
        p.setBrush(QColor("#4D8FCC")); p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawEllipse(QRectF(split_x - 7, oy + dh / 2 - 7, 14, 14))
        # étiquettes
        self._tag(p, "ORIGINAL", ox + 8, oy + 8)
        self._tag(p, f"COMPRESSÉ · {self.qp_text}", ox + dw - 8, oy + 8, right=True)
        p.end()

    def _set_split_from(self, e):
        rect = self._img_rect()
        if rect is None:
            return
        ox, _, dw, _ = rect
        frac = (e.position().x() - ox) / dw
        self.split = min(1.0, max(0.0, frac))
        self.update()

    def mousePressEvent(self, e):
        self._set_split_from(e)

    def mouseMoveEvent(self, e):
        self._set_split_from(e)


# ===========================================================================
# ONGLET 2 — EXPORT → COMPRESSION (NVENC + aperçu live)
# ===========================================================================
class CompressTab(QWidget):
    def __init__(self, tmpdir):
        super().__init__()
        self.tmp = tmpdir
        self.video: str | None = None
        self.dur = 0.0
        self.preview_src: str | None = None
        self.original_img: QImage | None = None
        self.compressed_img: QImage | None = None
        self.worker: PreviewWorker | None = None
        self.pending_qp: int | None = None
        self.proc: QProcess | None = None

        self.debounce = QTimer(self); self.debounce.setSingleShot(True)
        self.debounce.setInterval(180); self.debounce.timeout.connect(self.request_preview)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16); lay.setSpacing(12)

        row = QHBoxLayout()
        row.addWidget(QLabel("Codec :"))
        self.combo = QComboBox(); self.combo.addItems(DELIVERY.keys())
        self.combo.currentIndexChanged.connect(self.schedule_preview)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)

        lay.addWidget(DropZone(self.load_video,
                               main="Glisse ta vidéo exportée ici",
                               sub="le .mov sorti de Resolve · clic pour parcourir"))

        # Aperçu comparateur avant/après
        self.preview = ComparePreview()
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self.preview, 1)

        hint = QLabel("💡 glisse la ligne rose pour comparer avant / après")
        hint.setStyleSheet("color:#7E7E7E; font-size:11px;")
        lay.addWidget(hint)

        # Curseur de force
        self.qlabel = QLabel("Force : Moyen  (qp 29)")
        self.qlabel.setStyleSheet("color:#4D8FCC; font-weight:600;")
        lay.addWidget(self.qlabel)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Léger"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100); self.slider.setValue(50)
        self.slider.valueChanged.connect(self.on_slider)
        srow.addWidget(self.slider, 1)
        srow.addWidget(QLabel("Fort"))
        lay.addLayout(srow)

        self.status = QLabel("")
        self.status.setStyleSheet("color:#9A9A9A; font-size:13px;")
        lay.addWidget(self.status)

        self.bar = QProgressBar(); self.bar.setTextVisible(False)
        lay.addWidget(self.bar)

        self.go = QPushButton("⚡ Compresser la vidéo")
        self.go.setObjectName("primary")
        self.go.clicked.connect(self.compress)
        lay.addWidget(self.go)

        if missing_bins():
            self.status.setText(f"⚠️ Introuvable : {', '.join(missing_bins())}")
            self.status.setStyleSheet("color:#E05A5A; font-weight:600;")

    # -- chargement -------------------------------------------------------
    def load_video(self, paths):
        p = paths[0]
        info = probe(p)
        if not info["has_video"]:
            self.status.setText("❌ pas de piste vidéo"); return
        self.video, self.dur = p, info["duration"]
        self.preview_src = os.path.join(self.tmp, "src.png")
        mid = self.dur / 2 if self.dur > 0 else 0
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", str(mid), "-i", p, "-frames:v", "1",
                 "-q:v", "2", self.preview_src],
                capture_output=True, timeout=60, check=True)
        except Exception:
            self.status.setText("❌ extraction de la frame échouée"); return
        self.original_img = QImage(self.preview_src)
        self.preview.set_original(self.original_img)
        self.preview.set_qp(slider_to_qp(self.slider.value()))
        self.status.setText(f"Chargé : {Path(p).name}")
        self.schedule_preview()

    # -- curseur ----------------------------------------------------------
    def on_slider(self, v):
        qp = slider_to_qp(v)
        self.qlabel.setText(f"Force : {qp_label(qp)}  (qp {qp})")
        self.preview.set_qp(qp)
        self.schedule_preview()

    def schedule_preview(self):
        if self.preview_src:
            self.debounce.start()

    # -- aperçu -----------------------------------------------------------
    def request_preview(self):
        if not self.preview_src:
            return
        qp = slider_to_qp(self.slider.value())
        if self.worker is not None and self.worker.isRunning():
            self.pending_qp = qp; return
        self.launch_preview(qp)

    def launch_preview(self, qp):
        codec = DELIVERY[self.combo.currentText()]
        self.worker = PreviewWorker(self.preview_src, codec, qp, self.tmp)
        self.worker.done.connect(self.on_preview)
        self.worker.fail.connect(lambda m: self.status.setText(f"⚠️ {m}"))
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def on_worker_finished(self):
        if self.pending_qp is not None:
            qp, self.pending_qp = self.pending_qp, None
            self.launch_preview(qp)

    def on_preview(self, img):
        self.compressed_img = img
        self.preview.set_compressed(img)

    # -- compression complète --------------------------------------------
    def compress(self):
        if self.video is None or self.proc is not None:
            return
        codec = DELIVERY[self.combo.currentText()]
        qp = slider_to_qp(self.slider.value())
        dst = make_output(self.video, f"{Path(self.video).stem}_compressed", "mp4")
        self.out_path = dst
        args = ["-y", "-i", self.video, "-c:v", codec, "-rc", "constqp",
                "-qp", str(qp), "-preset", "p5", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-progress", "pipe:1", "-nostats", dst]
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self.on_compress_progress)
        self.proc.finished.connect(self.on_compress_finished)
        self.go.setEnabled(False); self.slider.setEnabled(False)
        self.bar.setRange(0, 100) if self.dur > 0 else self.bar.setRange(0, 0)
        self.bar.setValue(0)
        self.status.setText(f"Compression → {Path(dst).name}")
        self.proc.start("ffmpeg", args)

    def on_compress_progress(self):
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "ignore")
        for line in data.splitlines():
            if line.startswith("out_time=") and self.dur > 0:
                secs = hms_to_secs(line.split("=", 1)[1].strip())
                self.bar.setValue(min(100, int(secs / self.dur * 100)))

    def on_compress_finished(self, code, _s):
        ok = code == 0 and Path(self.out_path).exists()
        if ok:
            mb = Path(self.out_path).stat().st_size / 1e6
            self.status.setText(f"✅ {Path(self.out_path).name}  ({mb:.1f} Mo)")
            self.bar.setRange(0, 100); self.bar.setValue(100)
        else:
            self.status.setText("❌ échec de la compression")
        self.proc.deleteLater(); self.proc = None
        self.go.setEnabled(True); self.slider.setEnabled(True)


# ===========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎬 Resolve Converter")
        self.resize(580, 760)
        self.tmpdir = tempfile.mkdtemp(prefix="resolveconv_")

        tabs = QTabWidget()
        tabs.addTab(ImportTab(), "Import → Resolve")
        self.compress_tab = CompressTab(self.tmpdir)
        tabs.addTab(self.compress_tab, "Export → Compression")
        self.setCentralWidget(tabs)
        self.setStyleSheet(STYLE)

    def closeEvent(self, e):
        try:
            rmtree(self.tmpdir, ignore_errors=True)
        except Exception:
            pass
        super().closeEvent(e)


STYLE = """
QMainWindow, QWidget { background:#1B1B1B; color:#C0C0C0; }
QLabel { color:#C0C0C0; font-size:13px; }
QTabWidget::pane { border:1px solid #3D3D3D; border-radius:4px; top:-1px; background:#232323; }
QTabBar::tab { background:#262626; color:#9A9A9A; padding:8px 20px; margin-right:2px;
    border:1px solid #3D3D3D; border-bottom:none;
    border-top-left-radius:4px; border-top-right-radius:4px; font-weight:600; }
QTabBar::tab:selected { background:#2E2E2E; color:#E6E6E6; border-bottom:2px solid #4D8FCC; }
QTabBar::tab:hover:!selected { background:#2A2A2A; }
QComboBox { background:#2A2A2A; border:1px solid #444444; border-radius:4px;
    padding:6px 10px; color:#D0D0D0; }
QComboBox:hover { border-color:#4D8FCC; }
QComboBox QAbstractItemView { background:#2A2A2A; color:#D0D0D0;
    selection-background-color:#4D8FCC; selection-color:#FFFFFF;
    border:1px solid #444444; outline:none; }
QListWidget { background:#202020; border:1px solid #3D3D3D; border-radius:4px;
    padding:4px; font-size:13px; color:#D0D0D0; }
QListWidget::item { padding:5px 4px; color:#D0D0D0; }
QListWidget::item:selected { background:#34465A; color:#FFFFFF; }
QProgressBar { background:#2A2A2A; border:1px solid #3D3D3D; border-radius:4px; height:16px; }
QProgressBar::chunk { background:#4D8FCC; border-radius:3px; }
QSlider::groove:horizontal { height:6px; background:#2A2A2A; border:1px solid #3D3D3D; border-radius:3px; }
QSlider::sub-page:horizontal { background:#4D8FCC; border-radius:3px; }
QSlider::handle:horizontal { width:16px; margin:-6px 0; background:#D0D0D0;
    border:1px solid #5A5A5A; border-radius:8px; }
QSlider::handle:horizontal:hover { background:#FFFFFF; }
QPushButton { background:#333333; color:#E0E0E0; border:1px solid #4A4A4A; border-radius:4px;
    padding:9px; font-size:13px; font-weight:600; }
QPushButton:hover { background:#3C3C3C; border-color:#5A5A5A; }
QPushButton:pressed { background:#2A2A2A; }
QPushButton#primary { background:#3D7AB8; border-color:#4D8FCC; color:#FFFFFF; }
QPushButton#primary:hover { background:#4D8FCC; }
QPushButton#primary:pressed { background:#356596; }
QPushButton:disabled { background:#2A2A2A; color:#666666; border-color:#383838; }
"""


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Nunito", 10))
    win = MainWindow(); win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
