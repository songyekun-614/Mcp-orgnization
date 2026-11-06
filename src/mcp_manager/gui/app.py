"""Native Qt desktop client. All business operations belong to ManagerCore."""
from __future__ import annotations
import argparse
import json
import sys
import uuid
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow,
    QMessageBox, QInputDialog, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


def pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


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

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.pool = QThreadPool(self)
        self._jobs = {}
        self._servers = []
        self.setWindowTitle('MCP Manager · 服务工作台')
        self.resize(1280, 850)
        self.setMinimumSize(880, 640)
        shell = QWidget()
        self.setCentralWidget(shell)
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(210)
        nav_layout = QVBoxLayout(sidebar)
        nav_layout.setContentsMargins(18, 26, 18, 20)
        brand = QLabel('MCP Manager')
        brand.setObjectName('brand')
        nav_layout.addWidget(brand)
        subtitle = QLabel('本地服务工作台')
        subtitle.setObjectName('muted')
        nav_layout.addWidget(subtitle)
        nav_layout.addSpacing(30)
        self.navigation = QListWidget()
        self.navigation.setAccessibleName('主导航')
        self.navigation.addItems(self.PAGE_NAMES)
        nav_layout.addWidget(self.navigation)
        footer = QLabel('V2.0  ·  本地管理\n执行前预览并批准变更')
        footer.setObjectName('muted')
        nav_layout.addWidget(footer)
        outer.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(30, 24, 30, 18)
        top = QHBoxLayout()
        self.page_title = QLabel()
        self.page_title.setObjectName('pageTitle')
        top.addWidget(self.page_title)
        top.addStretch()
        self.busy_label = QLabel('本地工作区')
        self.busy_label.setObjectName('muted')
        top.addWidget(self.busy_label)
        refresh = QPushButton('刷新')
        refresh.clicked.connect(self.refresh)
        top.addWidget(refresh)
        content.addLayout(top)
        self.pages = QStackedWidget()
        content.addWidget(self.pages, 1)
        self.activity = QPlainTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setAccessibleName('操作状态与错误')
        self.activity.setPlaceholderText('操作记录、错误与授权结果会显示在这里。')
        self.activity.setMaximumHeight(105)
        content.addWidget(self.activity)
        outer.addLayout(content, 1)
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

    def _page(self, description):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)
        label = QLabel(description)
        label.setWordWrap(True)
        label.setObjectName('muted')
        layout.addWidget(label)
        self.pages.addWidget(page)
        return layout

    def _button(self, layout, text, callback, primary=False):
        button = QPushButton(text)
        if primary:
            button.setObjectName('primary')
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _editor(self, placeholder='', readonly=True):
        editor = QPlainTextEdit()
        editor.setReadOnly(readonly)
        editor.setPlaceholderText(placeholder)
        return editor

    def _build_dashboard(self):
        layout = self._page('集中查看服务、工具与最近任务。所有指标均来自本地 Core 的实际记录。')
        grid = QGridLayout()
        self.metric_labels = {}
        for index, (key, title) in enumerate([('registered', '已登记服务'), ('connected', '当前连接'), ('tools', '可用工具'), ('updates', '可用更新')]):
            card = QFrame()
            card.setObjectName('card')
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(22, 20, 22, 20)
            card_layout.addWidget(QLabel(title))
            value = QLabel('—')
            value.setObjectName('metric')
            self.metric_labels[key] = value
            card_layout.addWidget(value)
            grid.addWidget(card, 0, index)
        layout.addLayout(grid)
        actions = QHBoxLayout()
        self._button(actions, '发现本地服务', lambda: self.navigation.setCurrentRow(2), True)
        self._button(actions, '查看已安装', lambda: self.navigation.setCurrentRow(1))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addWidget(QLabel('最近活动'))
        self.dashboard_recent = self._editor('当前没有任务或错误记录。')
        layout.addWidget(self.dashboard_recent, 1)

    def _build_installed(self):
        layout = self._page('查看登记来源、验证等级与工具详情；连接测试也会先展示执行计划。')
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.servers_table = QTableWidget(0, 5)
        self.servers_table.setHorizontalHeaderLabels(['服务', '传输', '启用', '管理方式', '验证等级'])
        self.servers_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.servers_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.servers_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.servers_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.servers_table.itemSelectionChanged.connect(self._server_selected)
        splitter.addWidget(self.servers_table)
        self.server_details = self._editor('选择服务查看工具、来源和验证详情。')
        splitter.addWidget(self.server_details)
        layout.addWidget(splitter, 1)
        actions = QGridLayout()
        for index, (title, action) in enumerate([('连接测试', 'test'), ('启用', 'enable'), ('禁用', 'disable'), ('启动', 'start'), ('停止', 'stop'), ('更新', 'update'), ('回滚', 'rollback'), ('卸载', 'uninstall')]):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, a=action: self.server_action(a))
            actions.addWidget(button, index // 4, index % 4)
        layout.addLayout(actions)

    def _build_discover(self):
        layout = self._page('静态扫描不会执行候选项目。深度扫描需要独立批准；Registry 搜索在后台运行。')
        row = QHBoxLayout()
        self.scan_mode = QComboBox()
        self.scan_mode.addItems(['quick', 'standard', 'deep'])
        self.scan_mode.setAccessibleName('扫描模式')
        row.addWidget(self.scan_mode)
        self.scan_roots = QLineEdit()
        self.scan_roots.setPlaceholderText('扫描目录（多个目录以换行保存在设置中；此处选择一个目录）')
        row.addWidget(self.scan_roots, 1)
        self._button(row, '选择目录', self.choose_scan_root)
        self._button(row, '开始扫描', self.scan, True)
        layout.addLayout(row)
        search = QHBoxLayout()
        self.registry_query = QLineEdit()
        self.registry_query.setPlaceholderText('搜索官方 MCP Registry')
        self.registry_query.returnPressed.connect(self.search_registry)
        search.addWidget(self.registry_query, 1)
        self._button(search, '搜索 Registry', self.search_registry)
        layout.addLayout(search)
        self.discovery_results = self._editor('扫描候选和 Registry 搜索结果将显示在这里。')
        layout.addWidget(self.discovery_results, 1)

    def _build_installation(self):
        layout = self._page('安装请求交给 Core 生成计划；批准后执行。依赖、目标位置与权限以预览为准。')
        form = QFormLayout()
        self.install_kind = QComboBox()
        self.install_kind.addItems(['python', 'node', 'docker', 'git'])
        self.install_package = QLineEdit()
        self.install_package.setPlaceholderText('包名 / 镜像 / Git URL')
        self.install_name = QLineEdit()
        self.install_version = QLineEdit()
        self.install_version.setPlaceholderText('Python / Node 需要固定版本，例如 1.2.3')
        self.install_options = self._editor('高级 JSON 参数，例如 {"module":"my_mcp"}；Git 需要 repository、commit', False)
        self.install_options.setMaximumHeight(88)
        form.addRow('安装类型', self.install_kind)
        form.addRow('来源', self.install_package)
        form.addRow('服务名称', self.install_name)
        form.addRow('固定版本', self.install_version)
        form.addRow('高级参数（JSON）', self.install_options)
        layout.addLayout(form)
        self._button(layout, '生成安装计划', self.install, True)
        layout.addWidget(QLabel('任务与执行日志'))
        self.tasks_view = self._editor('暂无任务。')
        layout.addWidget(self.tasks_view, 1)

    def _build_clients(self):
        layout = self._page('配置路径由平台适配器提供。先查看变更计划，批准后执行带备份与哈希校验的写入。')
        self.clients_view = self._editor()
        layout.addWidget(self.clients_view, 1)
        form = QFormLayout()
        self.client_name = QLineEdit()
        self.client_name.setPlaceholderText('例如 codex、dsh')
        self.client_path = QLineEdit()
        self.client_entry = QLineEdit()
        self.client_server = QComboBox()
        form.addRow('客户端标识', self.client_name)
        form.addRow('配置文件路径', self.client_path)
        form.addRow('配置条目名称', self.client_entry)
        form.addRow('服务', self.client_server)
        layout.addLayout(form)
        row = QHBoxLayout()
        self._button(row, '预览添加 / 更新', lambda: self.client_change(False), True)
        self._button(row, '预览移除条目', lambda: self.client_change(True))
        layout.addLayout(row)

    def _build_extensions(self):
        layout = self._page('扩展操作由 Core 的隔离与授权机制执行。当前 Core 未提供的能力会明确报告不支持。')
        form = QFormLayout()
        self.extension_parameters = self._editor('{"name": "扩展名"}', False)
        self.extension_parameters.setMaximumHeight(160)
        form.addRow('操作参数（JSON 对象）', self.extension_parameters)
        layout.addLayout(form)
        row = QGridLayout()
        for i, (title, operation) in enumerate([('创建工作区', 'workspace'), ('提交扩展', 'submit'), ('静态验证', 'validate'), ('隔离测试', 'test'), ('生成安装计划', 'plan_install'), ('启用计划', 'enable'), ('禁用计划', 'disable'), ('回滚计划', 'rollback')]):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, op=operation: self.extension_action(op))
            row.addWidget(button, i // 4, i % 4)
        layout.addLayout(row)
        self.extension_results = self._editor('尚未运行扩展操作。')
        layout.addWidget(self.extension_results, 1)

    def _build_monitoring(self):
        layout = self._page('查看 Core 脱敏审计与任务日志。错误会保留在此，便于检查真实执行结果。')
        self.logs_view = self._editor('暂无审计记录。')
        layout.addWidget(self.logs_view, 1)
        self._button(layout, '刷新审计日志', self.refresh_status)

    def _build_settings(self):
        layout = self._page('非敏感偏好保存在本地。镜像参数由 Core 校验；导入配置仅生成候选，不自动安装。')
        form = QFormLayout()
        self.theme = QComboBox()
        self.theme.addItem('浅色', 'light')
        self.theme.addItem('深色', 'dark')
        self.theme.currentIndexChanged.connect(lambda: self.apply_theme(self.theme.currentData()))
        self.settings_roots = self._editor('每行一个扫描根目录', False)
        self.settings_roots.setMaximumHeight(130)
        self.mirror_url = QLineEdit()
        self.registry_url = QLineEdit()
        self.sandbox_image = QLineEdit()
        self.sandbox_image.setPlaceholderText('可选：本机已有镜像的 sha256:…，不自动下载')
        self.agent_policy = QComboBox()
        self.agent_policy.addItem('只读与生成计划', 'read_only')
        self.agent_policy.addItem('可执行本地已批准计划', 'approved_only')
        form.addRow('界面主题', self.theme)
        form.addRow('默认扫描根', self.settings_roots)
        form.addRow('Python 镜像 HTTPS URL', self.mirror_url)
        form.addRow('Registry HTTPS URL', self.registry_url)
        form.addRow('Agent 执行权限', self.agent_policy)
        form.addRow('扩展隔离镜像', self.sandbox_image)
        layout.addLayout(form)
        self._button(layout, '保存设置', self.save_settings, True)
        row = QHBoxLayout()
        self._button(row, '导出脱敏配置', self.export_configuration)
        self._button(row, '导入配置候选', self.import_configuration)
        layout.addLayout(row)
        layout.addStretch()

    @Slot(int)
    def _navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.page_title.setText(self.PAGE_NAMES[index])

    def report(self, message):
        self.activity.appendPlainText(str(message))

    def run_background(self, label, operation, callback=None):
        token = uuid.uuid4().hex
        worker = Worker(token, operation)
        self._jobs[token] = (label, callback, worker)
        worker.signals.completed.connect(self._completed)
        self.busy_label.setText(f'运行中 · {len(self._jobs)} 项任务')
        self.report(f'开始：{label}')
        self.pool.start(worker)

    @Slot(str, object, str)
    def _completed(self, token, result, error):
        label, callback, _worker = self._jobs.pop(token)
        self.busy_label.setText(f'运行中 · {len(self._jobs)} 项任务' if self._jobs else '本地工作区')
        if error:
            self.report(f'{label}失败：{error}')
        else:
            self.report(f'{label}完成')
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
            for key, label in self.metric_labels.items():
                label.setText(str(metrics.get(key, '—')))
            recent_tasks, recent_errors = metrics.get('recent_tasks', []), metrics.get('recent_errors', [])
            self.dashboard_recent.setPlainText(('最近任务\n' + pretty(recent_tasks) if recent_tasks else '暂无任务记录。可以先发现本地服务，或在安装中心创建计划。') + '\n\n' + ('最近错误\n' + pretty(recent_errors) if recent_errors else '暂无错误记录。'))
            self._servers = self.core.list_servers()
            self.servers_table.setRowCount(len(self._servers))
            self.client_server.clear()
            for row, record in enumerate(self._servers):
                spec = record.get('spec', {})
                values = [spec.get('name', record['id']), spec.get('transport', ''), '已启用' if record.get('enabled') else '已禁用', '托管' if record.get('managed') else '外部', record.get('validation', {}).get('level', '未验证')]
                for col, value in enumerate(values):
                    self.servers_table.setItem(row, col, QTableWidgetItem(str(value)))
                self.client_server.addItem(spec.get('name', record['id']), record['id'])
            self.clients_view.setPlainText(pretty(self.core.list_clients()))
            settings = self.core.get_settings()
            self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get('theme', 'light'))))
            self.settings_roots.setPlainText('\n'.join(settings.get('scan_roots', [])))
            self.mirror_url.setText(settings.get('mirror', 'https://pypi.org/simple'))
            self.registry_url.setText(settings.get('registry_url', 'https://registry.modelcontextprotocol.io'))
            self.agent_policy.setCurrentIndex(max(0,self.agent_policy.findData(settings.get('agent_permission','read_only'))))
            self.sandbox_image.setText(settings.get('extension_sandbox_image','') or '')
        except Exception as exc:
            self.report(f'刷新失败：{exc}')
        self.refresh_status()

    def refresh_status(self):
        try:
            self.tasks_view.setPlainText(pretty(self.core.list_tasks()))
            self.logs_view.setPlainText(pretty(self.core.get_logs()))
        except Exception as exc:
            self.report(f'读取任务与审计失败：{exc}')

    def _server_selected(self):
        row = self.servers_table.currentRow()
        if 0 <= row < len(self._servers):
            self.server_details.setPlainText(pretty(self._servers[row]))

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
        dialog.setInformativeText('请展开详情核对目标、命令、权限与变更内容。批准后将由 Core 执行该一次性计划。')
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
            self.call_background('discover', roots=roots, mode=mode, callback=lambda result: (self.discovery_results.setPlainText(pretty(result)), self.refresh()))

    def search_registry(self):
        self.call_background('search_registry', self.registry_query.text().strip(), callback=lambda result: self.discovery_results.setPlainText(pretty(result)))

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
        values = {'theme': self.theme.currentData(), 'scan_roots': [line.strip() for line in self.settings_roots.toPlainText().splitlines() if line.strip()], 'mirror': self.mirror_url.text().strip(), 'registry_url': self.registry_url.text().strip(), 'agent_permission':self.agent_policy.currentData(), 'extension_sandbox_image':self.sandbox_image.text().strip()}
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

    def apply_theme(self, theme):
        dark = theme == 'dark'
        bg, panel, text, muted, border = ('#181c23', '#222832', '#edf1f7', '#a5b0c0', '#394250') if dark else ('#f5f6f8', '#ffffff', '#202a38', '#667284', '#e0e5ec')
        self.setStyleSheet(f'''
            QWidget {{ color: {text}; font-size: 14px; }}
            QMainWindow, QStackedWidget {{ background: {bg}; }}
            QFrame#sidebar {{ background: {panel}; border-right: 1px solid {border}; }}
            QLabel#brand {{ font-size: 21px; font-weight: 700; }}
            QLabel#pageTitle {{ font-size: 27px; font-weight: 700; }}
            QLabel#muted {{ color: {muted}; }}
            QFrame#card {{ background: {panel}; border: 1px solid {border}; border-radius: 14px; }}
            QLabel#metric {{ color: #4c89ef; font-size: 38px; font-weight: 600; }}
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ padding: 13px 14px; border-radius: 9px; margin: 3px 0; }}
            QListWidget::item:selected {{ background: {'#263e61' if dark else '#eaf1ff'}; color: {'#a7c8ff' if dark else '#2466ca'}; }}
            QListWidget::item:focus {{ border: 1px solid #4c89ef; }}
            QPushButton {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 9px 16px; min-height: 18px; }}
            QPushButton:hover {{ border-color: #4c89ef; }}
            QPushButton:pressed {{ background: {'#30405b' if dark else '#edf3fc'}; }}
            QPushButton#primary {{ background: #316fd3; color: white; border-color: #316fd3; }}
            QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 2px solid #4c89ef; }}
            QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 8px; selection-background-color: #316fd3; }}
            QHeaderView::section {{ background: {bg}; color: {muted}; border: none; padding: 10px; }}
            QTableWidget {{ gridline-color: {border}; }}
            QSplitter::handle {{ background: {border}; height: 3px; }}
            QMessageBox {{ background: {bg}; }}
        ''')

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
