from pathlib import Path
import os,subprocess,sys


def test_launcher_preserves_selected_virtual_environment(tmp_path):
    script=Path(__file__).resolve().parents[1]/'scripts/install_user_entry.py'
    subprocess.run([sys.executable,str(script),'--python',sys.executable,'--app-dir',str(tmp_path/'app')],check=True,capture_output=True)
    launcher=tmp_path/'app'/('MCP-Manager.cmd' if os.name=='nt' else 'start')
    assert str(Path(sys.executable).absolute()) in launcher.read_text()
    assert 'mcp_manager.gui.app' in launcher.read_text()
