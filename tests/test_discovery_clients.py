import json
import threading
from pathlib import Path
import pytest


def test_client_contract_exists():
    from mcp_manager.adapters import clients
    assert hasattr(clients, 'ClientConfigAdapter')


def test_codex_roundtrip_and_stale_plan(tmp_path):
    from mcp_manager.adapters.clients import ClientConfigAdapter, restore_backup
    from mcp_manager.models import ServerSpec
    path=tmp_path/'config.toml'
    path.write_text('# keep\nmodel = "model"\n[mcp_servers.old]\ncommand = "python"\nargs = ["old.py"]\n')
    adapter=ClientConfigAdapter('codex',path)
    assert adapter.read()[0].name=='old'
    plan=adapter.plan_change('new',ServerSpec(name='new',command='node',args=['server.js'],env_refs={'API_TOKEN':'API_TOKEN'}))
    assert path.read_text().startswith('# keep')
    result=adapter.apply_plan(plan)
    assert '# keep' in path.read_text() and 'model = "model"' in path.read_text()
    assert adapter.read()[1].env_refs=={'API_TOKEN':'API_TOKEN'}
    with pytest.raises(ValueError):adapter.apply_plan(plan)
    restore_backup(result)
    assert [s.name for s in adapter.read()]==['old']


def test_dsh_nested_js_not_executed_and_secrets_omitted(tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    from mcp_manager.adapters import clients
    key=Fernet.generate_key();monkeypatch.setattr(clients,"_backup_key",lambda:key)
    from mcp_manager.adapters.clients import ClientConfigAdapter
    from mcp_manager.models import ServerSpec
    path=tmp_path/'cordis.patch.yml'
    path.write_text('''# retained\ninsert:\n  - name: '@deepseek-ai/dsh-mcp-client'\n    config:\n      serverName: one\n      transport: stdio\n      command: python\n      env:\n        TOKEN: !!js process.env.TOKEN\n        API_KEY: literal-secret\n  - name: other\n    config:\n      expression: !!js 'throw new Error("never execute")'\n''')
    adapter=ClientConfigAdapter('dsh',path)
    spec=adapter.read()[0]
    assert spec.env=={} and spec.env_refs=={'TOKEN':'TOKEN','API_KEY':'API_KEY'}
    plan=adapter.plan_change('one',ServerSpec(name='one',command='node',env_refs={'TOKEN':'TOKEN'}))
    adapter.apply_plan(plan)
    assert 'throw new Error' in path.read_text() and '# retained' in path.read_text()
    assert 'process.env.TOKEN' in path.read_text()


def test_discovery_static_candidates_limits_and_dedup(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    home=tmp_path/'home'; (home/'.codex').mkdir(parents=True)
    (home/'.codex/config.toml').write_text('[mcp_servers.a]\ncommand="python"\nargs=["server.py"]\n')
    roots=tmp_path/'项目'; roots.mkdir()
    (roots/'mcp-server.json').write_text(json.dumps({'name':'copy','command':'python','args':['server.py']}))
    (roots/'pyproject.toml').write_text('[project]\nname="sample"\ndependencies=["mcp>=1"]\n')
    (roots/'mcp_fake.py').write_text('print("not MCP")')
    (roots/'loop').symlink_to(roots,target_is_directory=True)
    result=DiscoveryEngine(home,environ={}).discover([str(roots)],mode='standard')
    assert len(result['servers'])==1
    assert len(result['servers'][0]['sources'])==2
    assert result['candidates'][0]['level']=='project_present'
    assert all('mcp_fake.py' not in c['path'] for c in result['candidates'])
    event=threading.Event();event.set()
    stopped=DiscoveryEngine(home,environ={}).discover([str(roots)],cancel=event)
    assert stopped['stats']['cancelled']
    with pytest.raises(PermissionError):DiscoveryEngine(home,environ={}).discover(mode='deep')


def test_discovery_overrides_and_invalid_configuration(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    codex=tmp_path/'custom';codex.mkdir()
    (codex/'config.toml').write_text('invalid = [')
    dsh=tmp_path/'dsh';profile=dsh/'profiles/default';profile.mkdir(parents=True)
    (profile/'cordis.yml').write_text('insert:\n- name: "@deepseek-ai/dsh-mcp-client"\n  config: {serverName: demo, command: python, transport: stdio}\n')
    result=DiscoveryEngine(tmp_path,environ={'CODEX_HOME':str(codex),'DSH_HOME':str(dsh)}).discover()
    assert result['servers'][0]['spec']['name']=='demo'
    assert result['issues']


def test_plan_cannot_modify_unrelated_settings(tmp_path):
    from mcp_manager.adapters.clients import ClientConfigAdapter
    from mcp_manager.models import ServerSpec
    path=tmp_path/'config.toml'; path.write_text('model="keep"\n')
    adapter=ClientConfigAdapter('codex',path)
    plan=adapter.plan_change('a',ServerSpec(name='a',command='python'))
    plan['after_text']=plan['after_text'].replace('keep','changed')
    with pytest.raises(ValueError):adapter.apply_plan(plan)
    assert path.read_text()=='model="keep"\n'


def test_dsh_disabled_and_restore_detects_tampering(tmp_path):
    from mcp_manager.adapters.clients import ClientConfigAdapter, restore_backup
    from mcp_manager.models import ServerSpec
    path=tmp_path/'cordis.yml'
    adapter=ClientConfigAdapter('dsh',path)
    record=adapter.apply_plan(adapter.plan_change('disabled',ServerSpec(name='disabled',command='python',enabled=False)))
    assert adapter.read()[0].enabled is False
    path.write_text('insert: []\n')
    with pytest.raises(ValueError):restore_backup(record)


def test_pause_cancellation_and_scan_file_limit(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    paused=threading.Event();paused.set();cancel=threading.Event()
    threading.Timer(.05,cancel.set).start()
    result=DiscoveryEngine(tmp_path,{}).discover([str(tmp_path)],pause=paused,cancel=cancel,max_seconds=1)
    assert result['stats']['cancelled']
    for n in range(10):(tmp_path/f'{n}.txt').write_text('x')
    result=DiscoveryEngine(tmp_path,{}).discover([str(tmp_path)],mode='standard',max_files=3)
    assert result['stats']['files']<=3 and result['stats']['limited']


def test_python_project_launch_requires_existing_local_executable(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    project=tmp_path/'source';project.mkdir()
    (project/'pyproject.toml').write_text('[project]\nname="sample"\ndependencies=["mcp>=1"]\n[project.scripts]\nsample-mcp="sample.server:main"\n')
    (project/'sample').mkdir();(project/'sample/server.py').write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP("sample")\ndef main():m.run()\n')
    scripts=project/'.venv/bin';scripts.mkdir(parents=True)
    executable=scripts/'sample-mcp';executable.write_text('#!/usr/bin/env python\n');executable.chmod(0o700)
    result=DiscoveryEngine(tmp_path,{}).discover([str(project)],mode='standard')
    assert len(result['servers'])==1
    assert result['servers'][0]['spec']['command']==str(executable)
    assert result['servers'][0]['source']['evidence']==['python_mcp_dependency','installed_console_entry_point']


def test_node_sdk_and_existing_bin_provide_static_launch(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    executable=tmp_path/'node';executable.write_text('');executable.chmod(0o700)
    project=tmp_path/'node-project';project.mkdir()
    (project/'package.json').write_text(json.dumps({'name':'sample','dependencies':{'@modelcontextprotocol/sdk':'^1'},'bin':{'sample-mcp':'server.js'}}))
    (project/'server.js').write_text('console.log("never executed")')
    result=DiscoveryEngine(tmp_path,{'PATH':str(tmp_path)}).discover([str(project)],mode='standard')
    assert result['servers'][0]['spec']['args']==[str(project/'server.js')]


def test_invalid_yaml_is_an_issue_not_scan_failure(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    (tmp_path/'.dsh').mkdir()
    (tmp_path/'.dsh/cordis.yml').write_text('insert: [unterminated')
    result=DiscoveryEngine(tmp_path,{}).discover()
    assert result['issues'][0]['code'].startswith('configuration_read_failed')


def test_explicit_symlink_root_is_scanned_but_child_links_are_not(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    actual=tmp_path/'actual';actual.mkdir()
    (actual/'mcp-server.json').write_text(json.dumps({'name':'real','command':'python'}))
    outside=tmp_path/'outside';outside.mkdir()
    (outside/'mcp-server.json').write_text(json.dumps({'name':'not-selected','command':'node'}))
    (actual/'child-link').symlink_to(outside,target_is_directory=True)
    selected=tmp_path/'selected';selected.symlink_to(actual,target_is_directory=True)
    result=DiscoveryEngine(tmp_path,{}).discover([str(selected)],mode='standard')
    assert [s['spec']['name'] for s in result['servers']]==['real']


def test_codex_object_env_vars_remote_is_unresolved(tmp_path):
    from mcp_manager.adapters.clients import ClientConfigAdapter
    path=tmp_path/'config.toml'
    path.write_text('[mcp_servers.local]\ncommand="python"\nenv_vars=[{name="LOCAL_KEY",source="local"}]\n[mcp_servers.remote]\ncommand="node"\nenv_vars=[{name="REMOTE_KEY",source="remote"}]\n')
    adapter=ClientConfigAdapter('codex',path)
    servers=adapter.read()
    assert len(servers)==1 and servers[0].env_refs=={'LOCAL_KEY':'LOCAL_KEY'}
    assert adapter.issues[0]['code']=='unsupported_remote_env_reference'
    assert adapter.issues[0]['server']=='remote'


def test_codex_http_plan_omits_stdio_fields(tmp_path):
    from mcp_manager.adapters.clients import ClientConfigAdapter
    from mcp_manager.models import ServerSpec
    import tomllib
    adapter=ClientConfigAdapter('codex',tmp_path/'config.toml')
    plan=adapter.plan_change('http',ServerSpec(name='http',transport='streamable-http',url='http://localhost:8765/mcp'))
    config=tomllib.loads(plan['after_text'])['mcp_servers']['http']
    assert not {'command','args','cwd','env','env_vars'} & config.keys()


def test_distribution_metadata_and_service_are_static_evidence(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    site=tmp_path/'env/lib/python3.11/site-packages';site.mkdir(parents=True)
    dist=site/'example-1.0.dist-info';dist.mkdir()
    (dist/'METADATA').write_text('Metadata-Version: 2.1\nName: example\nVersion: 1.0\nRequires-Dist: mcp>=1\n')
    (dist/'entry_points.txt').write_text('[console_scripts]\nexample-mcp = example.server:main\n')
    (site/'example').mkdir();(site/'example/server.py').write_text('from mcp.server.fastmcp import FastMCP\nm=FastMCP(\"example\")\ndef main():m.run()\n')
    scripts=tmp_path/'env/bin';scripts.mkdir()
    command=scripts/'example-mcp';command.write_text('#!/usr/bin/env python\n');command.chmod(0o700)
    services=tmp_path/'.config/systemd/user';services.mkdir(parents=True)
    (services/'example.service').write_text('[Service]\nExecStart=/some/path/server --serve\n')
    result=DiscoveryEngine(tmp_path,{},metadata_paths=[str(site)]).discover()
    assert result['servers'][0]['spec']['command']==str(command)
    assert result['servers'][0]['source']['kind']=='python_distribution'
    service=[c for c in result['candidates'] if 'user_service_execstart' in c['evidence']]
    assert service and service[0]['level']=='project_present'


def test_atomic_write_without_fchmod_uses_path_chmod(tmp_path,monkeypatch):
    from mcp_manager.adapters import clients
    from mcp_manager.models import ServerSpec
    monkeypatch.delattr(clients.os,'fchmod')
    adapter=clients.ClientConfigAdapter('codex',tmp_path/'config.toml')
    adapter.apply_plan(adapter.plan_change('portable',ServerSpec(name='portable',command='python')))
    assert adapter.read()[0].name=='portable'


def test_metadata_distribution_count_is_bounded(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    site=tmp_path/'site-packages';site.mkdir()
    for i in range(4):
        dist=site/f'nonmcp{i}-1.dist-info';dist.mkdir()
        (dist/'METADATA').write_text(f'Name: nonmcp{i}\nVersion: 1\n')
        (dist/'entry_points.txt').write_text('[console_scripts]\nlooks-mcp=package:main\n')
    result=DiscoveryEngine(tmp_path,{},metadata_paths=[str(site)],max_distributions=2).discover()
    assert result['stats']['distributions']==2 and result['stats']['limited']
    assert result['servers']==[] and result['candidates']==[]


def test_simulated_windows_platform_paths_use_environment_roots(tmp_path):
    from mcp_manager.adapters.platform import PlatformDetector
    home=tmp_path/'用户';roaming=tmp_path/'roaming';local=tmp_path/'local'
    result=PlatformDetector(home=home,environ={'APPDATA':str(roaming),'LOCALAPPDATA':str(local),'PATH':''},system='Windows').detect()
    assert result['paths']['config']==str(roaming/'MCPManager')
    assert result['paths']['data']==str(local/'MCPManager')
    assert result['service_manager']=='windows-user-process'


def test_explicit_real_home_keeps_current_environment_metadata(tmp_path,monkeypatch):
    from mcp_manager.core import discovery
    site=tmp_path/'site-packages';site.mkdir()
    monkeypatch.setattr(discovery.Path,'home',classmethod(lambda cls:tmp_path))
    monkeypatch.setattr(discovery.sys,'path',[str(site)])
    engine=discovery.DiscoveryEngine(home=tmp_path,environ={})
    assert engine.metadata_paths==[str(site)]


def test_sensitive_backup_is_encrypted_and_restores_exactly(tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    from mcp_manager.adapters import clients
    from mcp_manager.models import ServerSpec
    key=Fernet.generate_key();monkeypatch.setattr(clients,'_backup_key',lambda:key,raising=False)
    path=tmp_path/'config.toml';original='[mcp_servers.one]\ncommand="old"\n[mcp_servers.one.env]\nAPI_KEY="PRIVATE_BACKUP_VALUE"\n';path.write_text(original)
    adapter=clients.ClientConfigAdapter('codex',path)
    result=adapter.apply_plan(adapter.plan_change('two',ServerSpec(name='two',command='python')))
    assert b'PRIVATE_BACKUP_VALUE' not in Path(result['backup_path']).read_bytes()
    assert result['backup_encrypted'] is True
    clients.restore_backup(result)
    assert path.read_text()==original


def test_mcp_sdk_dependency_and_console_entry_alone_are_not_a_server(tmp_path):
    from mcp_manager.core.discovery import DiscoveryEngine
    site=tmp_path/'env/lib/python3.11/site-packages';site.mkdir(parents=True)
    dist=site/'library-1.dist-info';dist.mkdir()
    (dist/'METADATA').write_text('Name: library\nVersion: 1\nRequires-Dist: mcp>=1\n')
    (dist/'entry_points.txt').write_text('[console_scripts]\nnot-a-server=library:main\n')
    (site/'library.py').write_text('def main():\n print("just a CLI")\n')
    scripts=tmp_path/'env/bin';scripts.mkdir();executable=scripts/'not-a-server';executable.write_text('#!/bin/sh\n');executable.chmod(0o700)
    r=DiscoveryEngine(tmp_path,{},metadata_paths=[str(site)]).discover()
    assert r['servers']==[]
    assert r['candidates']
