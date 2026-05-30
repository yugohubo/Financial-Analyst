"""
splash_screen.py — Premium Animated Splash / Tanıtım Ekranı
═══════════════════════════════════════════════════════════════
Cyberpunk-themed splash screen with:
  • Constellation particle field (floating neon dots + connection lines)
  • Glowing title with scale-in animation
  • Typewriter subtitle with blinking cursor
  • 4 staggered feature cards with slide-up + fade-in
  • Neon green glow progress bar with module loading steps
  • Subtle scanline CRT overlay
  • "Bir daha gösterme" checkbox (preference saved to SQLite)
  • Click / ESC to skip

All rendering via QPainter — zero external dependencies beyond PyQt6.
"""

import sys
import math
import random
from typing import List, Optional

from PyQt6.QtWidgets import QWidget, QCheckBox, QApplication
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QFont, QLinearGradient,
    QRadialGradient, QPainterPath, QBrush, QFontMetrics
)

import database


# ════════════════════════════════════════════════════════════
#  Particle System
# ════════════════════════════════════════════════════════════

class Particle:
    """A single floating dot in the constellation background effect."""

    # Shared color palette (neon cyberpunk tones)
    PALETTE = [
        QColor(0, 230, 118),     # neon green
        QColor(41, 121, 255),    # electric blue
        QColor(0, 229, 255),     # cyan
        QColor(200, 200, 220),   # soft white
        QColor(255, 209, 0),     # gold
        QColor(255, 23, 68),     # hot pink (rare accent)
    ]

    def __init__(self, bounds_w: int, bounds_h: int) -> None:
        self.bounds_w = bounds_w
        self.bounds_h = bounds_h

        # Random spawn position
        self.x = random.uniform(0, bounds_w)
        self.y = random.uniform(0, bounds_h)

        # Slow drift velocity
        self.vx = random.uniform(-0.35, 0.35)
        self.vy = random.uniform(-0.25, 0.25)

        # Visual properties
        self.size = random.uniform(1.5, 4.0)
        self.base_opacity = random.uniform(0.25, 0.75)
        self.current_opacity = self.base_opacity
        self.color = random.choice(self.PALETTE)

        # Pulsing parameters (each particle breathes at its own rate)
        self.pulse_speed = random.uniform(0.015, 0.04)
        self.pulse_phase = random.uniform(0, math.tau)

    def update(self) -> None:
        """Advance position and pulse opacity by one tick."""
        self.x += self.vx
        self.y += self.vy

        # Wrap around edges seamlessly
        if self.x < -10:
            self.x = self.bounds_w + 10
        elif self.x > self.bounds_w + 10:
            self.x = -10
        if self.y < -10:
            self.y = self.bounds_h + 10
        elif self.y > self.bounds_h + 10:
            self.y = -10

        # Smooth breathing pulse
        self.pulse_phase += self.pulse_speed
        pulse = 0.5 + 0.5 * math.sin(self.pulse_phase)
        self.current_opacity = self.base_opacity * (0.5 + 0.5 * pulse)


# ════════════════════════════════════════════════════════════
#  Splash Screen Widget
# ════════════════════════════════════════════════════════════

class SplashScreen(QWidget):
    """
    Full-screen frameless splash with layered QPainter animations.
    Emits `finished` signal when the intro sequence completes or
    the user skips it (click / ESC).
    """

    finished = pyqtSignal()

    # Window dimensions
    W = 960
    H = 640

    # ── Timeline constants (milliseconds) ──
    FADE_IN_END      = 500
    TITLE_START      = 400
    TITLE_END        = 1400
    LINE_START       = 900
    LINE_END         = 1400
    SUBTITLE_START   = 1400
    SUBTITLE_END     = 3200
    CARDS_START      = 1900
    CARD_STAGGER     = 280
    CARD_DURATION    = 450
    PROGRESS_START   = 2400
    PROGRESS_END     = 5400
    FADEOUT_START     = 5700
    FADEOUT_DURATION  = 850

    def __init__(self) -> None:
        super().__init__()

        # ── Window flags ──
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        # ── Animation state ──
        self.elapsed = 0          # total elapsed ms
        self.overall_alpha = 0.0  # 0→1 fade-in, 1→0 fade-out
        self.fading_out = False
        self._done = False

        # Title
        self.title_alpha = 0.0
        self.title_scale = 0.55
        self.glow_phase = 0.0

        # Decorative line
        self.line_progress = 0.0

        # Typewriter subtitle
        self.sub_full = "AI-Powered Autonomous Market Intelligence"
        self.sub_chars = 0
        self.cursor_visible = True
        self._cursor_timer = QTimer(self)
        self._cursor_timer.timeout.connect(self._blink_cursor)
        self._cursor_timer.start(530)

        # Feature cards
        self.features = [
            {"icon": "📊", "title": "Teknik Analiz",
             "desc": "RSI, MACD, ARIMA ile\nderin piyasa taraması"},
            {"icon": "🌐", "title": "Otonom Haber Kazıma",
             "desc": "DuckDuckGo + FastAPI ile\nanlık haber keşfi"},
            {"icon": "🤖", "title": "AI Makroekonomist",
             "desc": "Gemini / Ollama ile\nuzman seviye tahminler"},
            {"icon": "📡", "title": "Canlı İzleme",
             "desc": "10+ varlık, gerçek\nzamanlı sinyal akışı"},
        ]
        self.card_alpha = [0.0] * 4
        self.card_y_off = [22.0] * 4

        # Progress bar
        self.prog = 0.0
        self.prog_steps = [
            (0.00, "Veritabanı bağlantısı kuruluyor..."),
            (0.20, "Vektör deposu hazırlanıyor..."),
            (0.45, "Analiz motoru başlatılıyor..."),
            (0.70, "Piyasa verisi kaynakları kontrol ediliyor..."),
            (0.95, "✅ Sistem hazır!"),
        ]
        self.prog_label = "Başlatılıyor..."

        # Scanline offset
        self.scan_off = 0

        # Particles
        self.particles: List[Particle] = [
            Particle(self.W, self.H) for _ in range(48)
        ]

        # ── "Don't show again" checkbox ──
        self.skip_cb = QCheckBox("  Bir daha gösterme", self)
        self.skip_cb.setStyleSheet("""
            QCheckBox {
                color: #50516a;
                font-size: 11px;
                font-family: 'Segoe UI';
                background: transparent;
            }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border: 1px solid #3e3f4b;
                border-radius: 3px;
                background-color: #15151a;
            }
            QCheckBox::indicator:hover {
                border-color: #2979ff;
            }
            QCheckBox::indicator:checked {
                background-color: #2979ff;
                border-color: #2979ff;
            }
        """)
        self.skip_cb.move(self.W - 185, self.H - 38)
        self.skip_cb.resize(175, 22)

        # ── Cached fonts (avoid per-frame allocation) ──
        self._font_title      = QFont("Segoe UI", 28, QFont.Weight.Bold)
        self._font_sub_brand  = QFont("Segoe UI", 11)
        self._font_typewriter = QFont("Consolas", 12)
        self._font_card_icon  = QFont("Segoe UI Emoji", 26)
        self._font_card_title = QFont("Segoe UI", 10, QFont.Weight.Bold)
        self._font_card_desc  = QFont("Segoe UI", 8)
        self._font_prog_label = QFont("Consolas", 9)
        self._font_prog_pct   = QFont("Segoe UI", 9, QFont.Weight.Bold)
        self._font_version    = QFont("Segoe UI", 8)

        # ── Main render timer (~30 fps) ──
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    # ────────────────────────────────────────────
    #  Animation Loop
    # ────────────────────────────────────────────

    def _blink_cursor(self) -> None:
        self.cursor_visible = not self.cursor_visible

    @staticmethod
    def _ease_out(t: float) -> float:
        """Cubic ease-out for smooth deceleration."""
        return 1.0 - (1.0 - min(1.0, max(0.0, t))) ** 3

    def _tick(self) -> None:
        if self._done:
            return

        dt = 33
        self.elapsed += dt
        t = self.elapsed

        # Update particles
        for p in self.particles:
            p.update()

        self.scan_off = (self.scan_off + 1) % 4

        # ── Phase 0 — overall fade-in ──
        if t <= self.FADE_IN_END:
            self.overall_alpha = t / self.FADE_IN_END
        elif not self.fading_out:
            self.overall_alpha = 1.0

        # ── Phase 1 — title scale + fade ──
        if self.TITLE_START <= t <= self.TITLE_END:
            p = (t - self.TITLE_START) / (self.TITLE_END - self.TITLE_START)
            e = self._ease_out(p)
            self.title_alpha = e
            self.title_scale = 0.55 + 0.45 * e
        elif t > self.TITLE_END:
            self.title_alpha = 1.0
            self.title_scale = 1.0

        # glow pulse (continuous)
        if t > 700:
            self.glow_phase += 0.055

        # ── Phase 1.5 — decorative line ──
        if self.LINE_START <= t <= self.LINE_END:
            self.line_progress = (t - self.LINE_START) / (self.LINE_END - self.LINE_START)
        elif t > self.LINE_END:
            self.line_progress = 1.0

        # ── Phase 2 — typewriter ──
        if self.SUBTITLE_START <= t <= self.SUBTITLE_END:
            p = (t - self.SUBTITLE_START) / (self.SUBTITLE_END - self.SUBTITLE_START)
            self.sub_chars = int(len(self.sub_full) * min(1.0, p))
        elif t > self.SUBTITLE_END:
            self.sub_chars = len(self.sub_full)

        # ── Phase 3 — feature cards ──
        for i in range(4):
            cs = self.CARDS_START + i * self.CARD_STAGGER
            if t >= cs:
                p = min(1.0, (t - cs) / self.CARD_DURATION)
                e = self._ease_out(p)
                self.card_alpha[i] = e
                self.card_y_off[i] = 22.0 * (1.0 - e)

        # ── Phase 4 — progress bar ──
        if self.PROGRESS_START <= t <= self.PROGRESS_END:
            self.prog = min(1.0, (t - self.PROGRESS_START) / (self.PROGRESS_END - self.PROGRESS_START))
            for threshold, label in self.prog_steps:
                if self.prog >= threshold:
                    self.prog_label = label
        elif t > self.PROGRESS_END:
            self.prog = 1.0
            self.prog_label = "✅ Sistem hazır!"

        # ── Phase 5 — fade-out ──
        if t >= self.FADEOUT_START and not self.fading_out:
            self.fading_out = True
            self._save_pref()

        if self.fading_out:
            fo_elapsed = t - self.FADEOUT_START
            if fo_elapsed < 0:
                fo_elapsed = 0
            self.overall_alpha = max(0.0, 1.0 - fo_elapsed / self.FADEOUT_DURATION)
            if self.overall_alpha <= 0:
                self._finish()
                return

        self.update()   # trigger repaint

    def _finish(self) -> None:
        self._done = True
        self._timer.stop()
        self._cursor_timer.stop()
        self.close()
        self.finished.emit()

    def _save_pref(self) -> None:
        if self.skip_cb.isChecked():
            try:
                database.save_setting("skip_splash", "true")
            except Exception:
                pass

    # ────────────────────────────────────────────
    #  Input Handling (skip on click / ESC)
    # ────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Space):
            self._skip()

    def mousePressEvent(self, event) -> None:
        # Don't skip when clicking the checkbox
        cb = self.skip_cb.geometry()
        if not cb.contains(event.pos()):
            self._skip()

    def _skip(self) -> None:
        if not self.fading_out:
            self._save_pref()
            self.fading_out = True
            # Jump the timeline so fade-out starts immediately
            self.elapsed = self.FADEOUT_START

    # ────────────────────────────────────────────
    #  Rendering
    # ────────────────────────────────────────────

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        w, h = self.W, self.H

        # Clip to rounded rect for clean edges
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 18, 18)
        painter.setClipPath(clip)

        painter.setOpacity(self.overall_alpha)

        # ── Background ──
        bg = QLinearGradient(0, 0, w, h)
        bg.setColorAt(0.0, QColor(6, 6, 12))
        bg.setColorAt(0.4, QColor(12, 12, 18))
        bg.setColorAt(1.0, QColor(16, 16, 22))
        painter.fillRect(0, 0, w, h, bg)

        # Radial center glow (pulses subtly)
        g_alpha = int(18 + 12 * math.sin(self.glow_phase * 0.7))
        cg = QRadialGradient(w / 2, h * 0.28, 340)
        cg.setColorAt(0, QColor(41, 121, 255, g_alpha))
        cg.setColorAt(1, QColor(41, 121, 255, 0))
        painter.fillRect(0, 0, w, h, cg)

        # Secondary warm glow bottom
        g2_alpha = int(10 + 6 * math.sin(self.glow_phase * 0.5 + 1.5))
        cg2 = QRadialGradient(w / 2, h * 0.85, 280)
        cg2.setColorAt(0, QColor(0, 230, 118, g2_alpha))
        cg2.setColorAt(1, QColor(0, 230, 118, 0))
        painter.fillRect(0, 0, w, h, cg2)

        # ── Scanlines (subtle CRT overlay) ──
        painter.setOpacity(self.overall_alpha * 0.025)
        for y in range(self.scan_off, h, 4):
            painter.fillRect(0, y, w, 1, QColor(255, 255, 255))
        painter.setOpacity(self.overall_alpha)

        # ── Layers ──
        self._paint_particles(painter, w, h)
        self._paint_title(painter, w, h)
        self._paint_line(painter, w, h)
        self._paint_subtitle(painter, w, h)
        self._paint_features(painter, w, h)
        self._paint_progress(painter, w, h)

        # ── Outer border (breathing color) ──
        painter.setOpacity(self.overall_alpha)
        border_hue = int(140 + 30 * math.sin(self.glow_phase * 0.4))
        border_color = QColor.fromHsl(border_hue, 80, 45, 100)
        painter.setPen(QPen(border_color, 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 18, 18)

        # ── Version tag ──
        painter.setOpacity(self.overall_alpha * 0.35)
        painter.setFont(self._font_version)
        painter.setPen(QColor(143, 144, 166))
        painter.drawText(
            QRectF(20, h - 38, 200, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "v1.0 • PyQt6 Native Engine"
        )

        painter.end()

    # ── Particles & Constellation ──

    def _paint_particles(self, p: QPainter, w: int, h: int) -> None:
        oa = self.overall_alpha
        particles = self.particles

        # Draw connection lines between nearby particles
        for i in range(len(particles)):
            p1 = particles[i]
            for j in range(i + 1, len(particles)):
                p2 = particles[j]
                dx = p1.x - p2.x
                dy = p1.y - p2.y
                dist_sq = dx * dx + dy * dy
                if dist_sq < 12100:  # 110²
                    dist = math.sqrt(dist_sq)
                    alpha = int(30 * (1.0 - dist / 110.0) * oa)
                    if alpha > 0:
                        p.setPen(QPen(QColor(0, 230, 118, alpha), 0.6))
                        p.drawLine(QPointF(p1.x, p1.y), QPointF(p2.x, p2.y))

        # Draw particle dots with soft glow halos
        for pt in particles:
            a = int(pt.current_opacity * 255 * oa)
            if a < 1:
                continue

            # Glow halo (simple translucent larger circle — fast)
            halo_a = max(0, min(255, int(a * 0.18)))
            halo_color = QColor(pt.color.red(), pt.color.green(), pt.color.blue(), halo_a)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(halo_color))
            p.drawEllipse(QPointF(pt.x, pt.y), pt.size * 3.5, pt.size * 3.5)

            # Core dot
            core = QColor(pt.color.red(), pt.color.green(), pt.color.blue(), a)
            p.setBrush(QBrush(core))
            p.drawEllipse(QPointF(pt.x, pt.y), pt.size, pt.size)

    # ── Title ──

    def _paint_title(self, p: QPainter, w: int, h: int) -> None:
        if self.title_alpha <= 0.01:
            return

        p.setOpacity(self.overall_alpha * self.title_alpha)

        text = "OTONOM FİNANSAL ANALİST"
        scaled_size = int(28 * self.title_scale)
        font = QFont("Segoe UI", max(10, scaled_size), QFont.Weight.Bold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.0 * self.title_scale)
        p.setFont(font)

        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        tx = (w - tw) / 2
        ty = h * 0.20

        # Multi-layer glow effect (neon green corona)
        glow_str = 0.25 + 0.12 * math.sin(self.glow_phase)
        glow_layers = [(4, 0.06), (3, 0.09), (2, 0.14), (1, 0.18)]
        for offset, alpha_mult in glow_layers:
            ga = int(255 * glow_str * alpha_mult * self.title_alpha)
            gc = QColor(0, 230, 118, max(0, min(255, ga)))
            p.setPen(gc)
            for dx in (-offset, 0, offset):
                for dy in (-offset, 0, offset):
                    if dx == 0 and dy == 0:
                        continue
                    p.drawText(int(tx + dx), int(ty + dy), text)

        # Main white text
        p.setPen(QColor(255, 255, 255))
        p.drawText(int(tx), int(ty), text)

        # Sub-brand text
        sub = "▸ VE TAHMİN SİSTEMİ"
        p.setFont(self._font_sub_brand)
        sfm = QFontMetrics(self._font_sub_brand)
        sw = sfm.horizontalAdvance(sub)
        p.setPen(QColor(143, 144, 166, int(220 * self.title_alpha)))
        p.drawText(int((w - sw) / 2), int(ty + 35), sub)

    # ── Decorative Gradient Line ──

    def _paint_line(self, p: QPainter, w: int, h: int) -> None:
        if self.line_progress <= 0.01:
            return

        p.setOpacity(self.overall_alpha * self.line_progress)

        line_w = 420 * self.line_progress
        lx = (w - line_w) / 2
        ly = h * 0.20 + 52

        grad = QLinearGradient(lx, ly, lx + line_w, ly)
        grad.setColorAt(0.0, QColor(0, 230, 118, 0))
        grad.setColorAt(0.3, QColor(0, 230, 118, 180))
        grad.setColorAt(0.5, QColor(41, 121, 255, 220))
        grad.setColorAt(0.7, QColor(0, 230, 118, 180))
        grad.setColorAt(1.0, QColor(0, 230, 118, 0))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(QRectF(lx, ly, line_w, 2.5), 1, 1)

        # Soft glow band under the line
        glow = QLinearGradient(lx, ly - 4, lx + line_w, ly - 4)
        glow.setColorAt(0.0, QColor(41, 121, 255, 0))
        glow.setColorAt(0.5, QColor(41, 121, 255, 25))
        glow.setColorAt(1.0, QColor(41, 121, 255, 0))
        p.setBrush(QBrush(glow))
        p.drawRect(QRectF(lx, ly - 4, line_w, 10))

    # ── Typewriter Subtitle ──

    def _paint_subtitle(self, p: QPainter, w: int, h: int) -> None:
        if self.sub_chars <= 0:
            return

        p.setOpacity(self.overall_alpha)
        p.setFont(self._font_typewriter)

        visible = self.sub_full[:self.sub_chars]
        cursor = "█" if self.cursor_visible and self.sub_chars < len(self.sub_full) else ""
        display = visible + cursor

        fm = QFontMetrics(self._font_typewriter)
        tw = fm.horizontalAdvance(display)
        tx = (w - tw) / 2
        ty = h * 0.20 + 82

        # Faint green glow behind text
        p.setPen(QColor(0, 230, 118, 35))
        p.drawText(int(tx + 1), int(ty + 1), display)

        # Main text
        p.setPen(QColor(0, 230, 118, 200))
        p.drawText(int(tx), int(ty), display)

    # ── Feature Cards ──

    def _paint_features(self, p: QPainter, w: int, h: int) -> None:
        card_w = 200
        card_h = 125
        gap = 16
        total = 4 * card_w + 3 * gap   # 860
        sx = (w - total) / 2
        cy = h * 0.46

        for i, feat in enumerate(self.features):
            alpha = self.card_alpha[i]
            if alpha < 0.01:
                continue

            y_off = self.card_y_off[i]
            p.setOpacity(self.overall_alpha * alpha)

            x = sx + i * (card_w + gap)
            y = cy + y_off

            cr = QRectF(x, y, card_w, card_h)

            # Card background
            card_bg = QLinearGradient(x, y, x, y + card_h)
            card_bg.setColorAt(0.0, QColor(24, 24, 32, 230))
            card_bg.setColorAt(1.0, QColor(18, 18, 26, 230))

            card_path = QPainterPath()
            card_path.addRoundedRect(cr, 12, 12)
            p.fillPath(card_path, card_bg)

            # Border with subtle gradient
            border_grad = QLinearGradient(x, y, x + card_w, y + card_h)
            accent_colors = [
                (QColor(0, 230, 118, 90), QColor(0, 230, 118, 30)),   # green
                (QColor(41, 121, 255, 90), QColor(41, 121, 255, 30)),  # blue
                (QColor(255, 209, 0, 90), QColor(255, 209, 0, 30)),    # gold
                (QColor(0, 229, 255, 90), QColor(0, 229, 255, 30)),    # cyan
            ]
            c1, c2 = accent_colors[i]
            border_grad.setColorAt(0.0, c1)
            border_grad.setColorAt(1.0, c2)
            p.setPen(QPen(QBrush(border_grad), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(cr, 12, 12)

            # Icon
            p.setFont(self._font_card_icon)
            p.setPen(QColor(255, 255, 255))
            p.drawText(
                QRectF(x, y + 12, card_w, 38),
                Qt.AlignmentFlag.AlignCenter, feat["icon"]
            )

            # Title
            title_colors = [
                QColor(0, 230, 118),
                QColor(41, 121, 255),
                QColor(255, 209, 0),
                QColor(0, 229, 255),
            ]
            p.setFont(self._font_card_title)
            p.setPen(title_colors[i])
            p.drawText(
                QRectF(x + 8, y + 55, card_w - 16, 20),
                Qt.AlignmentFlag.AlignCenter, feat["title"]
            )

            # Description
            p.setFont(self._font_card_desc)
            p.setPen(QColor(143, 144, 166))
            p.drawText(
                QRectF(x + 10, y + 78, card_w - 20, 42),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                | Qt.TextFlag.TextWordWrap,
                feat["desc"]
            )

    # ── Progress Bar ──

    def _paint_progress(self, p: QPainter, w: int, h: int) -> None:
        if self.prog <= 0.005:
            return

        bar_w = 560
        bar_h = 7
        bx = (w - bar_w) / 2
        by = h - 105

        bar_alpha = min(1.0, self.prog * 6)   # quick fade-in
        p.setOpacity(self.overall_alpha * bar_alpha)

        # Track background
        track = QPainterPath()
        track.addRoundedRect(QRectF(bx, by, bar_w, bar_h), 3.5, 3.5)
        p.fillPath(track, QColor(24, 24, 32))
        p.setPen(QPen(QColor(40, 40, 50), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(bx, by, bar_w, bar_h), 3.5, 3.5)

        # Filled portion
        fill_w = max(bar_h, bar_w * self.prog)  # min width = bar_h for round ends
        fill = QPainterPath()
        fill.addRoundedRect(QRectF(bx, by, fill_w, bar_h), 3.5, 3.5)

        fill_grad = QLinearGradient(bx, by, bx + fill_w, by)
        fill_grad.setColorAt(0.0, QColor(0, 180, 90))
        fill_grad.setColorAt(0.6, QColor(0, 230, 118))
        fill_grad.setColorAt(1.0, QColor(50, 255, 150))
        p.fillPath(fill, fill_grad)

        # Glow band around the filled bar
        glow_band = QLinearGradient(bx, by - 8, bx, by + bar_h + 8)
        glow_band.setColorAt(0.0, QColor(0, 230, 118, 0))
        glow_band.setColorAt(0.5, QColor(0, 230, 118, 30))
        glow_band.setColorAt(1.0, QColor(0, 230, 118, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(glow_band))
        p.drawRect(QRectF(bx, by - 8, fill_w, bar_h + 16))

        # Moving shine highlight on the bar
        if self.prog < 1.0:
            shine_x = bx + fill_w - 30
            shine = QRadialGradient(shine_x, by + bar_h / 2, 18)
            shine.setColorAt(0, QColor(255, 255, 255, 55))
            shine.setColorAt(1, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(shine))
            p.drawEllipse(QPointF(shine_x, by + bar_h / 2), 18, 8)

        # Label text
        p.setFont(self._font_prog_label)
        p.setPen(QColor(120, 121, 140))
        p.drawText(
            QRectF(bx, by + 14, bar_w, 22),
            Qt.AlignmentFlag.AlignCenter, self.prog_label
        )

        # Percentage text
        pct = f"{int(self.prog * 100)}%"
        p.setFont(self._font_prog_pct)
        p.setPen(QColor(0, 230, 118))
        p.drawText(
            QRectF(bx + bar_w + 12, by - 3, 50, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            pct
        )


# ════════════════════════════════════════════════════════════
#  Standalone Test (python splash_screen.py)
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

    database.init_db()

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))

    splash = SplashScreen()

    def on_done():
        print("Splash finished — would launch main window here.")
        app.quit()

    splash.finished.connect(on_done)
    splash.show()

    # Center on primary screen
    geo = app.primaryScreen().availableGeometry()
    splash.move(
        (geo.width() - splash.width()) // 2,
        (geo.height() - splash.height()) // 2,
    )

    sys.exit(app.exec())
