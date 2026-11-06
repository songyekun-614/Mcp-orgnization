import json
from pathlib import Path
import pytest


def test_identity_deduplicates_source_not_name(tmp_path):
    from mcp_manager.models import ServerSpec
    a=ServerSpec(name='one',command='python',args=['server.py'],cwd=str(tmp_path))
    b=ServerSpec(name='two',command='python',args=['server.py'],cwd=str(tmp_path))
    c=ServerSpec(name='one',command='python',args=['other.py'],cwd=str(tmp_path))
    assert a.identity()==b.identity()
    assert a.identity()!=c.identity()


def test_database_persists_validation_and_redacts_secrets(tmp_path):
    from mcp_manager.core.database import Database
    from mcp_manager.models import ServerSpec
    db=Database(tmp_path/'registry.sqlite')
    spec=ServerSpec(name='test',command='python',env={'API_TOKEN':'DO_NOT_PERSIST','MODE':'local'})
    server=db.upsert_server(spec,{'kind':'test'})
    assert 'DO_NOT_PERSIST' not in json.dumps(server)
    db.record_validation(server['id'],{'level':'tools_discovered','tool_count':2,'tools':[{'name':'ping'}]})
    reopened=Database(tmp_path/'registry.sqlite')
    assert reopened.get_server(server['id'])['validation']['level']=='tools_discovered'
    assert reopened.list_servers()[0]['spec']['env']['MODE']=='local'
    assert b'DO_NOT_PERSIST' not in (tmp_path/'registry.sqlite').read_bytes()


def test_plan_requires_local_single_use_approval_and_unchanged_payload(tmp_path):
    from mcp_manager.core.database import Database
    from mcp_manager.core.security import ApprovalStore
    db=Database(tmp_path/'db.sqlite');a=ApprovalStore(db)
    plan=a.create('install',{'package':'example','version':'1.0'})
    with pytest.raises(PermissionError):a.consume(plan['id'],'install')
    a.approve_local(plan['id'])
    result=a.consume(plan['id'],'install')
    assert result['payload']['version']=='1.0'
    with pytest.raises(PermissionError):a.consume(plan['id'],'install')


def test_platform_paths_use_selected_home_and_xdg(tmp_path):
    from mcp_manager.adapters.platform import PlatformDetector
    detected=PlatformDetector(home=tmp_path,environ={'XDG_DATA_HOME':str(tmp_path/'custom-data')},system='Linux').detect()
    assert detected['paths']['data']==str(tmp_path/'custom-data/mcp-manager')
    win=PlatformDetector(home=tmp_path,environ={'LOCALAPPDATA':str(tmp_path/'appdata')},system='Windows').detect()
    assert win['paths']['data']==str(tmp_path/'appdata/MCPManager')
    assert win['environment_kind']=='native'


def test_reference_names_survive_persistence_and_spec_updates_refresh_identity(tmp_path):
    from mcp_manager.core.database import Database
    from mcp_manager.models import ServerSpec
    db=Database(tmp_path/'db.sqlite')
    s=db.upsert_server(ServerSpec(name='one',command='first'))
    updated=ServerSpec(name='one',command='second',env_refs={'API_TOKEN':'MY_TOKEN'})
    db.update_server(s['id'],{'spec':updated.model_dump()})
    assert db.get_server(s['id'])['spec']['env_refs']['API_TOKEN']=='MY_TOKEN'
    assert db.upsert_server(updated)['id']==s['id']


def test_schema_fields_preserve_structure_while_credentials_are_redacted():
    from mcp_manager.core.security import redact
    r=redact({'inputSchema':{'type':'object','properties':{'password':{'type':'string'}}},'env':{'API_KEY':'private'}})
    assert r['inputSchema']['properties']['password']['type']=='string'
    assert r['env']['API_KEY']=='<redacted>'


def test_credentials_must_not_be_embedded_in_launch_arguments_or_endpoint():
    from mcp_manager.models import ServerSpec
    for values in [dict(name='bad',command='server',args=['--token=private']),dict(name='bad',command='server',args=['--password','private']),dict(name='bad',transport='streamable-http',url='https://example.com/mcp?api_key=private')]:
        with pytest.raises(ValueError):ServerSpec(**values)


def test_database_backup_and_restore_preserve_records(tmp_path):
    from mcp_manager.core.database import Database
    from mcp_manager.models import ServerSpec
    db=Database(tmp_path/'active.sqlite');db.upsert_server(ServerSpec(name='first',command='first'))
    db.backup(tmp_path/'backup.sqlite');db.upsert_server(ServerSpec(name='second',command='second'))
    db.restore_from(tmp_path/'backup.sqlite')
    assert [r['spec']['name'] for r in db.list_servers()]==['first']
