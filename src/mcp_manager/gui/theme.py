"""Design tokens and the Qt style sheet for the desktop workbench."""
from __future__ import annotations
LIGHT = dict(
    bg='#f3f5fa', surface='#ffffff', surface2='#f8fafc', hover='#f1f4f9', border='#e3e8f0', border_strong='#cfd7e3',
    text='#0f172a', muted='#64748b', subtle='#94a3b8',
    accent='#4f46e5', accent_hover='#4338ca', accent_soft='#eef2ff', accent_text='#4338ca',
    success='#15803d', success_soft='#dcfce7', warning='#b45309', warning_soft='#fef3c7',
    danger='#dc2626', danger_soft='#fee2e2', info='#0369a1', info_soft='#e0f2fe',
    sidebar='#0f172a', sidebar_text='#cbd5e1', sidebar_muted='#64748b', sidebar_active='#1e293b', sidebar_border='#1e293b',
    code_bg='#f8fafc', hero_a='#4f46e5', hero_b='#7c3aed',
)
DARK = dict(
    bg='#0b1020', surface='#121a2b', surface2='#0f1626', hover='#18213a', border='#222c42', border_strong='#2f3a55',
    text='#e5e9f2', muted='#8d97ad', subtle='#5f6a82',
    accent='#818cf8', accent_hover='#a5b4fc', accent_soft='#1f2550', accent_text='#c7d2fe',
    success='#4ade80', success_soft='#12301f', warning='#fbbf24', warning_soft='#33270b',
    danger='#f87171', danger_soft='#3a1518', info='#38bdf8', info_soft='#0c2a3d',
    sidebar='#070b16', sidebar_text='#cbd5e1', sidebar_muted='#5f6a82', sidebar_active='#151d33', sidebar_border='#151d33',
    code_bg='#0c1322', hero_a='#4338ca', hero_b='#6d28d9',
)


def palette(theme: str) -> dict:
    return DARK if theme == 'dark' else LIGHT


def stylesheet(p: dict) -> str:
    return f'''
    QWidget {{ color: {p['text']}; font-size: 13px; }}
    QMainWindow, QWidget#content, QStackedWidget#pages, QScrollArea#pageScroll, QWidget#pageBody {{ background: {p['bg']}; }}
    QToolTip {{ background: {p['sidebar']}; color: #f8fafc; border: none; padding: 6px 8px; border-radius: 6px; }}
    /* Sidebar */
    QFrame#sidebar {{ background: {p['sidebar']}; border-right: 1px solid {p['sidebar_border']}; }}
    QLabel#logo {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {p['hero_a']}, stop:1 {p['hero_b']});
        color: white; font-size: 16px; font-weight: 800; border-radius: 10px; }}
    QLabel#brand {{ color: #f8fafc; font-size: 16px; font-weight: 700; }}
    QLabel#brandSub {{ color: {p['sidebar_muted']}; font-size: 12px; }}
    QLabel#navSection {{ color: {p['sidebar_muted']}; font-size: 11px; font-weight: 600; letter-spacing: 1px; padding-left: 12px; }}
    QListWidget#nav {{ background: transparent; border: none; outline: none; color: {p['sidebar_text']}; }}
    QListWidget#nav::item {{ padding: 10px 12px; border-radius: 8px; margin: 2px 0; border-left: 3px solid transparent; }}
    QListWidget#nav::item:hover {{ background: {p['sidebar_active']}; }}
    QListWidget#nav::item:selected {{ background: {p['sidebar_active']}; color: #ffffff; border-left: 3px solid {p['accent']}; }}
    QFrame#sideFooter {{ background: {p['sidebar_active']}; border-radius: 10px; }}
    QLabel#sideFooterTitle {{ color: #f8fafc; font-weight: 600; }}
    QLabel#sideFooterText {{ color: {p['sidebar_muted']}; font-size: 12px; }}
    /* Top bar */
    QFrame#topbar {{ background: {p['bg']}; }}
    QLabel#pageTitle {{ font-size: 22px; font-weight: 700; }}
    QLabel#pageSubtitle {{ color: {p['muted']}; }}
    QLabel#statusPill {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 13px; padding: 4px 12px; color: {p['muted']}; }}
    QLabel#statusPill[busy="true"] {{ background: {p['accent_soft']}; border-color: {p['accent_soft']}; color: {p['accent_text']}; }}
    QProgressBar#busyBar {{ background: transparent; border: none; max-height: 2px; }}
    QProgressBar#busyBar::chunk {{ background: {p['accent']}; }}
    /* Cards */
    QFrame#card {{ background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 12px; }}
    QLabel#cardTitle {{ font-size: 14px; font-weight: 650; }}
    QLabel#cardHint, QLabel#muted {{ color: {p['muted']}; }}
    QLabel#fieldLabel {{ color: {p['muted']}; font-size: 12px; font-weight: 600; }}
    QLabel#metricValue {{ font-size: 30px; font-weight: 700; }}
    QLabel#metricTitle {{ color: {p['muted']}; font-weight: 500; }}
    QLabel#iconBadge {{ border-radius: 9px; background: {p['accent_soft']}; }}
    QLabel#iconBadge[tone="success"] {{ background: {p['success_soft']}; }}
    QLabel#iconBadge[tone="warning"] {{ background: {p['warning_soft']}; }}
    QLabel#iconBadge[tone="info"] {{ background: {p['info_soft']}; }}
    QLabel#iconBadge[tone="danger"] {{ background: {p['danger_soft']}; }}
    QFrame#hero {{ border-radius: 14px; background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {p['hero_a']}, stop:1 {p['hero_b']}); }}
    QFrame#hero QLabel {{ color: white; }}
    QLabel#heroTitle {{ font-size: 20px; font-weight: 700; }}
    QLabel#heroText {{ color: rgba(255,255,255,0.82); }}
    QFrame#hero QPushButton {{ background: rgba(255,255,255,0.16); border: 1px solid rgba(255,255,255,0.28); color: white; }}
    QFrame#hero QPushButton:hover {{ background: rgba(255,255,255,0.26); }}
    QFrame#hero QPushButton#heroPrimary {{ background: white; color: {p['hero_a']}; border-color: white; font-weight: 600; }}
    QFrame#step {{ background: {p['surface2']}; border: 1px solid {p['border']}; border-radius: 10px; }}
    QLabel#stepNum {{ background: {p['accent']}; color: white; border-radius: 11px; font-weight: 700; font-size: 12px; }}
    QLabel#stepTitle {{ font-weight: 600; }}
    QLabel#emptyTitle {{ font-size: 15px; font-weight: 600; }}
    QFrame#divider {{ background: {p['border']}; max-height: 1px; min-height: 1px; border: none; }}
    /* Pills */
    QLabel[pill] {{ border-radius: 10px; padding: 2px 10px; font-size: 12px; font-weight: 600; }}
    QLabel[pill="neutral"] {{ background: {p['hover']}; color: {p['muted']}; }}
    QLabel[pill="success"] {{ background: {p['success_soft']}; color: {p['success']}; }}
    QLabel[pill="warning"] {{ background: {p['warning_soft']}; color: {p['warning']}; }}
    QLabel[pill="danger"] {{ background: {p['danger_soft']}; color: {p['danger']}; }}
    QLabel[pill="info"] {{ background: {p['info_soft']}; color: {p['info']}; }}
    QLabel[pill="accent"] {{ background: {p['accent_soft']}; color: {p['accent_text']}; }}
    /* Buttons */
    QPushButton {{ background: {p['surface']}; border: 1px solid {p['border_strong']}; border-radius: 8px; padding: 7px 14px; min-height: 18px; font-weight: 500; }}
    QPushButton:hover {{ background: {p['hover']}; }}
    QPushButton:pressed {{ background: {p['border']}; }}
    QPushButton:disabled {{ color: {p['subtle']}; }}
    QPushButton#primary {{ background: {p['accent']}; color: white; border: 1px solid {p['accent']}; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
    QPushButton#danger {{ color: {p['danger']}; border-color: {p['border_strong']}; }}
    QPushButton#danger:hover {{ background: {p['danger_soft']}; border-color: {p['danger']}; }}
    QPushButton#ghost, QToolButton#ghost {{ background: transparent; border: 1px solid transparent; color: {p['muted']}; }}
    QPushButton#ghost:hover, QToolButton#ghost:hover {{ background: {p['hover']}; color: {p['text']}; }}
    QPushButton#iconButton {{ padding: 7px; min-width: 18px; }}
    QPushButton#segment {{ border-radius: 0; border-right: none; padding: 7px 14px; background: {p['surface']}; color: {p['muted']}; }}
    QPushButton#segment[first="true"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
    QPushButton#segment[last="true"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; border-right: 1px solid {p['border_strong']}; }}
    QPushButton#segment:checked {{ background: {p['accent_soft']}; color: {p['accent_text']}; font-weight: 600; }}
    QPushButton:focus {{ outline: none; border: 1px solid {p['accent']}; }}
    /* Inputs */
    QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser {{ background: {p['surface']}; border: 1px solid {p['border_strong']}; border-radius: 8px; padding: 7px 10px;
        selection-background-color: {p['accent']}; selection-color: white; }}
    QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {p['accent']}; }}
    QLineEdit[search="true"] {{ padding-left: 8px; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{ background: {p['surface']}; border: 1px solid {p['border']}; selection-background-color: {p['accent_soft']}; selection-color: {p['accent_text']}; outline: none; padding: 4px; }}
    QPlainTextEdit#code {{ background: {p['code_bg']}; font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; border: 1px solid {p['border']}; }}
    QPlainTextEdit#flat {{ background: transparent; border: none; padding: 0; }}
    QPlainTextEdit#activity {{ background: {p['code_bg']}; border: none; border-top: 1px solid {p['border']}; border-radius: 0;
        font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }}
    /* Tables & lists */
    QTableWidget {{ background: {p['surface']}; border: none; gridline-color: transparent; selection-background-color: {p['accent_soft']}; selection-color: {p['text']}; outline: none; }}
    QTableWidget::item {{ padding: 8px 10px; border-bottom: 1px solid {p['border']}; }}
    QTableWidget::item:selected {{ background: {p['accent_soft']}; color: {p['text']}; }}
    QHeaderView {{ background: transparent; }}
    QHeaderView::section {{ background: {p['surface2']}; color: {p['muted']}; border: none; border-bottom: 1px solid {p['border']};
        padding: 9px 10px; font-size: 12px; font-weight: 600; }}
    QTableCornerButton::section {{ background: {p['surface2']}; border: none; }}
    QListWidget#plainList {{ background: transparent; border: none; outline: none; }}
    QListWidget#plainList::item {{ border-bottom: 1px solid {p['border']}; padding: 2px; }}
    QListWidget#plainList::item:selected {{ background: {p['accent_soft']}; color: {p['text']}; }}
    /* Tabs */
    QTabWidget::pane {{ border: none; background: transparent; }}
    QTabBar {{ background: transparent; }}
    QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 8px 14px; margin-right: 4px; border: none; border-bottom: 2px solid transparent; font-weight: 500; }}
    QTabBar::tab:selected {{ color: {p['accent_text']}; border-bottom: 2px solid {p['accent']}; }}
    QTabBar::tab:hover {{ color: {p['text']}; }}
    /* Activity drawer */
    QFrame#drawer {{ background: {p['surface']}; border-top: 1px solid {p['border']}; }}
    QLabel#drawerTitle {{ font-weight: 600; }}
    QLabel#drawerLast {{ color: {p['muted']}; }}
    /* Scrollbars */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p['border_strong']}; border-radius: 4px; min-height: 28px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {p['border_strong']}; border-radius: 4px; min-width: 28px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QSplitter::handle {{ background: transparent; }}
    QMessageBox {{ background: {p['surface']}; }}
    '''
