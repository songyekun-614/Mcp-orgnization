import hashlib
from pathlib import Path
import pytest
from mcp_manager.core.database import Database
from mcp_manager.core.security import ApprovalStore
from mcp_manager.models import ServerSpec
from mcp_manager.core.extensions import ExtensionService


@pytest.fixture
def svc(tmp_path):
    db=Database(tmp_path/'state.db'); approvals=ApprovalStore(db)
    sid=db.upsert_server(ServerSpec(name='target',command='python'))['id']
    return ExtensionService(db,approvals,tmp_path/'extensions'),sid


def submitted(svc, **changes):
    service,sid=svc; ws=service.workspace(sid); path=Path(ws['path'])
    (path/'module.py').write_text('def register(registry):\n    pass\n')
    (path/'test_host.py').write_text("import module\nassert callable(module.register)\n")
    manifest=dict(id='example',version='1.0.0',target='generic',module='module.py',tools=[dict(name='ext_example',input_schema={'type':'object'},output_schema={'type':'object'})],dependencies=[],permissions=[],tests={'host':'test_host.py'})
    manifest.update(changes)
    return service.submit(ws['id'],manifest)


def approved_execute(service,plan,executor):
    service.approvals.approve_local(plan['id']); consumed=service.approvals.consume(plan['id'],plan['action'])
    return executor(consumed['payload'])


def test_workspace_and_snapshot(svc):
    service,_=svc; record=submitted(svc)
    assert record['module_sha256']==hashlib.sha256(b'def register(registry):\n    pass\n').hexdigest()
    ws=service.db.get_record('extension_workspaces',record['workspace_id'])
    (Path(ws['path'])/'module.py').write_text('raise RuntimeError()')
    assert service.validate(record['id'])['valid']


def test_submission_rejects_symlinks_and_traversal(svc,tmp_path):
    service,sid=svc; ws=service.workspace(sid); path=Path(ws['path'])
    (path/'module.py').symlink_to(tmp_path/'outside.py')
    with pytest.raises((ValueError,PermissionError)):submitted_existing(service,ws)
    (path/'module.py').unlink(); (path/'module.py').write_text('pass')
    with pytest.raises((ValueError,PermissionError)):submitted_existing(service,ws,module='../outside.py')


def submitted_existing(service,ws,**changes):
    m=dict(id='example',version='1',target='generic',module='module.py',tools=[dict(name='ext_x',input_schema={},output_schema={})],dependencies=[],permissions=[],tests={})
    m.update(changes); return service.submit(ws['id'],m)


def test_static_schema_syntax_and_dependency_findings(svc):
    service,_=svc; r=submitted(svc,dependencies=['not installed'],tools=[dict(name='bad-name',input_schema={'type':'nonsense'},output_schema={})])
    report=service.validate(r['id']); assert not report['valid']; assert report['errors']


def test_test_requires_local_approval_and_native_is_never_fabricated(svc):
    service,_=svc; r=submitted(svc); plan=service.test(r['id'])
    assert plan['action']=='test_extension' and plan['state']=='pending'
    with pytest.raises(PermissionError):service.execute_test(plan['payload'])
    native=approved_execute(service,service.test(r['id'],'native'),service.execute_test)
    assert native['status']=='not_run' and native['level']=='native'


def test_real_host_sandbox_or_explicit_block(svc):
    service,_=svc; r=submitted(svc)
    result=approved_execute(service,service.test(r['id']),service.execute_test)
    assert result['status'] in {'passed','blocked','failed'}
    if result['status']=='passed':
        assert result['sandbox']=='linux-bwrap' and result['returncode']==0
    assert result['module_sha256']==r['module_sha256']
    with pytest.raises(PermissionError):service.execute_test(service.db.list_records('plans')[0]['payload'])


def test_changed_snapshot_cannot_test_or_publish(svc):
    service,_=svc;r=submitted(svc); plan=service.test(r['id'])
    (Path(r['snapshot'])/'module.py').write_text('pass')
    with pytest.raises((ValueError,PermissionError)):approved_execute(service,plan,service.execute_test)
    with pytest.raises((ValueError,PermissionError)):service.plan_install(r['id'])


def test_no_publish_without_evidence_and_no_unapproved_install(svc):
    service,_=svc;r=submitted(svc)
    with pytest.raises((ValueError,PermissionError)):service.plan_install(r['id'])
    with pytest.raises(PermissionError):service.install({'extension_id':r['id']})


def test_os_isolation_blocks_home_writes_network_and_ambient_secrets(svc,monkeypatch):
    service,sid=svc; ws=service.workspace(sid); path=Path(ws['path'])
    (path/'module.py').write_text('def register(registry):\n    pass\n')
    monkeypatch.setenv('MCP_TEST_SECRET','sensitive-test-value-3495')
    (path/'test_host.py').write_text('''import os, socket
from pathlib import Path
assert 'MCP_TEST_SECRET' not in os.environ
assert not Path('/home/song/.aws').exists()
try:
    Path('/usr/extension-isolation-probe').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('system is writable')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=0.2)
except OSError:
    pass
else:
    raise AssertionError('network not isolated')
Path('/work/output.txt').write_text('allowed')
''')
    r=submitted_existing(service,ws,tests={'host':'test_host.py'})
    report=approved_execute(service,service.test(r['id']),service.execute_test)
    if report['status']=='blocked':pytest.skip('Real OS isolation unavailable: '+report.get('reason','')+' '+report.get('stderr',''))
    assert report['status']=='passed',report
    plan=service.plan_install(r['id'])
    installed=approved_execute(service,plan,service.install)
    registry=Path(installed['registry'])
    assert registry.is_file()
    import json
    document=json.loads(registry.read_text())
    assert document['extensions'][0]['sha256']==r['module_sha256']
    with pytest.raises(PermissionError):service.install(plan['payload'])
    approved_execute(service,service.disable(r['id']),service.execute_state)
    assert not json.loads(registry.read_text())['extensions']
    approved_execute(service,service.enable(r['id']),service.execute_state)
    assert len(json.loads(registry.read_text())['extensions'])==1
    approved_execute(service,service.rollback(r['id']),service.execute_state)
    assert len(json.loads(registry.read_text())['extensions'])==1


def test_no_abaqus_release_without_actual_native_evidence(svc):
    service,_=svc; r=submitted(svc,target='abaqus')
    report=approved_execute(service,service.test(r['id'],'native'),service.execute_test)
    assert report['status']=='not_run'
    with pytest.raises(PermissionError):service.plan_install(r['id'])


def test_parent_symlink_boundary_rejected(svc,tmp_path):
    service,sid=svc; ws=service.workspace(sid)
    path=Path(ws['path']); path.rmdir(); elsewhere=tmp_path/'elsewhere';elsewhere.mkdir()
    path.symlink_to(elsewhere,target_is_directory=True)
    (elsewhere/'module.py').write_text('pass')
    with pytest.raises(PermissionError):submitted_existing(service,ws)


def test_changed_evidence_cannot_be_promoted_to_passed(svc):
    import json
    service,_=svc; r=submitted(svc)
    approved_execute(service,service.test(r['id']),service.execute_test)
    stored=service.db.get_record('extensions',r['id'])
    evidence=Path(stored['tests']['host']['evidence'])
    fake=dict(stored['tests']['host']['report'],status='passed')
    evidence.write_text(json.dumps(fake))
    with pytest.raises(PermissionError):service.plan_install(r['id'])


def test_test_output_redacts_ambient_secrets(svc,monkeypatch):
    import sys
    service,_=svc; monkeypatch.setenv('TEST_API_KEY','extension-test-secret-99881')
    result=service._run([sys.executable,'-I','-c',"print('extension-test-secret-99881');print('password=1234')"],{'PATH':'/usr/bin:/bin'})
    assert 'extension-test-secret-99881' not in result['stdout']
    assert '1234' not in result['stdout']


def test_schema_property_names_are_not_mistaken_for_stored_credentials(svc):
    service,_=svc
    record=submitted(svc,tools=[dict(name='ext_example',input_schema={'type':'object','properties':{'password':{'type':'string'}}},output_schema={'type':'object'})])
    assert service.validate(record['id'])['valid']


def test_generic_host_requires_manifest_and_closed_typed_registration(tmp_path):
    from mcp_manager.extension_host import ExtensionHost
    registry=tmp_path/'registry.json';registry.write_text('{"schema_version":1,"extensions":[]}')
    host=ExtensionHost(registry)
    assert host.tools=={}
    registry.write_text('{"schema_version":1,"extensions":[{"id":"example"}]}')
    with pytest.raises(ValueError):ExtensionHost(registry)


def test_test_plan_binds_operator_selected_immutable_container(svc):
    service,_=svc;record=submitted(svc)
    service.db.set_setting('extension_sandbox_image','latest')
    with pytest.raises(ValueError):service.test(record['id'])
    image='sha256:'+'a'*64
    service.db.set_setting('extension_sandbox_image',image)
    assert service.test(record['id'])['payload']['sandbox_image']==image


def _math_extension(service,sid,version='1',offset=0):
    ws=service.workspace(sid);path=Path(ws['path'])
    (path/'module.py').write_text(f'''def register(registry):
    @registry.tool('ext_add', 'Add two integers')
    async def add(x: int, y: int) -> dict:
        return {{'sum': x + y + {offset}}}
''')
    (path/'test_host.py').write_text('''import asyncio, module, os, socket
from pathlib import Path
class Registry:
    def tool(self, name, description):
        def decorate(fn):
            self.fn=fn
            return fn
        return decorate
registry=Registry()
module.register(registry)
assert asyncio.run(registry.fn(2,3))['sum'] >= 5
assert 'MCP_MANAGER_AMBIENT_SECRET' not in os.environ
assert not Path('/home/song/.aws').exists()
try:
    Path('/src/module.py').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('source mount is writable')
try:
    socket.create_connection(('1.1.1.1',443),timeout=.2)
except OSError:
    pass
else:
    raise AssertionError('network available')
Path('/work/result.txt').write_text('allowed')
''')
    return service.submit(ws['id'],dict(id='math-example',version=version,target='generic',module='module.py',
         tools=[dict(name='ext_add',input_schema={'type':'object','properties':{'x':{'type':'integer'},'y':{'type':'integer'}},'required':['x','y'],'additionalProperties':False},output_schema={'type':'object','properties':{'sum':{'type':'integer'}},'required':['sum'],'additionalProperties':False})],
         dependencies=[],permissions=[],tests={'host':'test_host.py'}))


@pytest.mark.integration
def test_real_docker_approval_install_mcp_call_disable_and_rollback(svc,monkeypatch):
    import os,asyncio,json,sys
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    image=os.environ.get('MCP_MANAGER_TEST_SANDBOX_IMAGE')
    if not image:pytest.skip('Set an explicit immutable local MCP_MANAGER_TEST_SANDBOX_IMAGE for Docker integration')
    service,sid=svc;service.db.set_setting('extension_sandbox_image',image)
    monkeypatch.setenv('MCP_MANAGER_AMBIENT_SECRET','must-not-enter-test-container')
    r=_math_extension(service,sid)
    result=approved_execute(service,service.test(r['id']),service.execute_test)
    assert result['status']=='passed',result
    assert result['sandbox'] in ('linux-bwrap','docker')
    installed=approved_execute(service,service.plan_install(r['id']),service.install)
    registry=Path(installed['registry'])
    async def call(expected):
        params=StdioServerParameters(command=sys.executable,args=['-m','mcp_manager.extension_host','--registry',str(registry)],env={'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize();tools=await session.list_tools()
                if expected is None:
                    assert tools.tools==[];return
                assert [t.name for t in tools.tools]==['ext_add']
                output=await session.call_tool('ext_add',{'x':2,'y':3})
                assert not output.isError and output.structuredContent=={'sum':expected},output
                wrong=await session.call_tool('ext_add',{'x':2,'y':3,'shell':'ls'})
                assert wrong.isError
    asyncio.run(call(5))
    approved_execute(service,service.disable(r['id']),service.execute_state);asyncio.run(call(None))
    approved_execute(service,service.enable(r['id']),service.execute_state);asyncio.run(call(5))
    newer=_math_extension(service,sid,version='2',offset=10)
    result=approved_execute(service,service.test(newer['id']),service.execute_test)
    assert result['status']=='passed',result
    approved_execute(service,service.plan_install(newer['id']),service.install);asyncio.run(call(15))
    approved_execute(service,service.rollback(r['id']),service.execute_state);asyncio.run(call(5))
    document=json.loads(registry.read_text());entry=document['extensions'][0]
    (registry.parent/entry['module']).write_text('raise RuntimeError("tampered")')
    from mcp_manager.extension_host import ExtensionHost
    with pytest.raises(ValueError):ExtensionHost(registry)
    evidence_dir=os.environ.get('MCP_MANAGER_TEST_EVIDENCE_DIR')
    if evidence_dir:
        import shutil
        output=Path(evidence_dir);output.mkdir(parents=True,exist_ok=True)
        for name in ('evidence','snapshots'):
            shutil.copytree(service.root/name,output/name,dirs_exist_ok=True)


def test_disable_remains_available_when_snapshot_was_damaged(svc):
    import json
    service,_=svc;record=submitted(svc)
    directory=service.root/'installed'/record['target_server_id'];directory.mkdir()
    registry=directory/'registry.json';entry=dict(id=record['manifest']['id'])
    registry.write_text(json.dumps({'schema_version':1,'extensions':[entry]}))
    record.update(status='installed',registry=str(registry),entry=entry,enabled=True)
    service.db.save_record('extensions',record['id'],record)
    (Path(record['snapshot'])/'module.py').write_text('damaged source')
    approved_execute(service,service.disable(record['id']),service.execute_state)
    assert json.loads(registry.read_text())['extensions']==[]


def test_static_validation_without_posix_open_flags(svc,monkeypatch):
    import os
    monkeypatch.delattr(os,'O_NOFOLLOW',raising=False)
    monkeypatch.delattr(os,'O_NONBLOCK',raising=False)
    service,_=svc;record=submitted(svc)
    assert service.validate(record['id'])['valid']
