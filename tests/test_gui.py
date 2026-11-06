"""Native Qt tests; the stub is an explicit UI contract, never a backend substitute."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope='module')
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    import shiboken6
    for widget in application.topLevelWidgets():
        if not shiboken6.isValid(widget):
            continue
        if hasattr(widget, 'pool'):
            widget.pool.waitForDone(3000)
        shiboken6.delete(widget)
    application.processEvents()


class ReadOnlyCore:
    def dashboard(self):
        return {'registered': 7, 'connected': 2, 'tools': 11, 'updates': 1, 'recent_errors': [], 'recent_tasks': []}
    def list_servers(self):
        return [{'id': 'test-id', 'spec': {'name': '测试服务', 'command': 'python', 'args': [], 'transport': 'stdio'}, 'enabled': True, 'managed': False, 'validation': {'level': 'static', 'tools': [{'name': 'hello', 'description': '问候'}]}}]
    def list_clients(self):
        return {'clients': []}
    def list_tasks(self):
        return []
    def get_logs(self, server_id=None):
        return {'audit': []}
    def get_settings(self):
        return {'theme': 'light', 'scan_roots': []}


def wait_until(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_eight_pages_and_actual_dashboard_values(app):
    from mcp_manager.gui.app import MainWindow
    window = MainWindow(ReadOnlyCore())
    assert window.navigation.count() == 8
    assert window.pages.count() == 8
    assert window.metric_labels['registered'].text() == '7'
    assert window.servers_table.rowCount() == 1
    window.servers_table.selectRow(0)
    assert 'hello' in window.server_details.toPlainText()
    window.close()


def test_worker_runs_off_ui_thread_and_delivers_result(app):
    from mcp_manager.gui.app import MainWindow
    window = MainWindow(ReadOnlyCore())
    results = []
    gui_thread = threading.get_ident()
    window.run_background('测试', lambda: threading.get_ident(), results.append)
    wait_until(app, lambda: bool(results))
    assert results[0] != gui_thread
    window.close()


def test_cancelled_plan_never_approved_or_executed(app, monkeypatch):
    from mcp_manager.gui.app import MainWindow
    class ApprovalCore(ReadOnlyCore):
        def approve_plan(self, plan_id):
            pytest.fail('cancelled plan was approved')
        def execute_plan(self, plan_id):
            pytest.fail('cancelled plan was executed')
    window = MainWindow(ApprovalCore())
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Cancel)
    window.review_plan({'id': 'p1', 'action': 'uninstall', 'payload': {'server_id': 'test-id'}, 'state': 'pending'})
    assert '取消' in window.activity.toPlainText()
    window.close()


def test_approved_plan_executes_in_background(app, monkeypatch):
    from mcp_manager.gui.app import MainWindow
    events = []
    class ApprovalCore(ReadOnlyCore):
        def approve_plan(self, plan_id):
            events.append(('approve', plan_id))
        def execute_plan(self, plan_id):
            events.append(('execute', plan_id, threading.get_ident()))
            return {'state': 'complete'}
    window = MainWindow(ApprovalCore())
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Yes)
    window.review_plan({'id': 'p2', 'action': 'disable', 'payload': {}, 'state': 'pending'})
    wait_until(app, lambda: len(events) == 2)
    assert events[0] == ('approve', 'p2')
    assert events[1][0:2] == ('execute', 'p2')
    assert events[1][2] != threading.get_ident()
    window.close()


def test_missing_extension_is_visible_error(app):
    from mcp_manager.gui.app import MainWindow
    window = MainWindow(ReadOnlyCore())
    window.extension_action('workspace')
    assert '不支持' in window.activity.toPlainText()
    window.close()


def test_empty_dashboard_uses_readable_empty_state(app):
    from mcp_manager.gui.app import MainWindow
    window = MainWindow(ReadOnlyCore())
    assert '暂无' in window.dashboard_recent.toPlainText()
    window.close()


def test_install_form_preserves_kind_version_and_advanced_parameters(app, monkeypatch):
    from mcp_manager.gui.app import MainWindow
    requests = []
    class InstallContract(ReadOnlyCore):
        def plan_install(self, request):
            requests.append(request)
            return {'id': 'p3', 'action': 'install', 'payload': request}
    window = MainWindow(InstallContract())
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Cancel)
    window.install_kind.setCurrentText('node')
    window.install_package.setText('example-package')
    window.install_version.setText('1.2.3')
    window.install_options.setPlainText('{"executable":"example-cli"}')
    window.install()
    wait_until(app, lambda: bool(requests) and not window._jobs)
    assert requests[0]['kind'] == 'node'
    assert requests[0]['version'] == '1.2.3'
    assert requests[0]['executable'] == 'example-cli'
    window.close()


def test_real_core_settings_and_deep_plan_contract(app, tmp_path, monkeypatch):
    from mcp_manager.core.manager import ManagerCore
    from mcp_manager.gui.app import MainWindow
    core = ManagerCore(data_dir=tmp_path/'data', home=tmp_path, environ={})
    window = MainWindow(core)
    window.mirror_url.setText('https://pypi.org/simple')
    window.save_settings()
    wait_until(app, lambda: not window._jobs)
    assert '设置已保存' in window.activity.toPlainText()
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Cancel)
    window.scan_mode.setCurrentText('deep')
    window.scan_roots.setText(str(tmp_path))
    window.scan()
    wait_until(app, lambda: not window._jobs)
    assert '已取消计划' in window.activity.toPlainText()
    assert '失败' not in window.activity.toPlainText()
    window.close()
    core.close()


def test_update_collects_target_version_before_core_plan(app, monkeypatch):
    from mcp_manager.gui.app import MainWindow
    from PySide6.QtWidgets import QInputDialog
    plans = []
    class UpdateContract(ReadOnlyCore):
        def plan_action(self, action, server_id, **parameters):
            plans.append((action, server_id, parameters))
            return {'id': 'p4', 'action': 'install', 'payload': parameters}
    window = MainWindow(UpdateContract())
    window.servers_table.selectRow(0)
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *a: ('{"kind":"python","package":"example","version":"2.0.0"}', True))
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: QMessageBox.StandardButton.Cancel)
    window.server_action('update')
    wait_until(app, lambda: bool(plans) and not window._jobs)
    assert plans[0][2]['request']['version'] == '2.0.0'
    window.close()
