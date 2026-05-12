#!/usr/bin/env python3
"""
BIST DOM Replay — Professional 25-Level Depth of Market Ladder with Trades
"""

import sys, os, time
import pandas as pd
import numpy as np
from collections import deque
import pyarrow.parquet as pq

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QScrollArea, QLabel, QPushButton, QComboBox, QSlider,
    QStatusBar, QListWidget, QListWidgetItem, QMessageBox, QFrame,
    QSizePolicy, QSpacerItem
)
from PyQt5.QtCore import Qt, QTimer, QRect, QSize, pyqtSignal
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush, QPalette,
    QLinearGradient, QFontMetrics
)

# ── Constants ─────────────────────────────────────────────────────────────────
DATA_ROOT  = r"D:\BIST_ITCH_MTX_PIPELINE_DATA\output\enriched\market_depth_hive"
LEVELS     = 25
MAX_TAPE   = 1000
TIMER_MS   = 16      # ~60 fps playback tick
MAX_ROWS_PER_TICK = 500   # cap per tick to avoid UI freeze

# ── Color Palette ─────────────────────────────────────────────────────────────
BG           = "#0d1117"
PANEL        = "#161b22"
HEADER_BG    = "#1c2128"
BORDER       = "#30363d"
BID_TEXT     = "#3fb950"
BID_BAR_L1   = "#0d4429"   # bar fill (level 1 = best)
BID_BAR_FAR  = "#0a2e1c"   # bar fill (far levels)
BID_FLASH    = "#56d364"   # bright — used only for text on flash
BID_FLASH_BG = "#122b18"   # faded green row background
BID_FLASH_BAR= "#1c5c2a"   # medium green bar on flash
ASK_TEXT     = "#f85149"
ASK_BAR_L1   = "#3d0e0d"
ASK_BAR_FAR  = "#2a0a0a"
ASK_FLASH    = "#ff7b72"   # bright — used only for text on flash
ASK_FLASH_BG = "#2b1212"   # faded red row background
ASK_FLASH_BAR= "#5c1c1c"   # medium red bar on flash
TRADE_FLASH  = "#ffa500"   # bright — used only for text on flash
TRADE_FLASH_BG = "#2b1e00" # faded orange row background
TRADE_FLASH_BAR= "#5c3d00" # medium orange bar on flash
SPREAD_BG    = "#1a1f27"
TEXT_PRI     = "#e6edf3"
TEXT_SEC     = "#8b949e"
TEXT_DIM     = "#484f58"
TRADE_BUY    = "#58a6ff"
TRADE_SELL   = "#e3b341"
ACCENT       = "#388bfd"
ACCENT_DIM   = "#1f3a6e"


def C(s):
    return QColor(s)


# ── Stylesheet ────────────────────────────────────────────────────────────────
APP_CSS = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT_PRI};
    font-family: "Consolas", "Courier New", monospace;
    font-size: 11px;
}}
QComboBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 2px 6px;
    color: {TEXT_PRI};
}}
QComboBox QAbstractItemView {{
    background: {PANEL};
    border: 1px solid {BORDER};
    color: {TEXT_PRI};
    selection-background-color: {ACCENT};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 3px 8px;
    color: {TEXT_PRI};
}}
QPushButton:hover  {{ background: #1c2128; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; }}
QSlider::groove:horizontal {{
    height: 4px; background: {PANEL};
    border: 1px solid {BORDER}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 12px; height: 12px;
    margin: -4px 0; border-radius: 6px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QScrollBar:vertical {{
    background: {PANEL}; width: 7px; border-radius: 3px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER}; border-radius: 3px; min-height: 20px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar:horizontal {{
    background: {PANEL}; height: 7px; border-radius: 3px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER}; border-radius: 3px; min-width: 20px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
QStatusBar {{ background:{PANEL}; color:{TEXT_SEC}; font-size:9px; border-top:1px solid {BORDER}; }}
QListWidget {{ background:{BG}; border:none; outline:none; }}
QListWidget::item {{ border-bottom: 1px solid #1c2128; }}
QListWidget::item:selected {{ background: #1c2128; }}
QLabel {{ color: {TEXT_PRI}; }}
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  DOM Widget  (custom-painted)
# ═══════════════════════════════════════════════════════════════════════════════

class DOMWidget(QWidget):
    ROW_H = 17

    # Column widths
    CNT_W   = 48    # order count
    QTY_W   = 150   # qty bar + number
    PRC_W   = 96    # price
    # total = 48+150+96+150+48 = 492  (fits in 530 min-width)

    FLASH_TTL = 8   # ticks at 50ms → ~400ms flash

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snap      = None
        self._prev      = None
        self._flash     = {}     # row_idx -> (bg_color, bar_color, text_color, ttl)
        self._max_qty   = 1
        self._dec       = 2
        self._last_tp   = 0.0    # last trade price for highlight

        self._ftimer = QTimer(self)
        self._ftimer.timeout.connect(self._tick_flash)
        self._ftimer.start(50)

        self.setMinimumWidth(492 + 20)
        self.setAttribute(Qt.WA_OpaquePaintEvent)

    # ── Public API ──────────────────────────────────────────────────────────
    def set_snapshot(self, snap, prev=None, trade_price=None):
        self._snap = snap
        self._prev = prev
        self._dec  = int(snap.get("decimals_price", 2) or 2) if snap else 2

        if trade_price and trade_price > 0:
            self._last_tp = trade_price

        # Detect changed qty levels → flash
        if prev and snap:
            for lvl in range(1, LEVELS + 1):
                bq_new = snap.get(f"bid_q{lvl}") or 0
                bq_old = prev.get(f"bid_q{lvl}") or 0
                if bq_new != bq_old:
                    self._flash[LEVELS + 1 + (lvl - 1)] = (
                        C(BID_FLASH_BG), C(BID_FLASH_BAR), C(BID_FLASH), self.FLASH_TTL)

                aq_new = snap.get(f"ask_q{lvl}") or 0
                aq_old = prev.get(f"ask_q{lvl}") or 0
                if aq_new != aq_old:
                    self._flash[LEVELS - lvl] = (
                        C(ASK_FLASH_BG), C(ASK_FLASH_BAR), C(ASK_FLASH), self.FLASH_TTL)

        # Trade flash: find price levels matching trade_price
        if trade_price and trade_price > 0 and snap:
            for lvl in range(1, LEVELS + 1):
                bp = snap.get(f"bid_p{lvl}") or 0
                ap = snap.get(f"ask_p{lvl}") or 0
                if bp and abs(bp - trade_price) < 1e-7:
                    self._flash[LEVELS + 1 + (lvl - 1)] = (
                        C(TRADE_FLASH_BG), C(TRADE_FLASH_BAR), C(TRADE_FLASH), self.FLASH_TTL + 4)
                if ap and abs(ap - trade_price) < 1e-7:
                    self._flash[LEVELS - lvl] = (
                        C(TRADE_FLASH_BG), C(TRADE_FLASH_BAR), C(TRADE_FLASH), self.FLASH_TTL + 4)

        # Recalculate max qty for bar scaling
        if snap:
            vals = [snap.get(f"bid_q{i}") or 0 for i in range(1, LEVELS + 1)]
            vals += [snap.get(f"ask_q{i}") or 0 for i in range(1, LEVELS + 1)]
            self._max_qty = max(max(vals), 1)

        self.update()

    # ── Flash animation ──────────────────────────────────────────────────────
    def _tick_flash(self):
        expired = [k for k, v in self._flash.items() if v[3] <= 1]
        for k in expired:
            del self._flash[k]
        for k in list(self._flash.keys()):
            bg, bar, txt, ttl = self._flash[k]
            self._flash[k] = (bg, bar, txt, ttl - 1)
        if self._flash:
            self.update()

    # ── Size hint ────────────────────────────────────────────────────────────
    def sizeHint(self):
        total_rows = LEVELS * 2 + 1   # asks + spread + bids
        h = self.ROW_H * (total_rows + 1)  # +1 for header
        return QSize(self.CNT_W*2 + self.QTY_W*2 + self.PRC_W + 20, h)

    # ── Paint ────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)

        W   = self.width()
        rh  = self.ROW_H
        snap = self._snap

        # Column x positions (centered on widget)
        total_w = self.CNT_W*2 + self.QTY_W*2 + self.PRC_W
        ox = max(0, (W - total_w) // 2)   # left offset

        bid_cnt_x  = ox
        bid_qty_x  = ox + self.CNT_W
        price_x    = ox + self.CNT_W + self.QTY_W
        ask_qty_x  = ox + self.CNT_W + self.QTY_W + self.PRC_W
        ask_cnt_x  = ox + self.CNT_W + self.QTY_W + self.PRC_W + self.QTY_W

        # Header row
        self._paint_header(p, W, rh, bid_cnt_x, bid_qty_x, price_x,
                           ask_qty_x, ask_cnt_x)

        y0 = rh  # content starts here

        # Ask rows: row0=ask_p25 (farthest), row24=ask_p1 (best)
        for i in range(LEVELS):
            lvl = LEVELS - i   # 25..1
            row_idx = i
            y = y0 + i * rh
            flash = self._flash.get(row_idx)

            ap  = (snap.get(f"ask_p{lvl}") or 0) if snap else 0
            aq  = (snap.get(f"ask_q{lvl}") or 0) if snap else 0
            ac  = (snap.get(f"ask_c{lvl}") or 0) if snap else 0
            near = (lvl <= 3)   # top-3 best levels → brighter bar

            self._paint_ask_row(p, y, W, rh, lvl, ap, aq, ac, near,
                                bid_cnt_x, bid_qty_x, price_x,
                                ask_qty_x, ask_cnt_x, flash)

        # Spread row
        spread_y = y0 + LEVELS * rh
        self._paint_spread(p, spread_y, W, rh, snap, price_x)

        # Bid rows: row0=bid_p1 (best), row24=bid_p25 (farthest)
        for i in range(LEVELS):
            lvl = i + 1   # 1..25
            row_idx = LEVELS + 1 + i
            y = y0 + (LEVELS + 1 + i) * rh
            flash = self._flash.get(row_idx)

            bp  = (snap.get(f"bid_p{lvl}") or 0) if snap else 0
            bq  = (snap.get(f"bid_q{lvl}") or 0) if snap else 0
            bc  = (snap.get(f"bid_c{lvl}") or 0) if snap else 0
            near = (lvl <= 3)

            self._paint_bid_row(p, y, W, rh, lvl, bp, bq, bc, near,
                                bid_cnt_x, bid_qty_x, price_x,
                                ask_qty_x, ask_cnt_x, flash)

        p.end()

    # ─── Header ──────────────────────────────────────────────────────────────
    def _paint_header(self, p, W, rh, bci, bqi, pri, aqi, aci):
        p.fillRect(0, 0, W, rh, C(HEADER_BG))
        p.setPen(C(BORDER))
        p.drawLine(0, rh - 1, W, rh - 1)

        hf = QFont("Consolas", 8, QFont.Bold)
        p.setFont(hf)
        p.setPen(C(TEXT_SEC))

        cols = [
            (bci, self.CNT_W,  Qt.AlignRight,   "ORDERS"),
            (bqi, self.QTY_W,  Qt.AlignRight,   "BID SIZE"),
            (pri, self.PRC_W,  Qt.AlignHCenter,  "PRICE"),
            (aqi, self.QTY_W,  Qt.AlignLeft,    "ASK SIZE"),
            (aci, self.CNT_W,  Qt.AlignLeft,    "ORDERS"),
        ]
        for x, cw, align, lbl in cols:
            p.drawText(QRect(x, 0, cw, rh), align | Qt.AlignVCenter, lbl)

    # ─── Ask row ─────────────────────────────────────────────────────────────
    def _paint_ask_row(self, p, y, W, rh, lvl, price, qty, cnt, near,
                        bci, bqi, pri, aqi, aci, flash):
        # Background — faded red tint; flash uses its own faded bg color
        base_bg = "#110b0b" if lvl % 2 == 0 else "#0f0a0a"
        p.fillRect(0, y, W, rh, flash[0] if flash else C(base_bg))

        # Ask size bar
        if qty > 0 and self._max_qty > 0:
            ratio = min(qty / self._max_qty, 1.0)
            bar_w = int(self.QTY_W * 0.88 * ratio)
            bar_c = flash[1] if flash else (C(ASK_BAR_L1) if near else C(ASK_BAR_FAR))
            p.fillRect(aqi, y + 2, bar_w, rh - 4, bar_c)

        # Separator
        p.setPen(QPen(C(BORDER), 1))
        p.drawLine(0, y + rh - 1, W, y + rh - 1)

        f = QFont("Consolas", 10)
        p.setFont(f)

        # Price — always use normal ask color so text stays readable
        p.setPen(C(ASK_TEXT if price > 0 else TEXT_DIM))
        ps = f"{price:.{self._dec}f}" if price > 0 else "—"
        p.drawText(QRect(pri, y, self.PRC_W, rh), Qt.AlignHCenter | Qt.AlignVCenter, ps)

        # Ask qty
        if qty > 0:
            p.setPen(flash[2] if flash else C(ASK_TEXT))
            p.drawText(QRect(aqi + 2, y, self.QTY_W - 4, rh),
                       Qt.AlignLeft | Qt.AlignVCenter, f"{qty:,}")

        # Order count
        if cnt > 0:
            p.setPen(C(TEXT_SEC))
            p.drawText(QRect(aci + 2, y, self.CNT_W - 4, rh),
                       Qt.AlignLeft | Qt.AlignVCenter, str(cnt))

    # ─── Bid row ─────────────────────────────────────────────────────────────
    def _paint_bid_row(self, p, y, W, rh, lvl, price, qty, cnt, near,
                        bci, bqi, pri, aqi, aci, flash):
        base_bg = "#0a130b" if lvl % 2 == 0 else "#091109"
        p.fillRect(0, y, W, rh, flash[0] if flash else C(base_bg))

        # Bid size bar (grows left — right-aligned in bqi column)
        if qty > 0 and self._max_qty > 0:
            ratio = min(qty / self._max_qty, 1.0)
            bar_w = int(self.QTY_W * 0.88 * ratio)
            bar_c = flash[1] if flash else (C(BID_BAR_L1) if near else C(BID_BAR_FAR))
            p.fillRect(bqi + self.QTY_W - bar_w, y + 2, bar_w, rh - 4, bar_c)

        # Separator
        p.setPen(QPen(C(BORDER), 1))
        p.drawLine(0, y + rh - 1, W, y + rh - 1)

        f = QFont("Consolas", 10)
        p.setFont(f)

        # Price — always use normal bid color so text stays readable
        p.setPen(C(BID_TEXT if price > 0 else TEXT_DIM))
        ps = f"{price:.{self._dec}f}" if price > 0 else "—"
        p.drawText(QRect(pri, y, self.PRC_W, rh), Qt.AlignHCenter | Qt.AlignVCenter, ps)

        # Bid qty
        if qty > 0:
            p.setPen(flash[2] if flash else C(BID_TEXT))
            p.drawText(QRect(bqi, y, self.QTY_W - 4, rh),
                       Qt.AlignRight | Qt.AlignVCenter, f"{qty:,}")

        # Order count
        if cnt > 0:
            p.setPen(C(TEXT_SEC))
            p.drawText(QRect(bci, y, self.CNT_W - 4, rh),
                       Qt.AlignRight | Qt.AlignVCenter, str(cnt))

    # ─── Spread row ──────────────────────────────────────────────────────────
    def _paint_spread(self, p, y, W, rh, snap, pri):
        p.fillRect(0, y, W, rh, C(SPREAD_BG))

        # Accent lines
        pen = QPen(C(ACCENT), 1)
        p.setPen(pen)
        p.drawLine(0, y, W, y)
        p.drawLine(0, y + rh - 1, W, y + rh - 1)

        f = QFont("Consolas", 9, QFont.Bold)
        p.setFont(f)
        p.setPen(C(ACCENT))

        if snap:
            bp1 = snap.get("bid_p1") or 0
            ap1 = snap.get("ask_p1") or 0
            dec  = self._dec
            if bp1 > 0 and ap1 > 0:
                spread = ap1 - bp1
                mid    = (ap1 + bp1) / 2.0
                txt    = (f"BID {bp1:.{dec}f}   "
                          f"SPREAD {spread:.{dec}f}   "
                          f"MID {mid:.{dec}f}   "
                          f"ASK {ap1:.{dec}f}")
            else:
                txt = "— SPREAD —"
        else:
            txt = "— LOAD DATA —"

        p.drawText(QRect(0, y, W, rh), Qt.AlignHCenter | Qt.AlignVCenter, txt)


# ═══════════════════════════════════════════════════════════════════════════════
#  Trade Tape
# ═══════════════════════════════════════════════════════════════════════════════

class TradeTapeWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(520)
        self._dec = 2

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        hdr = QLabel("  TIME & SALES")
        hdr.setFixedHeight(22)
        hdr.setStyleSheet(
            f"background:{HEADER_BG}; color:{TEXT_SEC}; font-size:9px;"
            f" font-weight:bold; border-bottom:1px solid {BORDER};"
        )
        lay.addWidget(hdr)

        # Column sub-header
        col_hdr = QLabel("  TIME          PRICE       SIZE  AGG    BUYER           SELLER")
        col_hdr.setFixedHeight(16)
        col_hdr.setStyleSheet(
            f"background:{HEADER_BG}; color:{TEXT_DIM}; font-size:8px;"
            f" font-family:Consolas; border-bottom:1px solid {BORDER};"
        )
        lay.addWidget(col_hdr)

        self.lw = QListWidget()
        self.lw.setFont(QFont("Consolas", 10))
        self.lw.setUniformItemSizes(True)
        self.lw.setSpacing(0)
        lay.addWidget(self.lw)

    def add_trade(self, ts, price, qty, side, dec=2, buyer="", seller=""):
        self._dec = dec
        try:
            t_str = ts.strftime("%H:%M:%S.%f")[:12]
        except Exception:
            t_str = str(ts)[:12]

        # itch_side = S → passive seller at ask → aggressive BUYER lifted ask
        # itch_side = B → passive buyer at bid → aggressive SELLER hit bid
        s = str(side).upper()
        if s in ("S", "SELL"):
            agg_label = "↑ BUY "
            color     = TRADE_BUY
        else:
            agg_label = "↓ SELL"
            color     = TRADE_SELL

        buyer_s  = (str(buyer)  or "—")[:14]
        seller_s = (str(seller) or "—")[:14]

        text = (f"  {t_str}  {price:>{4+dec}.{dec}f}"
                f"  {qty:>7,}  {agg_label}"
                f"  {buyer_s:<14}  {seller_s:<14}")
        item = QListWidgetItem(text)
        item.setForeground(C(color))

        self.lw.insertItem(0, item)
        if self.lw.count() > MAX_TAPE:
            self.lw.takeItem(self.lw.count() - 1)

    def clear_tape(self):
        self.lw.clear()


# ═══════════════════════════════════════════════════════════════════════════════
#  Summary Bar
# ═══════════════════════════════════════════════════════════════════════════════

class SummaryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self.setStyleSheet(
            f"background:{PANEL}; border-bottom:1px solid {BORDER};"
        )
        self._last_tp = 0.0
        self._last_tp_dec = 2

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(0)

        self._stats = {}
        for key, label, color in [
            ("bid",       "BEST BID",     BID_TEXT),
            ("ask",       "BEST ASK",     ASK_TEXT),
            ("spread",    "SPREAD",       TEXT_SEC),
            ("mid",       "MID",          TEXT_PRI),
            ("last",      "LAST TRADE",   TRADE_BUY),
            ("state",     "STATE",        TEXT_SEC),
            ("vol",       "VOLUME",       TEXT_SEC),
            ("notional",  "NOTIONAL",     TEXT_SEC),
        ]:
            w = self._make_stat_widget(label, color)
            self._stats[key] = w
            lay.addWidget(w["frame"])
            if key != "notional":
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet(f"color:{BORDER};")
                sep.setFixedWidth(1)
                lay.addWidget(sep)
        lay.addStretch()

    def _make_stat_widget(self, label, color):
        f = QWidget()
        f.setFixedWidth(118)
        v = QVBoxLayout(f)
        v.setContentsMargins(8, 0, 8, 0)
        v.setSpacing(1)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{TEXT_DIM}; font-size:8px; font-weight:bold;")
        val = QLabel("—")
        val.setStyleSheet(
            f"color:{color}; font-size:13px; font-family:Consolas;"
            f" font-weight:bold;"
        )
        v.addWidget(lbl)
        v.addWidget(val)
        return {"frame": f, "val": val, "color": color}

    def update_stats(self, snap, dec=2, cum_vol=0, cum_notional=0, last_tp=None):
        if snap is None:
            return

        bp1 = snap.get("bid_p1") or 0
        ap1 = snap.get("ask_p1") or 0
        sp  = (ap1 - bp1) if (bp1 > 0 and ap1 > 0) else 0
        mid = (bp1 + ap1) / 2 if (bp1 > 0 and ap1 > 0) else 0
        state = str(snap.get("state_name") or "—").replace("_", " ")

        def fmt(v):
            return f"{v:.{dec}f}" if v > 0 else "—"

        self._stats["bid"]["val"].setText(fmt(bp1))
        self._stats["ask"]["val"].setText(fmt(ap1))
        self._stats["spread"]["val"].setText(fmt(sp))
        self._stats["mid"]["val"].setText(fmt(mid))
        self._stats["state"]["val"].setText(state[:14])

        if last_tp and last_tp > 0:
            self._stats["last"]["val"].setText(fmt(last_tp))

        if cum_vol > 0:
            self._stats["vol"]["val"].setText(f"{cum_vol:,}")
        if cum_notional > 0:
            if cum_notional >= 1e6:
                self._stats["notional"]["val"].setText(f"₺{cum_notional/1e6:.2f}M")
            else:
                self._stats["notional"]["val"].setText(f"₺{cum_notional:,.0f}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Selector Bar
# ═══════════════════════════════════════════════════════════════════════════════

class SelectorBar(QWidget):
    load_requested = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self.setStyleSheet(
            f"background:{PANEL}; border-bottom:1px solid {BORDER};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(8)

        def lbl(t):
            w = QLabel(t)
            w.setStyleSheet(f"color:{TEXT_SEC}; font-size:10px;")
            return w

        lay.addWidget(lbl("Symbol:"))
        self.sym_box = QComboBox()
        self.sym_box.setFixedWidth(100)
        self.sym_box.setEditable(True)
        self.sym_box.lineEdit().setPlaceholderText("e.g. AKBNK")
        lay.addWidget(self.sym_box)

        lay.addWidget(lbl("Date:"))
        self.date_box = QComboBox()
        self.date_box.setFixedWidth(110)
        lay.addWidget(self.date_box)

        self.btn_load = QPushButton("⏵  Load")
        self.btn_load.setFixedSize(78, 26)
        self.btn_load.setStyleSheet(
            f"background:{ACCENT}; color:white; border:none;"
            f" border-radius:3px; font-size:11px;"
        )
        lay.addWidget(self.btn_load)

        self.info_lbl = QLabel("")
        self.info_lbl.setStyleSheet(f"color:{TEXT_SEC}; font-size:9px;")
        lay.addWidget(self.info_lbl)
        lay.addStretch()

        self.sym_box.currentTextChanged.connect(self._sym_changed)
        self.btn_load.clicked.connect(self._emit_load)

    def populate(self, symbols):
        self.sym_box.blockSignals(True)
        self.sym_box.clear()
        self.sym_box.addItems(sorted(symbols))
        self.sym_box.blockSignals(False)
        if symbols:
            self._sym_changed(self.sym_box.currentText())

    def _sym_changed(self, sym):
        sym = sym.strip().upper()
        self.date_box.clear()
        d = os.path.join(DATA_ROOT, f"symbol={sym}")
        if os.path.isdir(d):
            dates = sorted([
                x.replace("date=", "")
                for x in os.listdir(d)
                if x.startswith("date=")
            ], reverse=True)
            self.date_box.addItems(dates)

    def _emit_load(self):
        sym  = self.sym_box.currentText().strip().upper()
        date = self.date_box.currentText().strip()
        if sym and date:
            self.load_requested.emit(sym, date)

    def set_info(self, t):
        self.info_lbl.setText(t)


# ═══════════════════════════════════════════════════════════════════════════════
#  Playback Controls
# ═══════════════════════════════════════════════════════════════════════════════

class PlaybackControls(QWidget):
    play_pause = pyqtSignal()
    step_back  = pyqtSignal()
    step_fwd   = pyqtSignal()
    speed_ch   = pyqtSignal(float)
    seek       = pyqtSignal(int)

    _SPEEDS = [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 100.0]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(46)
        self.setStyleSheet(
            f"background:{PANEL}; border-top:1px solid {BORDER};"
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)

        # Step back
        self.btn_back = QPushButton("◀◀")
        self.btn_back.setFixedSize(30, 28)
        self.btn_back.setToolTip("Step back  (Left arrow)")

        # Play / Pause
        self.btn_play = QPushButton("▶")
        self.btn_play.setFixedSize(44, 28)
        self.btn_play.setStyleSheet(
            f"background:{ACCENT}; color:white; font-size:14px;"
            f" border:none; border-radius:4px;"
        )
        self.btn_play.setToolTip("Play / Pause  (Space)")

        # Step forward
        self.btn_fwd = QPushButton("▶▶")
        self.btn_fwd.setFixedSize(30, 28)
        self.btn_fwd.setToolTip("Step forward  (Right arrow)")

        # Time display
        self.lbl_time = QLabel("—:—:—.———")
        self.lbl_time.setFixedWidth(96)
        self.lbl_time.setAlignment(Qt.AlignCenter)
        self.lbl_time.setStyleSheet(
            f"color:{TEXT_PRI}; font-family:Consolas; font-size:12px;"
            f" font-weight:bold;"
        )

        # Slider
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 10000)

        # Position label
        self.lbl_pos = QLabel("0 / 0")
        self.lbl_pos.setFixedWidth(80)
        self.lbl_pos.setAlignment(Qt.AlignCenter)
        self.lbl_pos.setStyleSheet(f"color:{TEXT_SEC}; font-size:9px;")

        # Speed
        spd_lbl = QLabel("Speed:")
        spd_lbl.setStyleSheet(f"color:{TEXT_SEC}; font-size:10px;")
        self.speed_box = QComboBox()
        self.speed_box.addItems(["0.1×","0.25×","0.5×","1×","2×","5×","10×","50×","100×"])
        self.speed_box.setCurrentIndex(3)
        self.speed_box.setFixedWidth(66)

        lay.addWidget(self.btn_back)
        lay.addWidget(self.btn_play)
        lay.addWidget(self.btn_fwd)
        lay.addWidget(self.lbl_time)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.lbl_pos)
        lay.addWidget(spd_lbl)
        lay.addWidget(self.speed_box)

        self.btn_back.clicked.connect(self.step_back)
        self.btn_play.clicked.connect(self.play_pause)
        self.btn_fwd.clicked.connect(self.step_fwd)
        self.slider.sliderMoved.connect(self.seek)
        self.speed_box.currentIndexChanged.connect(
            lambda i: self.speed_ch.emit(self._SPEEDS[i])
        )

    def set_playing(self, v):
        self.btn_play.setText("⏸" if v else "▶")

    def set_position(self, ts, row, total):
        try:
            self.lbl_time.setText(ts.strftime("%H:%M:%S.%f")[:12])
        except Exception:
            self.lbl_time.setText("—")
        self.lbl_pos.setText(f"{row:,} / {total:,}")
        if total > 0:
            self.slider.blockSignals(True)
            self.slider.setValue(int(row * 10000 / total))
            self.slider.blockSignals(False)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main Window
# ═══════════════════════════════════════════════════════════════════════════════

class DOMReplayWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BIST DOM Replay  —  25-Level Depth of Market")
        self.resize(1440, 980)
        self.setMinimumSize(900, 700)

        self._df        = None
        self._rows      = None   # list of dicts for fast access
        self._cur       = 0
        self._playing   = False
        self._speed     = 1.0
        self._dec       = 2
        self._cum_vol   = 0
        self._cum_notional = 0.0
        self._last_tp   = 0.0
        self._wall_t0   = None
        self._data_t0   = None

        self._build_ui()
        self._load_symbols()

        self._ptimer = QTimer(self)
        self._ptimer.timeout.connect(self._on_tick)
        self._ptimer.setInterval(TIMER_MS)

        # Key shortcuts
        from PyQt5.QtWidgets import QShortcut
        from PyQt5.QtGui import QKeySequence
        QShortcut(QKeySequence(Qt.Key_Space),  self, self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_Left),   self, self._step_back)
        QShortcut(QKeySequence(Qt.Key_Right),  self, self._step_fwd)

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        cw = QWidget()
        self.setCentralWidget(cw)
        vlay = QVBoxLayout(cw)
        vlay.setContentsMargins(0, 0, 0, 0)
        vlay.setSpacing(0)

        self.selector = SelectorBar()
        self.selector.load_requested.connect(self._load)
        vlay.addWidget(self.selector)

        self.summary = SummaryBar()
        vlay.addWidget(self.summary)

        # Content splitter
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(2)
        split.setStyleSheet(f"QSplitter::handle {{ background:{BORDER}; }}")

        # DOM in scroll area
        self.dom = DOMWidget()
        self.dom.resize(self.dom.sizeHint())
        dom_scroll = QScrollArea()
        dom_scroll.setWidget(self.dom)
        dom_scroll.setWidgetResizable(False)
        dom_scroll.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        dom_scroll.setStyleSheet(f"background:{BG}; border:none;")
        dom_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        dom_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.tape = TradeTapeWidget()

        split.addWidget(dom_scroll)
        split.addWidget(self.tape)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([1100, 300])

        vlay.addWidget(split, 1)

        self.controls = PlaybackControls()
        self.controls.play_pause.connect(self._toggle_play)
        self.controls.step_back.connect(self._step_back)
        self.controls.step_fwd.connect(self._step_fwd)
        self.controls.speed_ch.connect(self._set_speed)
        self.controls.seek.connect(self._seek)
        vlay.addWidget(self.controls)

        sb = QStatusBar()
        sb.setStyleSheet(f"background:{PANEL}; color:{TEXT_SEC}; font-size:9px;")
        self.setStatusBar(sb)
        sb.showMessage("Ready — select a symbol and date then press Load")

        # Scroll DOM to center on spread initially
        QTimer.singleShot(200, self._center_scroll)

    # ── Center DOM scroll on spread row ──────────────────────────────────────
    def _center_scroll(self):
        if not self.centralWidget():
            return
        scroll = None
        for child in self.centralWidget().findChildren(QScrollArea):
            scroll = child
            break
        if scroll:
            dom_h   = self.dom.sizeHint().height()
            view_h  = scroll.viewport().height()
            spread_y = self.dom.ROW_H * (LEVELS + 1)
            target   = max(0, spread_y - view_h // 2)
            scroll.verticalScrollBar().setValue(target)

    # ── Load available symbols ────────────────────────────────────────────────
    def _load_symbols(self):
        if not os.path.isdir(DATA_ROOT):
            self.statusBar().showMessage(f"⚠  Data root not found: {DATA_ROOT}")
            return
        syms = [
            d[7:] for d in os.listdir(DATA_ROOT)
            if d.startswith("symbol=") and
            os.path.isdir(os.path.join(DATA_ROOT, d))
        ]
        self.selector.populate(syms)
        self.statusBar().showMessage(
            f"{len(syms):,} symbols available  ·  {DATA_ROOT}"
        )

    # ── Load parquet file ─────────────────────────────────────────────────────
    def _load(self, sym, date):
        path = os.path.join(DATA_ROOT, f"symbol={sym}", f"date={date}", "data.parquet")
        if not os.path.exists(path):
            QMessageBox.warning(self, "File not found", path)
            return

        self._stop()
        self.statusBar().showMessage(f"Loading  {sym}  {date} …")
        QApplication.processEvents()

        try:
            tbl = pq.read_table(path)
            df  = tbl.to_pandas()

            if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
                df["timestamp"] = pd.to_datetime(df["timestamp"])

            self._df   = df
            self._rows = df.to_dict("records")
            self._dec  = int(df["decimals_price"].iloc[0] or 2) if "decimals_price" in df.columns else 2
            self._cur  = 0
            self._cum_vol = 0
            self._cum_notional = 0.0
            self._last_tp = 0.0

            self.tape.clear_tape()
            n = len(df)
            self.selector.set_info(
                f"  {n:,} rows   ·   {sym}   {date}"
            )
            self.setWindowTitle(f"BIST DOM Replay  —  {sym}  {date}")
            self.statusBar().showMessage(
                f"Loaded  {n:,} rows  ·  {sym}  /  {date}"
            )
            self._render(0)
            QTimer.singleShot(100, self._center_scroll)

        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))
            self.statusBar().showMessage(f"Error: {e}")

    # ── Render a single row ───────────────────────────────────────────────────
    def _render(self, idx):
        rows = self._rows
        if not rows or idx >= len(rows):
            return

        snap = rows[idx]
        prev = rows[idx - 1] if idx > 0 else None

        # Trade?
        tp_raw = snap.get("itch_trade_price") or 0
        tqty   = int(snap.get("itch_exec_qty") or 0)
        tp     = tp_raw / (10 ** self._dec) if tp_raw else 0.0
        tside  = snap.get("itch_side") or ""

        if tp > 0 and tqty > 0:
            self._last_tp      = tp
            self._cum_vol      += tqty
            self._cum_notional += snap.get("notional") or 0.0
            self.tape.add_trade(
                snap["timestamp"], tp, tqty, tside, self._dec,
                buyer  = snap.get("buyer")  or snap.get("buyer_broker_id")  or "",
                seller = snap.get("seller") or snap.get("seller_broker_id") or "",
            )

        self.dom.set_snapshot(snap, prev, tp if tp > 0 else None)
        self.summary.update_stats(
            snap, self._dec,
            self._cum_vol, self._cum_notional,
            self._last_tp
        )
        self.controls.set_position(snap["timestamp"], idx, len(rows))
        self._cur = idx

    # ── Playback tick ─────────────────────────────────────────────────────────
    def _on_tick(self):
        rows = self._rows
        if not rows or self._cur >= len(rows) - 1:
            self._stop()
            self.statusBar().showMessage("Replay finished.")
            return

        wall_elapsed = (time.monotonic() - self._wall_t0) * self._speed
        target_ts    = self._data_t0 + pd.Timedelta(seconds=wall_elapsed)

        # Find target row
        new = self._cur
        limit = min(new + MAX_ROWS_PER_TICK, len(rows) - 1)
        while new < limit:
            if rows[new + 1]["timestamp"] <= target_ts:
                new += 1
            else:
                break

        if new != self._cur:
            # Process trades for skipped rows (not rendered)
            for i in range(self._cur + 1, new):
                r   = rows[i]
                tr  = r.get("itch_trade_price") or 0
                tq  = int(r.get("itch_exec_qty") or 0)
                tp2 = tr / (10 ** self._dec) if tr else 0.0
                if tp2 > 0 and tq > 0:
                    self._last_tp       = tp2
                    self._cum_vol      += tq
                    self._cum_notional += r.get("notional") or 0.0
                    self.tape.add_trade(
                        r["timestamp"], tp2, tq,
                        r.get("itch_side") or "", self._dec,
                        buyer  = r.get("buyer")  or r.get("buyer_broker_id")  or "",
                        seller = r.get("seller") or r.get("seller_broker_id") or "",
                    )
            self._render(new)

    # ── Controls ──────────────────────────────────────────────────────────────
    def _toggle_play(self):
        if not self._rows:
            return
        if self._playing:
            self._stop()
        else:
            self._playing   = True
            self._wall_t0   = time.monotonic()
            self._data_t0   = self._rows[self._cur]["timestamp"]
            self.controls.set_playing(True)
            self._ptimer.start()

    def _stop(self):
        self._playing = False
        self._ptimer.stop()
        self.controls.set_playing(False)

    def _step_back(self):
        self._stop()
        if self._rows:
            self._render(max(0, self._cur - 1))

    def _step_fwd(self):
        self._stop()
        if self._rows:
            self._render(min(len(self._rows) - 1, self._cur + 1))

    def _set_speed(self, spd):
        was = self._playing
        if was:
            self._stop()
        self._speed = spd
        if was:
            self._toggle_play()

    def _seek(self, slider_val):
        if not self._rows:
            return
        self._stop()
        n   = len(self._rows)
        idx = int(slider_val * (n - 1) / 10000)

        # Recompute cumulative stats up to idx using vectorized pandas
        df   = self._df.iloc[:idx + 1]
        mask = (df["itch_trade_price"].fillna(0) > 0) & (df["itch_exec_qty"].fillna(0) > 0)
        tdf  = df[mask]
        self._cum_vol      = int(tdf["itch_exec_qty"].sum())
        self._cum_notional = float(tdf["notional"].fillna(0).sum())

        # Rebuild tape from last 100 trades before idx
        self.tape.clear_tape()
        tail = tdf.tail(100)
        for _, r in tail.iterrows():
            tp_raw = r.get("itch_trade_price") or 0
            tp     = tp_raw / (10 ** self._dec) if tp_raw else 0.0
            tq     = int(r.get("itch_exec_qty") or 0)
            if tp > 0 and tq > 0:
                self.tape.add_trade(
                    r["timestamp"], tp, tq,
                    r.get("itch_side") or "", self._dec,
                    buyer  = r.get("buyer")  or r.get("buyer_broker_id")  or "",
                    seller = r.get("seller") or r.get("seller_broker_id") or "",
                )

        self._render(idx)


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette
    pal = QPalette()
    pal.setColor(QPalette.Window,          C(BG))
    pal.setColor(QPalette.WindowText,      C(TEXT_PRI))
    pal.setColor(QPalette.Base,            C(PANEL))
    pal.setColor(QPalette.AlternateBase,   C(HEADER_BG))
    pal.setColor(QPalette.ToolTipBase,     C(PANEL))
    pal.setColor(QPalette.ToolTipText,     C(TEXT_PRI))
    pal.setColor(QPalette.Text,            C(TEXT_PRI))
    pal.setColor(QPalette.Button,          C(PANEL))
    pal.setColor(QPalette.ButtonText,      C(TEXT_PRI))
    pal.setColor(QPalette.Highlight,       C(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    app.setPalette(pal)
    app.setStyleSheet(APP_CSS)

    win = DOMReplayWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
