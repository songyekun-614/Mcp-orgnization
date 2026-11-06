import json
from pathlib import Path
import sys
import pytest
from mcp_manager.models import ServerSpec


def test_discovery_does_not_execute_unknown_project(tmp_path):
    from mcp_manager.core.manager import ManagerCore
    home=tmp_path/'home';config=home/'.codex/config.toml';config.parent.mkdir(parents=True)
    marker=tmp_path/'executed'
    config.write_text('[mcp_servers.unknown]\ncommand="python"\nargs=["-c","open(\\"'+str(marker)+'\\",\\"w\\").write(\\"bad\\")"]\n')
    core=ManagerCore(tmp_path/'data',home=home,environ={})
    result=core.discover();assert len(result['registered'])==1
    assert not marker.exists()
    record=core.list_servers()[0]
    plan=core.plan_connection(record['id'])
    with pytest.raises(PermissionError):core.execute_plan(plan['id'])
    assert not marker.exists()


def test_core_approved_connection_real_tools_and_facts(tmp_path):
    from mcp_manager.core.manager import ManagerCore
    path=tmp_path/'test_server.py';path.write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("real-test")\n@m.tool()\ndef ping()->str:return "pong"\nif __name__=="__main__":m.run()\n')
    core=ManagerCore(tmp_path/'data',home=tmp_path,environ={})
    server=core.register_server({'name':'local-test','command':sys.executable,'args':[str(path)]})
    plan=core.plan_connection(server['id'])
    core.approve_plan(plan['id']);result=core.execute_plan(plan['id'])
    assert result['level']=='tools_discovered'
    assert core.get_server(server['id'])['validation']['tool_count']==1
    assert core.dashboard()['registered']==1
    assert core.dashboard()['connected']==0  # The temporary session was closed.


def test_client_plan_never_persists_literal_secret(tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    from mcp_manager.adapters import clients
    key=Fernet.generate_key();monkeypatch.setattr(clients,"_backup_key",lambda:key)
    from mcp_manager.core.manager import ManagerCore
    path=tmp_path/'config.toml';path.write_text('model="keep"\n[mcp_servers.old]\ncommand="old"\n[mcp_servers.old.env]\nAPI_TOKEN="UNIQUE_PRIVATE_SECRET"\n')
    core=ManagerCore(tmp_path/'data',home=tmp_path,environ={})
    server=core.register_server({'name':'new','command':sys.executable,'args':['-V']})
    plan=core.plan_client_change('codex',str(path),'new',server['id'])
    assert 'UNIQUE_PRIVATE_SECRET' not in json.dumps(plan)
    core.approve_plan(plan['id']);core.execute_plan(plan['id'])
    assert 'API_TOKEN="UNIQUE_PRIVATE_SECRET"' in path.read_text()
    assert 'UNIQUE_PRIVATE_SECRET' not in core.db.path.read_bytes().decode(errors='replace')


def test_migration_strips_private_paths_and_relocates_on_import(tmp_path):
    from mcp_manager.core.manager import ManagerCore
    core=ManagerCore(tmp_path/'data',home=tmp_path,environ={})
    core.register_server({'name':'private','command':str(tmp_path/'secret-project/.venv/bin/python'),'cwd':str(tmp_path/'secret-project'),'args':['-m','server'],'env':{'TOKEN':'never-export'}})
    bundle=core.export_configuration();text=json.dumps(bundle)
    assert str(tmp_path) not in text and 'never-export' not in text
    result=core.import_configuration(bundle)
    assert result['installed_automatically'] is False
    assert core.dashboard()['registered']==1


def test_configured_mirror_is_used_in_reviewable_python_plan(tmp_path):
    from mcp_manager.core.manager import ManagerCore
    core=ManagerCore(tmp_path/'data',home=tmp_path,environ={})
    core.save_settings({'mirror':'https://pypi.tuna.tsinghua.edu.cn/simple'})
    plan=core.plan_install({'kind':'python','package':'example-mcp','version':'1.0'})
    assert 'https://pypi.tuna.tsinghua.edu.cn/simple' in plan['payload']['commands'][1]


def test_connection_approval_displays_exact_launch_configuration(tmp_path):
    from mcp_manager.core.manager import ManagerCore
    core=ManagerCore(tmp_path/'data',home=tmp_path,environ={})
    record=core.register_server({'name':'review','command':sys.executable,'args':['-m','example_server']})
    plan=core.plan_connection(record['id'])
    assert plan['payload']['launch']['command']==sys.executable
    assert plan['payload']['launch']['args']==['-m','example_server']
