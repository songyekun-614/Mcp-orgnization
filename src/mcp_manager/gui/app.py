"""Native Qt desktop client. All business operations belong to ManagerCore."""
from __future__ import annotations
import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from . import icons
from .theme import palette, stylesheet
from .widgets import Card, EmptyState, Segmented, divider, field, label, pill


def pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def short_time(value):
    if not value:
        return '—'
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).astimezone().strftime('%m-%d %H:%M:%S')
    except ValueError:
        return str(value)[:19].replace('T', ' ')


STATUS_TONES = {
    'completed': 'success', 'complete': 'success', 'running': 'info', 'queued': 'neutral',
    'failed': 'danger', 'cancelled': 'warning', 'pending': 'warning',
}
LEVEL_TONES = {'static': 'info', 'handshake': 'accent', 'tools': 'success', 'invoked': 'success', 'failed': 'danger'}
REGISTRY_KINDS = {'pypi': 'python', 'npm': 'node', 'oci': 'docker', 'docker': 'docker'}


class WorkerSignals(QObject):
    completed = Signal(str, object, str)


class Worker(QRunnable):
    def __init__(self, token, operation):
        super().__init__()
        self.token, self.operation = token, operation
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.operation()
        except Exception as exc:
            self.signals.completed.emit(self.token, None, str(exc))
        else:
            self.signals.completed.emit(self.token, result, '')


class MainWindow(QMainWindow):
    PAGE_NAMES = ['总览', '已安装服务', '发现服务', '安装中心', '客户端', '扩展中心', '监控与日志', '设置']
    PAGE_ICONS = ['dashboard', 'server', 'scan', 'package', 'monitor', 'puzzle', 'activity', 'settings']
    PAGE_SUBTITLES = [
        '集中查看服务、工具与最近任务。所有指标均来自本地 Core 的实际记录。',
        '查看登记来源、验证等级与工具详情；连接测试也会先展示执行计划。',
        '静态扫描不会执行候选项目。深度扫描需要独立批准；Registry 搜索在后台运行。',
        '安装请求交给 Core 生成计划；批准后执行。依赖、目标位置与权限以预览为准。',
        '配置路径由平台适配器提供。先查看变更计划，批准后执行带备份与哈希校验的写入。',
        '扩展操作由 Core 的隔离与授权机制执行。当前 Core 未提供的能力会明确报告不支持。',
        '查看 Core 脱敏审计与任务日志。错误会保留在此，便于检查真实执行结果。',
        '非敏感偏好保存在本地。镜像参数由 Core 校验；导入配置仅生成候选，不自动安装。',
    ]

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.pool = QThreadPool(self)
        self._jobs = {}
        self._servers = []
        self._tints = []
        self._theme_applied = None
        self._palette = palette('light')
        self.setWindowTitle('MCP Manager · 服务工作台')
        self.resize(1320, 860)
        self.setMinimumSize(880, 640)
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())
        content_widget = QWidget()
        content_widget.setObjectName('content')
        content = QVBoxLayout(content_widget)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self._build_topbar())
        self.busy_bar = QProgressBar()
        self.busy_bar.setObjectName('busyBar')
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedHeight(2)
        self.busy_bar.setVisible(False)
        content.addWidget(self.busy_bar)
        self.pages = QStackedWidget()
        self.pages.setObjectName('pages')
        content.addWidget(self.pages, 1)
        content.addWidget(self._build_drawer())
        outer.addWidget(content_widget, 1)
        self._build_dashboard()
        self._build_installed()
        self._build_discover()
        self._build_installation()
        self._build_clients()
        self._build_extensions()
        self._build_monitoring()
        self._build_settings()
        self.navigation.currentRowChanged.connect(self._navigate)
        self.navigation.setCurrentRow(0)
        self.apply_theme('light')
        self.refresh()
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start()

    # ------------------------------------------------------------------ chrome
    def _icon_label(self, name, role='accent', size=18, box=None, tone=None):
        widget = QLabel()
        widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if box:
            widget.setObjectName('iconBadge')
            widget.setFixedSize(box, box)
            if tone:
                widget.setProperty('tone', tone)
        self._tints.append((widget, name, role, size, 'label'))
        return widget

    def _tint(self, widget, name, role='text', size=16):
        if isinstance(widget, QPushButton) and widget.text() and not widget.text().startswith(' '):
            widget.setText(' ' + widget.text())
        self._tints.append((widget, name, role, size, 'button' if isinstance(widget, QPushButton) else 'action'))
        return widget

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(232)
        self.sidebar = sidebar
        self._compact = None
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 22, 16, 18)
        layout.setSpacing(0)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        logo = label('M', 'logo')
        logo.setFixedSize(36, 36)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_row.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = label('MCP Manager', 'brand')
        brand_text.addWidget(brand)
        brand_text.addWidget(label('本地服务工作台', 'brandSub'))
        self.brand_text = QWidget()
        self.brand_text.setLayout(brand_text)
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_row.addWidget(self.brand_text, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(26)
        self.nav_section = label('导航', 'navSection')
        layout.addWidget(self.nav_section)
        layout.addSpacing(6)
        self.navigation = QListWidget()
        self.navigation.setObjectName('nav')
        self.navigation.setAccessibleName('主导航')
        self.navigation.setIconSize(QSize(18, 18))
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for name in self.PAGE_NAMES:
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, 42))
            self.navigation.addItem(item)
        self.navigation.setCursor(Qt.CursorShape.PointingHandCursor)
        layout.addWidget(self.navigation, 1)
        footer = QFrame()
        footer.setObjectName('sideFooter')
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(14, 12, 14, 12)
        footer_layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._icon_label('shield', 'accent_sidebar', 16))
        row.addWidget(label('变更需本地批准', 'sideFooterTitle'), 1)
        footer_layout.addLayout(row)
        footer_layout.addWidget(label('V2.0 · 所有执行先预览计划', 'sideFooterText', wrap=True))
        self.side_footer = footer
        layout.addWidget(footer)
        return sidebar

    def _build_topbar(self):
        bar = QFrame()
        bar.setObjectName('topbar')
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(32, 22, 32, 14)
        layout.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.page_title = label('', 'pageTitle')
        self.page_subtitle = label('', 'pageSubtitle', wrap=True)
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        layout.addLayout(titles, 1)
        self.busy_label = label('● 本地工作区', 'statusPill')
        layout.addWidget(self.busy_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.theme_toggle = QPushButton()
        self.theme_toggle.setObjectName('iconButton')
        self.theme_toggle.setToolTip('切换浅色 / 深色主题')
        self.theme_toggle.setAccessibleName('切换主题')
        self.theme_toggle.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        refresh = self._tint(QPushButton('刷新'), 'refresh')
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _build_drawer(self):
        drawer = QFrame()
        drawer.setObjectName('drawer')
        layout = QVBoxLayout(drawer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(24, 8, 16, 8)
        header.setSpacing(10)
        header.addWidget(self._icon_label('terminal', 'muted', 16))
        header.addWidget(label('活动日志', 'drawerTitle'))
        self.activity_last = label('操作记录、错误与授权结果会显示在这里。', 'drawerLast')
        self.activity_last.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.activity_last, 1)
        clear = self._tint(QPushButton('清空'), 'eraser', 'muted', 14)
        clear.setObjectName('ghost')
        header.addWidget(clear)
        self.activity_toggle = QPushButton('展开')
        self.activity_toggle.setObjectName('ghost')
        self.activity_toggle.clicked.connect(lambda: self._set_drawer(not self.activity.isVisible()))
        header.addWidget(self.activity_toggle)
        layout.addLayout(header)
        self.activity = QPlainTextEdit()
        self.activity.setObjectName('activity')
        self.activity.setReadOnly(True)
        self.activity.setAccessibleName('操作状态与错误')
        self.activity.setPlaceholderText('操作记录、错误与授权结果会显示在这里。')
        self.activity.setFixedHeight(150)
        self.activity.setVisible(False)
        clear.clicked.connect(self.activity.clear)
        layout.addWidget(self.activity)
        return drawer

    def _set_drawer(self, visible):
        self.activity.setVisible(visible)
        self.activity_toggle.setText('收起' if visible else '展开')
        self._refresh_drawer_icon()

    def _refresh_drawer_icon(self):
        name = 'chevron-down' if self.activity.isVisible() else 'chevron-up'
        self.activity_toggle.setIcon(icons.icon(name, self._palette['muted'], size=14))

    # ------------------------------------------------------------------ helpers
    def _page(self, scroll=False):
        body = QWidget()
        body.setObjectName('pageBody')
        layout = QVBoxLayout(body)
        layout.setContentsMargins(32, 6, 32, 24)
        layout.setSpacing(16)
        if scroll:
            area = QScrollArea()
            area.setObjectName('pageScroll')
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(body)
            self.pages.addWidget(area)
        else:
            self.pages.addWidget(body)
        return layout

    def _button(self, layout, text, callback, primary=False, icon=None, name=None):
        button = QPushButton(text)
        if primary:
            button.setObjectName('primary')
        elif name:
            button.setObjectName(name)
        if icon:
            role = 'on_accent' if primary else ('danger' if name == 'danger' else 'text')
            self._tint(button, icon, role)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        if layout is not None:
            layout.addWidget(button)
        return button

    def _editor(self, placeholder='', readonly=True, code=True):
        editor = QPlainTextEdit()
        editor.setReadOnly(readonly)
        editor.setPlaceholderText(placeholder)
        if code:
            editor.setObjectName('code')
        return editor

    def _table(self, headers, stretch_column=0, fixed=None):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setAlternatingRowColors(False)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(44)
        header = table.horizontalHeader()
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for column in range(len(headers)):
            mode = QHeaderView.ResizeMode.Stretch if column == stretch_column else QHeaderView.ResizeMode.ResizeToContents
            header.setSectionResizeMode(column, mode)
        for column, width in (fixed or {}).items():
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(column, width)
        return table
    @staticmethod
    def _pill_cell(table, row, column, text, tone):
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.addWidget(pill(text, tone))
        layout.addStretch()
        item = QTableWidgetItem('')
        item.setData(Qt.ItemDataRole.UserRole, text)
        table.setItem(row, column, item)
        table.setCellWidget(row, column, holder)

    def _metric_card(self, key, title, icon_name, tone):
        card = Card(padding=18, spacing=6)
        top = QHBoxLayout()
        top.addWidget(label(title, 'metricTitle'), 1, Qt.AlignmentFlag.AlignTop)
        top.addWidget(self._icon_label(icon_name, tone, 18, box=36, tone=tone))
        card.add_layout(top)
        value = label('—', 'metricValue')
        self.metric_labels[key] = value
        card.add(value)
        return card

    # ------------------------------------------------------------------ pages
    def _build_dashboard(self):
        layout = self._page(scroll=True)
        hero = QFrame()
        hero.setObjectName('hero')
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(28, 24, 28, 24)
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addWidget(label('欢迎使用 MCP Manager', 'heroTitle'))
        text.addWidget(label('发现、安装与连接本地 MCP 服务。所有变更都会先生成计划，由你在本机审查并批准后执行。', 'heroText', wrap=True))
        text.addSpacing(10)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        discover = self._button(actions, '发现本地服务', lambda: self.navigation.setCurrentRow(2))
        discover.setObjectName('heroPrimary')
        self._button(actions, '安装新服务', lambda: self.navigation.setCurrentRow(3))
        self._button(actions, '查看已安装', lambda: self.navigation.setCurrentRow(1))
        actions.addStretch()
        text.addLayout(actions)
        hero_layout.addLayout(text, 1)
        art = self._icon_label('shield', 'white_soft', 76)
        art.setFixedSize(110, 110)
        hero_layout.addWidget(art)
        layout.addWidget(hero)
        grid = QGridLayout()
        grid.setSpacing(16)
        self.metric_labels = {}
        for index, (key, title, icon_name, tone) in enumerate([
            ('registered', '已登记服务', 'server', 'accent'), ('connected', '当前连接', 'link', 'success'),
            ('tools', '可用工具', 'wrench', 'info'), ('updates', '可用更新', 'upgrade', 'warning')]):
            grid.addWidget(self._metric_card(key, title, icon_name, tone), 0, index)
        layout.addLayout(grid)
        flow = Card('变更安全流程', '所有会修改系统的操作都遵循同一条本地批准链路')
        steps = QHBoxLayout()
        steps.setSpacing(12)
        for number, (title, hint) in enumerate([
            ('生成计划', 'Core 汇总命令、来源、版本与权限'),
            ('本地审查', '展开完整脱敏详情，默认选择取消'),
            ('批准执行', '一次性计划在后台执行并写入审计')], 1):
            step = QFrame()
            step.setObjectName('step')
            step_layout = QHBoxLayout(step)
            step_layout.setContentsMargins(12, 10, 12, 10)
            step_layout.setSpacing(12)
            num = label(str(number), 'stepNum')
            num.setFixedSize(22, 22)
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step_layout.addWidget(num, 0, Qt.AlignmentFlag.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(1)
            words.addWidget(label(title, 'stepTitle'))
            words.addWidget(label(hint, 'muted', wrap=True))
            step_layout.addLayout(words, 1)
            steps.addWidget(step, 1)
            if number < 3:
                steps.addWidget(self._icon_label('arrow-right', 'subtle', 16))
        flow.add_layout(steps)
        layout.addWidget(flow)
        row = QHBoxLayout()
        row.setSpacing(16)
        recent = Card('最近活动', '最近任务与验证失败的服务')
        self.dashboard_recent = self._editor('当前没有任务或错误记录。', code=False)
        self.dashboard_recent.setObjectName('flat')
        self.dashboard_recent.setMinimumHeight(170)
        recent.add(self.dashboard_recent, 1)
        row.addWidget(recent, 3)
        overview = Card('服务概况')
        view_all = self._button(None, '全部', lambda: self.navigation.setCurrentRow(1), name='ghost')
        overview.trailing(view_all)
        self.dashboard_servers = QVBoxLayout()
        self.dashboard_servers.setSpacing(8)
        overview.add_layout(self.dashboard_servers)
        overview.body.addStretch()
        row.addWidget(overview, 2)
        layout.addLayout(row, 1)

    def _build_installed(self):
        layout = self._page()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.installed_splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(16)
        list_card = Card('服务列表', padding=0, spacing=0)
        list_card.body.setContentsMargins(0, 16, 0, 0)
        list_card.header.setContentsMargins(20, 0, 16, 12)
        self.server_count = pill('0 项', 'neutral')
        list_card.trailing(self.server_count)
        self.server_filter = QLineEdit()
        self.server_filter.setPlaceholderText('筛选服务…')
        self.server_filter.setClearButtonEnabled(True)
        self.server_filter.setMinimumWidth(140)
        self.server_filter.setMaximumWidth(220)
        search_action = QAction(self)
        self._tint(search_action, 'search', 'subtle', 14)
        self.server_filter.addAction(search_action, QLineEdit.ActionPosition.LeadingPosition)
        self.server_filter.textChanged.connect(self._filter_servers)
        list_card.trailing(self.server_filter)
        self.servers_stack = QStackedWidget()
        self.servers_table = self._table(['服务', '传输', '启用', '管理方式', '验证等级'], fixed={2: 96, 4: 136})
        self.servers_table.itemSelectionChanged.connect(self._server_selected)
        self.servers_stack.addWidget(self.servers_table)
        empty_action = self._button(None, '发现本地服务', lambda: self.navigation.setCurrentRow(2), True, 'scan')
        self.servers_stack.addWidget(EmptyState(self._icon_label('server', 'accent', 22, box=52), '尚未登记任何服务',
                                                '扫描本地项目与客户端配置，或在安装中心创建安装计划。', empty_action))
        list_card.add(self.servers_stack, 1)
        splitter.addWidget(list_card)
        detail = Card(padding=20, spacing=10)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(self._icon_label('server', 'accent', 18, box=40))
        names = QVBoxLayout()
        names.setSpacing(2)
        self.detail_title = label('未选择服务', 'cardTitle')
        self.detail_sub = label('从左侧列表选择服务查看详情', 'muted')
        self.detail_sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        names.addWidget(self.detail_title)
        names.addWidget(self.detail_sub)
        head.addLayout(names, 1)
        detail.add_layout(head)
        self.detail_pills = QHBoxLayout()
        self.detail_pills.setSpacing(6)
        self.detail_pills.addStretch()
        detail.add_layout(self.detail_pills)
        tabs = QTabWidget()
        self.tools_list = QListWidget()
        self.tools_list.setObjectName('plainList')
        self.tools_list.setWordWrap(True)
        self.tools_list.setIconSize(QSize(16, 16))
        self.tools_list.setSpacing(2)
        tabs.addTab(self.tools_list, '工具')
        self.server_details = self._editor('选择服务查看工具、来源和验证详情。')
        tabs.addTab(self.server_details, '原始数据')
        detail.add(tabs, 1)
        detail.add(divider())
        actions = QGridLayout()
        actions.setSpacing(8)
        test = self._button(None, '连接测试', lambda: self.server_action('test'), True, 'plug')
        actions.addWidget(test, 0, 0, 1, 2)
        update = self._button(None, '更新', lambda: self.server_action('update'), icon='upgrade')
        actions.addWidget(update, 0, 2)
        rollback = self._button(None, '回滚', lambda: self.server_action('rollback'), icon='rollback')
        actions.addWidget(rollback, 0, 3)
        for column, (title, action, icon_name) in enumerate([('启用', 'enable', 'toggle-on'), ('禁用', 'disable', 'toggle-off'),
                                                               ('启动', 'start', 'play'), ('停止', 'stop', 'stop')]):
            button = self._button(None, title, lambda checked=False, a=action: self.server_action(a), icon=icon_name)
            actions.addWidget(button, 1, column)
        uninstall = self._button(None, '卸载', lambda: self.server_action('uninstall'), icon='trash', name='danger')
        actions.addWidget(uninstall, 2, 0, 1, 4)
        detail.add_layout(actions)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([620, 420])
        layout.addWidget(splitter, 1)

    def _build_discover(self):
        layout = self._page()
        row = QHBoxLayout()
        row.setSpacing(16)
        scan = Card('本地扫描', '静态读取配置与项目，不会执行候选程序')
        scan.trailing(self._icon_label('scan', 'accent', 18, box=36))
        self.scan_mode = QComboBox()
        self.scan_mode.addItems(['quick', 'standard', 'deep'])
        self.scan_mode.setAccessibleName('扫描模式')
        self.scan_mode.setVisible(False)
        scan.add(self.scan_mode)
        mode_hint = label('', 'muted', wrap=True)
        hints = {'quick': '浅层静态扫描，耗时最短。', 'standard': '在扫描根内进行有界静态发现。',
                 'deep': '需单独批准：最多 8 层、5 万文件、60 秒。'}
        segmented = Segmented(self.scan_mode, {'quick': '快速', 'standard': '标准', 'deep': '深度'})
        segmented.changed.connect(lambda mode: mode_hint.setText(hints.get(mode, '')))
        mode_hint.setText(hints[self.scan_mode.currentText()])
        scan.add(field('扫描模式', segmented))
        scan.add(mode_hint)
        roots = QHBoxLayout()
        roots.setSpacing(8)
        self.scan_roots = QLineEdit()
        self.scan_roots.setPlaceholderText('扫描目录（留空使用设置中的默认扫描根）')
        roots.addWidget(self.scan_roots, 1)
        self._button(roots, '选择', self.choose_scan_root, icon='folder')
        scan.add(field('扫描根目录', self._wrap(roots)))
        scan_actions = QHBoxLayout()
        scan_actions.addStretch()
        self._button(scan_actions, '开始扫描', self.scan, True, 'scan')
        scan.add_layout(scan_actions)
        row.addWidget(scan, 1)
        registry = Card('Registry 搜索', '查询官方 MCP Registry，可一键填入安装表单')
        registry.trailing(self._icon_label('search', 'info', 18, box=36, tone='info'))
        search = QHBoxLayout()
        search.setSpacing(8)
        self.registry_query = QLineEdit()
        self.registry_query.setPlaceholderText('例如 filesystem、github、postgres')
        self.registry_query.setClearButtonEnabled(True)
        self.registry_query.returnPressed.connect(self.search_registry)
        search.addWidget(self.registry_query, 1)
        registry.add(field('关键词', self._wrap(search)))
        registry.add(label('搜索在后台运行；离线时 Core 会使用缓存结果。', 'muted', wrap=True))
        registry.body.addStretch()
        registry_actions = QHBoxLayout()
        registry_actions.addStretch()
        self._button(registry_actions, '搜索 Registry', self.search_registry, True, 'search')
        registry.add_layout(registry_actions)
        row.addWidget(registry, 1)
        layout.addLayout(row)
        results = Card('结果')
        self.discover_tabs = QTabWidget()
        self.registry_list = QListWidget()
        self.registry_list.setObjectName('plainList')
        self.registry_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.registry_stack = QStackedWidget()
        self.registry_stack.addWidget(EmptyState(self._icon_label('search', 'info', 20, box=46, tone='info'), '暂无搜索结果',
                                                 '输入关键词搜索官方 Registry，或运行本地扫描查看原始输出。'))
        self.registry_stack.addWidget(self.registry_list)
        self.discover_tabs.addTab(self.registry_stack, 'Registry 结果')
        self.discovery_results = self._editor('扫描候选和 Registry 搜索结果将显示在这里。')
        self.discover_tabs.addTab(self.discovery_results, '原始输出')
        results.add(self.discover_tabs, 1)
        layout.addWidget(results, 1)
    @staticmethod
    def _wrap(inner_layout):
        holder = QWidget()
        inner_layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(inner_layout)
        return holder

    def _build_installation(self):
        layout = self._page(scroll=True)
        row = QHBoxLayout()
        row.setSpacing(16)
        form = Card('新建安装计划', 'Core 校验参数并生成计划，批准前不会改动系统')
        self.install_kind = QComboBox()
        self.install_kind.addItems(['python', 'node', 'docker', 'git'])
        self.install_kind.setVisible(False)
        form.add(self.install_kind)
        form.add(field('安装类型', Segmented(self.install_kind, {'python': 'Python', 'node': 'Node', 'docker': 'Docker', 'git': 'Git'})))
        self.install_package = QLineEdit()
        self.install_package.setPlaceholderText('包名 / 镜像 / Git URL')
        form.add(field('来源', self.install_package))
        pair = QHBoxLayout()
        pair.setSpacing(12)
        self.install_name = QLineEdit()
        self.install_name.setPlaceholderText('留空则使用来源名')
        self.install_version = QLineEdit()
        self.install_version.setPlaceholderText('例如 1.2.3')
        pair.addWidget(field('服务名称', self.install_name), 1)
        pair.addWidget(field('固定版本', self.install_version), 1)
        form.add_layout(pair)
        self.install_options = self._editor('{"module": "my_mcp"}', False)
        self.install_options.setFixedHeight(96)
        form.add(field('高级参数（JSON）', self.install_options, 'module / local_path / executable / commit / args 等 Core 参数'))
        buttons = QHBoxLayout()
        self._button(buttons, '清空', self._clear_install_form, name='ghost')
        buttons.addStretch()
        self._button(buttons, '生成安装计划', self.install, True, 'clipboard')
        form.add_layout(buttons)
        row.addWidget(form, 3)
        guide = Card('来源说明')
        guide.trailing(self._icon_label('package', 'accent', 18, box=36))
        self.kind_hint = label('', 'muted', wrap=True)
        guide.add(self.kind_hint)
        guide.add(divider())
        for icon_name, text in [('shield', '依赖、目标位置与权限以计划预览为准'), ('file-check', 'Python / Node 需要固定版本'),
                                ('rollback', '已安装服务可在“已安装服务”中更新或回滚')]:
            line = QHBoxLayout()
            line.setSpacing(10)
            line.addWidget(self._icon_label(icon_name, 'muted', 16), 0, Qt.AlignmentFlag.AlignTop)
            line.addWidget(label(text, wrap=True), 1)
            guide.add_layout(line)
        guide.body.addStretch()
        row.addWidget(guide, 2)
        layout.addLayout(row)
        kind_hints = {
            'python': 'PyPI 包名。需填写固定版本，可在高级参数中指定 module 或 executable。',
            'node': 'npm 包名。需填写固定版本，可通过 executable 指定入口命令。',
            'docker': '容器镜像引用，建议使用不可变 digest；Core 会校验镜像来源。',
            'git': 'Git 仓库 URL，将映射为 repository；请在高级参数中提供固定的 commit。',
        }
        self.install_kind.currentTextChanged.connect(lambda kind: self.kind_hint.setText(kind_hints.get(kind, '')))
        self.kind_hint.setText(kind_hints[self.install_kind.currentText()])
        tasks = Card('任务与执行日志')
        tabs = QTabWidget()
        self.tasks_table = self._table(['操作', '状态', '创建时间', '任务 ID'], stretch_column=3, fixed={1: 120})
        self.tasks_table.setMinimumHeight(220)
        tabs.addTab(self.tasks_table, '任务列表')
        self.tasks_view = self._editor('暂无任务。')
        tabs.addTab(self.tasks_view, '原始数据')
        tasks.add(tabs, 1)
        layout.addWidget(tasks, 1)

    def _clear_install_form(self):
        for widget in (self.install_package, self.install_name, self.install_version):
            widget.clear()
        self.install_options.clear()

    def _build_clients(self):
        layout = self._page()
        detected = Card('已检测客户端', '点击一行可将客户端与配置路径填入下方表单')
        tabs = QTabWidget()
        self.clients_table = self._table(['客户端', '配置路径', '状态', '条目'], stretch_column=1, fixed={2: 104})
        self.clients_table.itemSelectionChanged.connect(self._client_selected)
        tabs.addTab(self.clients_table, '列表')
        self.clients_view = self._editor()
        tabs.addTab(self.clients_view, '原始数据')
        detected.add(tabs, 1)
        layout.addWidget(detected, 1)
        change = Card('配置变更', '先生成变更计划；批准后执行带备份与哈希校验的写入')
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        self.client_name = QLineEdit()
        self.client_name.setPlaceholderText('例如 codex、dsh')
        self.client_path = QLineEdit()
        self.client_path.setPlaceholderText('客户端配置文件绝对路径')
        self.client_entry = QLineEdit()
        self.client_entry.setPlaceholderText('写入配置中的条目名')
        self.client_server = QComboBox()
        grid.addWidget(field('客户端标识', self.client_name), 0, 0)
        grid.addWidget(field('配置文件路径', self.client_path), 0, 1)
        grid.addWidget(field('配置条目名称', self.client_entry), 1, 0)
        grid.addWidget(field('服务', self.client_server), 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 2)
        change.add_layout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._button(buttons, '预览移除条目', lambda: self.client_change(True), icon='trash', name='danger')
        self._button(buttons, '预览添加 / 更新', lambda: self.client_change(False), True, 'clipboard')
        change.add_layout(buttons)
        layout.addWidget(change)

    def _build_extensions(self):
        layout = self._page()
        row = QHBoxLayout()
        row.setSpacing(16)
        left = Card('扩展操作', '参数原样交给 Core 校验；返回待批准计划时走同一批准流程')
        self.extension_parameters = self._editor('{"name": "扩展名"}', False)
        self.extension_parameters.setMinimumHeight(140)
        left.add(field('操作参数（JSON 对象）', self.extension_parameters), 1)
        for group, operations in [
            ('开发与验证', [('创建工作区', 'workspace', 'box-plus'), ('提交扩展', 'submit', 'send'),
                         ('静态验证', 'validate', 'file-check'), ('隔离测试', 'test', 'flask')]),
            ('发布与生命周期', [('生成安装计划', 'plan_install', 'package'), ('启用计划', 'enable', 'toggle-on'),
                           ('禁用计划', 'disable', 'toggle-off'), ('回滚计划', 'rollback', 'rollback')])]:
            left.add(label(group, 'fieldLabel'))
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, (title, operation, icon_name) in enumerate(operations):
                button = self._button(None, title, lambda checked=False, op=operation: self.extension_action(op), icon=icon_name)
                grid.addWidget(button, i // 2, i % 2)
            left.add_layout(grid)
        row.addWidget(left, 2)
        right = Card('执行结果')
        right.trailing(self._icon_label('puzzle', 'accent', 18, box=36))
        self.extension_results = self._editor('尚未运行扩展操作。')
        right.add(self.extension_results, 1)
        row.addWidget(right, 3)
        layout.addLayout(row, 1)

    def _build_monitoring(self):
        layout = self._page()
        card = Card('审计记录', '最近 100 条脱敏审计事件，每 5 秒自动刷新')
        card.trailing(self._button(None, '刷新审计日志', self.refresh_status, icon='refresh'))
        tabs = QTabWidget()
        self.audit_table = self._table(['时间', '动作', '详情'], stretch_column=2)
        tabs.addTab(self.audit_table, '审计事件')
        self.logs_view = self._editor('暂无审计记录。')
        tabs.addTab(self.logs_view, '原始数据')
        card.add(tabs, 1)
        layout.addWidget(card, 1)

    def _build_settings(self):
        layout = self._page(scroll=True)
        top = QHBoxLayout()
        top.addStretch()
        self._button(top, '保存设置', self.save_settings, True, 'save')
        layout.addLayout(top)
        appearance = Card('外观', '主题会立即预览，保存后持久生效')
        self.theme = QComboBox()
        self.theme.addItem('浅色', 'light')
        self.theme.addItem('深色', 'dark')
        self.theme.setVisible(False)
        self.theme.currentIndexChanged.connect(lambda: self.apply_theme(self.theme.currentData()))
        appearance.add(self.theme)
        appearance.add(field('界面主题', Segmented(self.theme)))
        layout.addWidget(appearance)
        discovery = Card('发现', '本地扫描未指定目录时使用这些根目录')
        self.settings_roots = self._editor('每行一个扫描根目录', False)
        self.settings_roots.setFixedHeight(110)
        discovery.add(field('默认扫描根', self.settings_roots))
        layout.addWidget(discovery)
        sources = Card('软件源', '必须使用 HTTPS；Core 会在保存时校验')
        self.mirror_url = QLineEdit()
        self.registry_url = QLineEdit()
        pair = QHBoxLayout()
        pair.setSpacing(12)
        pair.addWidget(field('Python 镜像 HTTPS URL', self.mirror_url), 1)
        pair.addWidget(field('Registry HTTPS URL', self.registry_url), 1)
        sources.add_layout(pair)
        layout.addWidget(sources)
        security = Card('安全与隔离', '管理型 MCP 默认拒绝 Agent 执行变更')
        security.trailing(self._icon_label('shield', 'success', 18, box=36, tone='success'))
        self.agent_policy = QComboBox()
        self.agent_policy.addItem('只读与生成计划', 'read_only')
        self.agent_policy.addItem('可执行本地已批准计划', 'approved_only')
        self.sandbox_image = QLineEdit()
        self.sandbox_image.setPlaceholderText('可选：本机已有镜像的 sha256:…，不自动下载')
        pair = QHBoxLayout()
        pair.setSpacing(12)
        pair.addWidget(field('Agent 执行权限', self.agent_policy), 1)
        pair.addWidget(field('扩展隔离镜像', self.sandbox_image), 1)
        security.add_layout(pair)
        layout.addWidget(security)
        migration = Card('数据迁移', '导出不含密钥与工程数据；导入仅生成候选，不自动安装')
        row = QHBoxLayout()
        self._button(row, '导出脱敏配置', self.export_configuration, icon='download')
        self._button(row, '导入配置候选', self.import_configuration, icon='upload')
        row.addStretch()
        self.data_dir_label = label('', 'muted')
        row.addWidget(self.data_dir_label)
        migration.add_layout(row)
        layout.addWidget(migration)
        layout.addStretch()

    # ------------------------------------------------------------------ behaviour
    @Slot(int)
    def _navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.PAGE_NAMES[index])
        self.page_subtitle.setText(self.PAGE_SUBTITLES[index])

    def _toggle_theme(self):
        self.theme.setCurrentIndex(0 if self.theme.currentData() == 'dark' else 1)

    def report(self, message):
        text = str(message)
        stamp = datetime.now().strftime('%H:%M:%S')
        self.activity.appendPlainText(f'[{stamp}] {text}')
        self.activity_last.setText(text.splitlines()[0] if text else '')
        if any(word in text for word in ('失败', '错误', '不支持')):
            self._set_drawer(True)

    def _set_busy(self):
        busy = bool(self._jobs)
        self.busy_label.setText(f'● 运行中 · {len(self._jobs)} 项任务' if busy else '● 本地工作区')
        self.busy_label.setProperty('busy', busy)
        self.busy_label.style().unpolish(self.busy_label)
        self.busy_label.style().polish(self.busy_label)
        self.busy_bar.setVisible(busy)

    def run_background(self, label_text, operation, callback=None):
        token = uuid.uuid4().hex
        worker = Worker(token, operation)
        self._jobs[token] = (label_text, callback, worker)
        worker.signals.completed.connect(self._completed)
        self._set_busy()
        self.report(f'开始：{label_text}')
        self.pool.start(worker)
    @Slot(str, object, str)
    def _completed(self, token, result, error):
        label_text, callback, _worker = self._jobs.pop(token)
        self._set_busy()
        if error:
            self.report(f'{label_text}失败：{error}')
        else:
            self.report(f'{label_text}完成')
            if callback:
                try:
                    callback(result)
                except Exception as exc:
                    self.report(f'显示结果失败：{exc}')
        self.refresh_status()

    def call_background(self, method, *args, callback=None, **kwargs):
        operation = getattr(self.core, method, None)
        if not callable(operation):
            self.report(f'当前 Core 不支持：{method}')
            return
        self.run_background(method, lambda: operation(*args, **kwargs), callback)

    def refresh(self):
        try:
            metrics = self.core.dashboard()
            for key, value_label in self.metric_labels.items():
                value_label.setText(str(metrics.get(key, '—')))
            self.dashboard_recent.setPlainText(self._recent_text(metrics.get('recent_tasks', []), metrics.get('recent_errors', [])))
            selected = self._selected_server_id()
            self._servers = self.core.list_servers()
            self._fill_servers(selected)
            self._fill_dashboard_servers()
            self.client_server.clear()
            for record in self._servers:
                self.client_server.addItem(record.get('spec', {}).get('name', record['id']), record['id'])
            clients = self.core.list_clients()
            self.clients_view.setPlainText(pretty(clients))
            self._fill_clients(clients)
            settings = self.core.get_settings()
            self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get('theme', 'light'))))
            self.settings_roots.setPlainText('\n'.join(settings.get('scan_roots', [])))
            self.mirror_url.setText(settings.get('mirror', 'https://pypi.org/simple'))
            self.registry_url.setText(settings.get('registry_url', 'https://registry.modelcontextprotocol.io'))
            self.agent_policy.setCurrentIndex(max(0, self.agent_policy.findData(settings.get('agent_permission', 'read_only'))))
            self.sandbox_image.setText(settings.get('extension_sandbox_image', '') or '')
            data_dir = settings.get('data_dir')
            self.data_dir_label.setText(f'数据目录：{data_dir}' if data_dir else '')
        except Exception as exc:
            self.report(f'刷新失败：{exc}')
        self.refresh_status()
    @staticmethod
    def _recent_text(tasks, errors):
        lines = []
        if tasks:
            lines.append('最近任务')
            for task in tasks:
                if isinstance(task, dict):
                    lines.append(f"  • {task.get('operation', '任务')} · {task.get('status', '—')} · {short_time(task.get('created_at'))}")
                else:
                    lines.append(f'  • {task}')
        else:
            lines.append('暂无任务记录。可以先发现本地服务，或在安装中心创建计划。')
        lines.append('')
        if errors:
            lines.append('最近错误')
            for record in errors:
                if isinstance(record, dict):
                    name = record.get('spec', {}).get('name', record.get('id', '服务'))
                    message = record.get('validation', {}).get('error') or record.get('validation', {}).get('status', '失败')
                    lines.append(f'  • {name}：{message if isinstance(message, str) else json.dumps(message, ensure_ascii=False, default=str)}')
                else:
                    lines.append(f'  • {record}')
        else:
            lines.append('暂无错误记录。')
        return '\n'.join(lines)

    def _selected_server_id(self):
        row = self.servers_table.currentRow()
        return self._servers[row]['id'] if 0 <= row < len(self._servers) else None

    def _fill_servers(self, selected=None):
        table = self.servers_table
        table.blockSignals(True)
        table.clearContents()
        table.setRowCount(len(self._servers))
        reselect = -1
        for row, record in enumerate(self._servers):
            spec = record.get('spec', {})
            validation = record.get('validation', {}) or {}
            name_item = QTableWidgetItem(str(spec.get('name', record['id'])))
            font = name_item.font()
            font.setWeight(QFont.Weight.DemiBold)
            name_item.setFont(font)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, QTableWidgetItem(str(spec.get('transport', ''))))
            enabled = bool(record.get('enabled'))
            self._pill_cell(table, row, 2, '已启用' if enabled else '已禁用', 'success' if enabled else 'neutral')
            table.setItem(row, 3, QTableWidgetItem('托管' if record.get('managed') else '外部'))
            level = validation.get('level', '未验证')
            tone = 'danger' if validation.get('status') == 'failed' else LEVEL_TONES.get(level, 'neutral')
            self._pill_cell(table, row, 4, str(level), tone)
            if record['id'] == selected:
                reselect = row
        table.blockSignals(False)
        self.servers_stack.setCurrentIndex(0 if self._servers else 1)
        self.server_count.setText(f'{len(self._servers)} 项')
        self._filter_servers(self.server_filter.text())
        if reselect >= 0:
            table.selectRow(reselect)
        else:
            self._show_server(None)

    def _filter_servers(self, text):
        needle = text.strip().lower()
        for row, record in enumerate(self._servers):
            spec = record.get('spec', {})
            haystack = f"{spec.get('name', '')} {spec.get('transport', '')} {spec.get('command', '')} {record['id']}".lower()
            self.servers_table.setRowHidden(row, bool(needle) and needle not in haystack)

    def _fill_dashboard_servers(self):
        while self.dashboard_servers.count():
            item = self.dashboard_servers.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not self._servers:
            self.dashboard_servers.addWidget(label('暂无已登记服务。', 'muted'))
            return
        for record in self._servers[:6]:
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            name = label(str(record.get('spec', {}).get('name', record['id'])))
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            line.addWidget(name, 1)
            enabled = bool(record.get('enabled'))
            line.addWidget(pill('已启用' if enabled else '已禁用', 'success' if enabled else 'neutral'))
            self.dashboard_servers.addWidget(row)
        if len(self._servers) > 6:
            self.dashboard_servers.addWidget(label(f'另有 {len(self._servers) - 6} 项…', 'muted'))

    def _fill_clients(self, clients):
        items = clients.get('clients', []) if isinstance(clients, dict) else []
        table = self.clients_table
        table.blockSignals(True)
        table.clearContents()
        table.setRowCount(len(items))
        for row, item in enumerate(items):
            table.setItem(row, 0, QTableWidgetItem(str(item.get('client', ''))))
            table.setItem(row, 1, QTableWidgetItem(str(item.get('path', ''))))
            if item.get('error'):
                self._pill_cell(table, row, 2, '解析失败', 'danger')
            elif item.get('exists'):
                self._pill_cell(table, row, 2, '已找到', 'success')
            else:
                self._pill_cell(table, row, 2, '未创建', 'neutral')
            table.setItem(row, 3, QTableWidgetItem(str(len(item.get('servers', []) or []))))
        table.blockSignals(False)
        self._client_rows = items

    def _client_selected(self):
        row = self.clients_table.currentRow()
        items = getattr(self, '_client_rows', [])
        if 0 <= row < len(items):
            self.client_name.setText(str(items[row].get('client', '')))
            self.client_path.setText(str(items[row].get('path', '')))

    def refresh_status(self):
        try:
            tasks = self.core.list_tasks()
            self.tasks_view.setPlainText(pretty(tasks))
            self._fill_tasks(tasks)
            logs = self.core.get_logs()
            self.logs_view.setPlainText(pretty(logs))
            self._fill_audit(logs)
        except Exception as exc:
            self.report(f'读取任务与审计失败：{exc}')

    def _fill_tasks(self, tasks):
        tasks = tasks if isinstance(tasks, list) else []
        table = self.tasks_table
        table.clearContents()
        table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            task = task if isinstance(task, dict) else {'operation': str(task)}
            table.setItem(row, 0, QTableWidgetItem(str(task.get('operation', '—'))))
            status = str(task.get('status', '—'))
            self._pill_cell(table, row, 1, status, STATUS_TONES.get(status, 'neutral'))
            table.setItem(row, 2, QTableWidgetItem(short_time(task.get('created_at'))))
            table.setItem(row, 3, QTableWidgetItem(str(task.get('id', ''))))

    def _fill_audit(self, logs):
        records = logs.get('audit', []) if isinstance(logs, dict) else []
        table = self.audit_table
        table.clearContents()
        table.setRowCount(len(records))
        for row, record in enumerate(records):
            record = record if isinstance(record, dict) else {'detail': record}
            table.setItem(row, 0, QTableWidgetItem(short_time(record.get('at'))))
            action = QTableWidgetItem(str(record.get('action', '—')))
            font = action.font()
            font.setWeight(QFont.Weight.DemiBold)
            action.setFont(font)
            table.setItem(row, 1, action)
            detail = json.dumps(record.get('detail', {}), ensure_ascii=False, default=str)
            item = QTableWidgetItem(detail if len(detail) <= 160 else detail[:157] + '…')
            item.setToolTip(detail[:2000])
            table.setItem(row, 2, item)

    def _server_selected(self):
        row = self.servers_table.currentRow()
        if 0 <= row < len(self._servers):
            self._show_server(self._servers[row])

    def _show_server(self, record):
        while self.detail_pills.count() > 1:
            item = self.detail_pills.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.tools_list.clear()
        if record is None:
            self.detail_title.setText('未选择服务')
            self.detail_sub.setText('从左侧列表选择服务查看详情')
            self.server_details.setPlainText('')
            return
        spec = record.get('spec', {})
        validation = record.get('validation', {}) or {}
        self.detail_title.setText(str(spec.get('name', record['id'])))
        command = ' '.join(str(x) for x in [spec.get('command') or spec.get('url') or '', *(spec.get('args') or [])]).strip()
        version = f" · v{spec['version']}" if spec.get('version') else ''
        self.detail_sub.setText((command or record['id']) + version)
        self.detail_sub.setToolTip(command)
        enabled = bool(record.get('enabled'))
        badges = [(spec.get('transport') or '—', 'accent'), ('已启用' if enabled else '已禁用', 'success' if enabled else 'neutral'),
                  ('托管' if record.get('managed') else '外部', 'info'), (f"验证 · {validation.get('level', '未验证')}", 'neutral')]
        for index, (text, tone) in enumerate(badges):
            self.detail_pills.insertWidget(index, pill(str(text), tone))
        tools = validation.get('tools') or []
        for tool in tools:
            if isinstance(tool, dict):
                description = tool.get('description') or ''
                item = QListWidgetItem(f"{tool.get('name', '工具')}\n{description}" if description else str(tool.get('name', '工具')))
            else:
                item = QListWidgetItem(str(tool))
            item.setIcon(icons.icon('wrench', self._palette['accent'], size=16))
            self.tools_list.addItem(item)
        if not tools:
            placeholder = QListWidgetItem('尚无工具记录。运行“连接测试”后会显示工具列表。')
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tools_list.addItem(placeholder)
        self.server_details.setPlainText(pretty(record))

    def server_action(self, action):
        row = self.servers_table.currentRow()
        if not 0 <= row < len(self._servers):
            self.report('请先选择一个服务。')
            return
        sid = self._servers[row]['id']
        if action == 'test':
            self.call_background('plan_connection', sid, callback=self.review_plan)
        elif action == 'update':
            spec = self._servers[row].get('spec', {})
            request = {'kind': spec.get('installation_type', 'python'), 'package': spec.get('package'), 'name': spec.get('name'), 'version': spec.get('version')}
            text, accepted = QInputDialog.getMultiLineText(self, '更新目标参数', '填写固定目标版本及来源参数（Core 会校验并生成计划）：', pretty(request))
            if not accepted:
                return
            try:
                request = json.loads(text)
                if not isinstance(request, dict):
                    raise ValueError('更新参数必须是 JSON 对象')
            except ValueError as exc:
                self.report(f'更新参数错误：{exc}')
                return
            self.call_background('plan_action', action, sid, request=request, callback=self.review_plan)
        else:
            self.call_background('plan_action', action, sid, callback=self.review_plan)

    def review_plan(self, plan):
        if not isinstance(plan, dict) or not plan.get('id'):
            self.report(f'Core 未返回可批准计划：{pretty(plan)}')
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle('批准执行计划')
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(f'批准执行 {plan.get("action", "变更")}？')
        payload = plan.get('payload') if isinstance(plan.get('payload'), dict) else {}
        summary = [f'{key}：{payload[key]}' for key in ('server_id', 'package', 'version', 'roots') if payload.get(key)]
        dialog.setInformativeText('\n'.join(summary + ['', '请展开详情核对目标、命令、权限与变更内容。批准后将由 Core 执行该一次性计划。']).strip())
        dialog.setDetailedText(pretty(plan))
        dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.button(QMessageBox.StandardButton.Yes).setText('批准并执行')
        dialog.button(QMessageBox.StandardButton.Cancel).setText('取消')
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            self.report('已取消计划，未批准或执行。')
            return
        try:
            self.core.approve_plan(plan['id'])
        except Exception as exc:
            self.report(f'批准失败：{exc}')
            return
        self.call_background('execute_plan', plan['id'], callback=lambda result: (self.report(pretty(result)), self.refresh()))

    def choose_scan_root(self):
        path = QFileDialog.getExistingDirectory(self, '选择扫描根目录')
        if path:
            self.scan_roots.setText(path)

    def scan(self):
        roots = [self.scan_roots.text().strip()] if self.scan_roots.text().strip() else None
        mode = self.scan_mode.currentText()
        if mode == 'deep':
            self.call_background('plan_scan', roots=roots, max_depth=8, max_files=50000, max_seconds=60, callback=self.review_plan)
        else:
            def show(result):
                self.discovery_results.setPlainText(pretty(result))
                self.discover_tabs.setCurrentIndex(1)
                self.refresh()
            self.call_background('discover', roots=roots, mode=mode, callback=show)

    def search_registry(self):
        def show(result):
            self.discovery_results.setPlainText(pretty(result))
            self._fill_registry(result)
            self.discover_tabs.setCurrentIndex(0)
        self.call_background('search_registry', self.registry_query.text().strip(), callback=show)

    def _fill_registry(self, result):
        self.registry_list.clear()
        entries = result.get('servers', []) if isinstance(result, dict) else []
        for entry in entries:
            server = entry.get('server', entry) if isinstance(entry, dict) else {}
            packages = [p for p in server.get('packages', []) or [] if isinstance(p, dict)]
            package = packages[0] if packages else {}
            kind = REGISTRY_KINDS.get(str(package.get('registryType') or package.get('registry_name') or '').lower())
            card = QWidget()
            row = QHBoxLayout(card)
            row.setContentsMargins(6, 10, 6, 10)
            row.setSpacing(12)
            row.addWidget(self._icon_label('package', 'accent', 16, box=34), 0, Qt.AlignmentFlag.AlignTop)
            text = QVBoxLayout()
            text.setSpacing(3)
            title_row = QHBoxLayout()
            title_row.setSpacing(6)
            title = label(str(server.get('name', '未命名')), 'stepTitle')
            title_row.addWidget(title)
            if server.get('version'):
                title_row.addWidget(pill(f"v{server['version']}", 'neutral'))
            if entry.get('installed'):
                title_row.addWidget(pill('已安装', 'success'))
            if kind:
                title_row.addWidget(pill(kind, 'accent'))
            title_row.addStretch()
            text.addLayout(title_row)
            if server.get('description'):
                text.addWidget(label(str(server['description']), 'muted', wrap=True))
            if package.get('identifier'):
                text.addWidget(label(f"{package.get('identifier')} {package.get('version', '')}".strip(), 'muted'))
            row.addLayout(text, 1)
            if kind and package.get('identifier'):
                use = self._button(None, '填入安装表单', lambda checked=False, k=kind, p=package, s=server: self._prefill_install(k, p, s), icon='arrow-right')
                row.addWidget(use, 0, Qt.AlignmentFlag.AlignVCenter)
            item = QListWidgetItem()
            item.setSizeHint(card.sizeHint())
            self.registry_list.addItem(item)
            self.registry_list.setItemWidget(item, card)
        self.registry_stack.setCurrentIndex(1 if entries else 0)
        self._apply_tints()

    def _prefill_install(self, kind, package, server):
        self.install_kind.setCurrentText(kind)
        self.install_package.setText(str(package.get('identifier', '')))
        self.install_version.setText(str(package.get('version') or server.get('version') or ''))
        self.install_name.setText(str(server.get('name', '')).split('/')[-1])
        self.navigation.setCurrentRow(3)
        self.report(f"已填入安装表单：{package.get('identifier')}")

    def install(self):
        package = self.install_package.text().strip()
        if not package:
            self.report('请填写安装来源。')
            return
        try:
            request = json.loads(self.install_options.toPlainText() or '{}')
            if not isinstance(request, dict):
                raise ValueError('高级参数必须是 JSON 对象')
        except ValueError as exc:
            self.report(f'安装参数错误：{exc}')
            return
        request.update(kind=self.install_kind.currentText(), package=package, name=self.install_name.text().strip() or package)
        if self.install_version.text().strip():
            request['version'] = self.install_version.text().strip()
        if request['kind'] == 'git':
            request.setdefault('repository', package)
        self.call_background('plan_install', request, callback=self.review_plan)

    def client_change(self, remove):
        values = [self.client_name.text().strip(), self.client_path.text().strip(), self.client_entry.text().strip()]
        if not all(values) or (not remove and not self.client_server.currentData()):
            self.report('请填写客户端、配置路径、条目名称并选择服务。')
            return
        self.call_background('plan_client_change', *values, server_id=self.client_server.currentData(), remove=remove, callback=self.review_plan)

    def extension_action(self, operation):
        service = getattr(self.core, 'extensions', None)
        method = getattr(service, operation, None)
        if not callable(method):
            self.report(f'当前 Core 不支持扩展操作：{operation}')
            return
        try:
            parameters = json.loads(self.extension_parameters.toPlainText() or '{}')
            if not isinstance(parameters, dict):
                raise ValueError('参数必须是 JSON 对象')
        except ValueError as exc:
            self.report(f'扩展参数错误：{exc}')
            return
        def display(result):
            self.extension_results.setPlainText(pretty(result))
            if isinstance(result, dict) and result.get('state') == 'pending' and result.get('id'):
                self.review_plan(result)
        self.run_background(f'扩展 {operation}', lambda: method(**parameters), display)

    def save_settings(self):
        values = {'theme': self.theme.currentData(), 'scan_roots': [line.strip() for line in self.settings_roots.toPlainText().splitlines() if line.strip()], 'mirror': self.mirror_url.text().strip(), 'registry_url': self.registry_url.text().strip(), 'agent_permission': self.agent_policy.currentData(), 'extension_sandbox_image': self.sandbox_image.text().strip()}
        self.call_background('save_settings', values, callback=lambda result: self.report('设置已保存。'))

    def export_configuration(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出脱敏配置', 'mcp-configuration.json', 'JSON (*.json)')
        if path:
            def save(bundle):
                Path(path).write_text(pretty(bundle), encoding='utf-8')
                self.report(f'配置已导出：{path}')
            self.call_background('export_configuration', callback=save)

    def import_configuration(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入配置候选', '', 'JSON (*.json)')
        if path:
            try:
                bundle = json.loads(Path(path).read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                self.report(f'导入失败：{exc}')
                return
            self.call_background('import_configuration', bundle, callback=lambda result: self.report(pretty(result)))

    # ------------------------------------------------------------------ theme
    def _role_color(self, role):
        p = self._palette
        special = {'on_accent': '#ffffff', 'white_soft': '#e0e7ff', 'accent_sidebar': p['accent'] if self._theme_applied == 'dark' else '#a5b4fc'}
        if role in special:
            return special[role]
        if role in ('accent', 'success', 'warning', 'danger', 'info'):
            return p['accent_text'] if role == 'accent' and self._theme_applied == 'dark' else p[role]
        return p.get(role, p['text'])

    def _apply_tints(self):
        alive = []
        for widget, name, role, size, kind in self._tints:
            try:
                color = self._role_color(role)
                if kind == 'label':
                    widget.setPixmap(icons.pixmap(name, color, size))
                else:
                    widget.setIcon(icons.icon(name, color, size=size))
                    if kind == 'button':
                        widget.setIconSize(QSize(size, size))
            except RuntimeError:
                continue  # widget already deleted (e.g. rebuilt registry rows)
            alive.append((widget, name, role, size, kind))
        self._tints = alive

    def apply_theme(self, theme):
        theme = 'dark' if theme == 'dark' else 'light'
        if theme == self._theme_applied:
            return
        self._theme_applied = theme
        self._palette = p = palette(theme)
        self.setStyleSheet(stylesheet(p))
        for index, name in enumerate(self.PAGE_ICONS):
            self.navigation.item(index).setIcon(icons.icon(name, p['sidebar_muted'] if theme == 'dark' else '#94a3b8', '#ffffff'))
        self.theme_toggle.setIcon(icons.icon('sun' if theme == 'dark' else 'moon', p['text'], size=16))
        self._refresh_drawer_icon()
        self._apply_tints()
        if self._servers:
            self._server_selected()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive(self.width())

    def _apply_responsive(self, width):
        compact = width < 1060
        if compact != self._compact:
            self._compact = compact
            self.sidebar.setFixedWidth(72 if compact else 232)
            for widget in (self.brand_text, self.nav_section, self.side_footer):
                widget.setVisible(not compact)
            for index, name in enumerate(self.PAGE_NAMES):
                item = self.navigation.item(index)
                item.setText('' if compact else name)
                item.setToolTip(name if compact else '')
        narrow = width < 1180
        for column in (1, 3):  # transport / management are secondary on narrow windows
            self.servers_table.setColumnHidden(column, narrow)

    def closeEvent(self, event):
        if self._jobs:
            self.report('后台任务仍在运行；完成后可关闭窗口。')
            event.ignore()
        else:
            self.timer.stop()
            super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description='MCP Manager 原生桌面工作台')
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--smoke-test', action='store_true')
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args(argv)
    from mcp_manager.core.manager import ManagerCore
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('MCP Manager')
    app.setFont(QFont('Noto Sans CJK SC', 10))
    core = ManagerCore(data_dir=args.data_dir)
    window = MainWindow(core)
    window.show()
    if args.smoke_test or args.screenshot:
        def finish():
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                if not window.grab().save(str(args.screenshot)):
                    app.exit(1)
                    return
            window.close()
            app.quit()
        QTimer.singleShot(350, finish)
    result = app.exec()
    # Dispose Qt children while QApplication is still alive (including offscreen).
    import shiboken6
    shiboken6.delete(window)
    core.close()
    return result


if __name__ == '__main__':
    raise SystemExit(main())
