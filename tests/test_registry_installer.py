import json
from pathlib import Path
import subprocess
import zipfile
import pytest
import httpx
from mcp_manager.core.database import Database
from mcp_manager.core.security import ApprovalStore
from mcp_manager.core.registry import RegistryClient
from mcp_manager.core.installer import InstallationService

@pytest.fixture
def services(tmp_path):
    db=Database(tmp_path/'db.sqlite'); approvals=ApprovalStore(db)
    return db,approvals,InstallationService(db,approvals,tmp_path/'installs')

def test_registry_search_cache_and_paths(tmp_path,monkeypatch):
    db=Database(tmp_path/'db'); seen=[]
    def response(self,url,**kwargs):
        seen.append(str(url));return httpx.Response(200,json={'servers':[{'server':{'name':'io.test/demo','version':'1.0'}}],'metadata':{'nextCursor':'abc'}},request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx.Client,'get',response)
    client=RegistryClient(db)
    result=client.search('demo',cursor='previous')
    assert 'version=latest' in seen[0] and 'cursor=previous' in seen[0]
    assert result['metadata']['nextCursor']=='abc' and result['source']['security_certified'] is False
    assert client.search('demo',cursor='previous',offline=True)['cached'] is True
    client.detail('io.test/demo');assert 'io.test%2Fdemo/versions/latest' in seen[-1]
    client.versions('io.test/demo');assert seen[-1].endswith('io.test%2Fdemo/versions')
    with pytest.raises(ValueError):client.search(' x ')
    with pytest.raises(FileNotFoundError):client.search('missing',offline=True)

@pytest.mark.parametrize('install_request',[
 {'kind':'python','package':'--evil','version':'1.0'},
 {'kind':'python','package':'demo','version':'1; touch /tmp/no'},
 {'kind':'node','package':'demo','version':'latest'},
 {'kind':'docker','package':'demo:latest'},
 {'kind':'git','repository':'https://example.com/repo.git','commit':'main'},
 {'kind':'python','package':'demo','version':'1','index_url':'http://mirror.invalid'},
])
def test_reject_unsafe_plans(services,install_request):
    with pytest.raises(ValueError):services[2].plan(install_request)

def make_wheel(path,version='1.0',wheel_version='1.0'):
    filename=path/f'demo_mcp-{version}-py3-none-any.whl';dist=f'demo_mcp-{version}.dist-info'
    with zipfile.ZipFile(filename,'w') as z:
        z.writestr('demo_mcp.py', '''import sys,json
for line in sys.stdin:
    message=json.loads(line)
    if 'id' not in message:continue
    method=message.get('method')
    result=({'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'local-fixture','version':'1.0'}} if method=='initialize' else {'tools':[]})
    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
''')
        z.writestr(dist+'/METADATA',f'Metadata-Version: 2.1\nName: demo-mcp\nVersion: {version}\n')
        z.writestr(dist+'/WHEEL',f'Wheel-Version: {wheel_version}\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr(dist+'/RECORD','')
    return filename

def test_real_local_python_install_update_rollback_uninstall(services,tmp_path):
    db,approvals,service=services
    request={'kind':'python','name':'Local fixture','package':'demo-mcp','version':'1.0','local_path':str(make_wheel(tmp_path)),'module':'demo_mcp','dependencies':False}
    plan=service.plan(request)
    assert plan['state']=='pending' and not list(service.root.glob('*/.mcp-manager-owned'))
    approvals.approve_local(plan['id']);payload=approvals.consume(plan['id'],'install')['payload']
    installed=service.execute(payload)
    record=db.get_server(installed['server_id']);spec=record['spec']
    assert record['managed'] and Path(spec['path']).is_dir()
    run=subprocess.run([spec['command'],*spec['args']],capture_output=True,text=True,check=True,input=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{}})+'\n')
    assert json.loads(run.stdout)['result']['serverInfo']['name']=='local-fixture'
    newer=dict(request,version='2.0',local_path=str(make_wheel(tmp_path,'2.0')))
    plan=service.plan_update(record['id'],newer);approvals.approve_local(plan['id'])
    service.execute(approvals.consume(plan['id'],'install')['payload'])
    assert db.get_server(record['id'])['spec']['version']=='2.0'
    plan=service.rollback(record['id']);approvals.approve_local(plan['id'])
    service.execute(approvals.consume(plan['id'],'install')['payload'])
    assert db.get_server(record['id'])['spec']['version']=='1.0'
    plan=service.uninstall(record['id']);approvals.approve_local(plan['id'])
    service.execute(approvals.consume(plan['id'],'install')['payload'])
    assert not Path(spec['path']).exists()
    assert db.get_server(record['id'])['managed'] is False

def test_external_uninstall_only_unmanages(services,tmp_path):
    from mcp_manager.models import ServerSpec
    db,approvals,service=services
    outside=tmp_path/'external';outside.mkdir()
    record=db.upsert_server(ServerSpec(name='external',command='python',path=str(outside)))
    plan=service.uninstall(record['id']);service.execute(plan['payload'])
    assert outside.exists()

def test_failed_install_records_and_cleans_owned_directory(services,tmp_path):
    db,_,service=services
    bad=make_wheel(tmp_path,'1',wheel_version='99.0')
    plan=service.plan({'kind':'python','package':'demo-mcp','version':'1','local_path':str(bad),'module':'demo','dependencies':False})
    with pytest.raises(RuntimeError):service.execute(plan['payload'])
    assert db.list_records('installations')[0]['status']=='failed'
    assert not list(service.root.glob('*/.mcp-manager-owned'))

def test_node_docker_git_remote_plans(services):
    service=services[2]
    node=service.plan({'kind':'node','package':'@demo/server','version':'1.2.3','executable':'server'})['payload']
    assert '--prefix' in node['commands'][0] and '--ignore-scripts' in node['commands'][0]
    docker=service.plan({'kind':'docker','package':'example/server@sha256:'+'a'*64})['payload']
    assert docker['commands'][0][:2]==['docker','pull']
    assert '--privileged' not in str(docker) and '--mount' not in str(docker)
    git=service.plan({'kind':'git','repository':'https://example.com/demo.git','commit':'a'*40,'command':'python','args':['server.py']})['payload']
    assert not any('build' in command for command in git['commands'])
    remote=service.plan({'kind':'remote','name':'Remote','url':'https://example.com/mcp'})['payload']
    assert remote['commands']==[]

def test_local_wheel_identity_must_match_request(services,tmp_path):
    wheel=make_wheel(tmp_path)
    with pytest.raises(ValueError,match='identity'):
        services[2].plan({'kind':'python','package':'other-package','version':'1.0','local_path':str(wheel)})

def test_artifact_change_after_plan_is_rejected(services,tmp_path):
    wheel=make_wheel(tmp_path)
    plan=services[2].plan({'kind':'python','package':'demo-mcp','version':'1.0','local_path':str(wheel)})
    wheel.write_text('changed')
    with pytest.raises(PermissionError,match='changed'):services[2].execute(plan['payload'])

def test_uninstall_refuses_forged_managed_path(services,tmp_path):
    from mcp_manager.models import ServerSpec
    db,_,service=services
    external=tmp_path/'external';external.mkdir()
    spec=ServerSpec(name='external',command='python',path=str(external)).model_dump()
    record=db.upsert_server(ServerSpec(**spec))
    db.update_server(record['id'],{'managed':True,'version_history':[{'spec':spec}]})
    with pytest.raises(PermissionError):service.uninstall(record['id'])
    assert external.is_dir()

def test_docker_requires_numeric_version_and_accepts_registry_port(services):
    service=services[2]
    with pytest.raises(ValueError):service.plan({'kind':'docker','package':'example/server:stable'})
    result=service.plan({'kind':'docker','package':'registry.example:5000/team/server:v1.2.3'})
    assert result['payload']['commands']==[['docker','pull','registry.example:5000/team/server:v1.2.3']]

@pytest.mark.parametrize('operation',['update','rollback','uninstall'])
def test_maintenance_rejects_deployment_changed_after_approval(services,operation):
    from uuid import uuid4
    from mcp_manager.models import ServerSpec
    db,approvals,service=services
    history=[]
    for version in ('1.0','2.0'):
        path=service.root/uuid4().hex;path.mkdir();(path/'.mcp-manager-owned').write_text(path.name)
        spec=ServerSpec(name='fixture',command='python',path=str(path),version=version).model_dump()
        history.append({'spec':spec})
    record=db.upsert_server(ServerSpec(**history[-1]['spec']))
    db.update_server(record['id'],{'managed':True,'version_history':history})
    if operation=='update':plan=service.plan_update(record['id'],{'kind':'node','package':'demo','version':'3.0'})
    elif operation=='rollback':plan=service.rollback(record['id'])
    else:plan=service.uninstall(record['id'])
    approvals.approve_local(plan['id'])
    payload=approvals.consume(plan['id'],'install')['payload']
    changed=dict(history[-1]['spec'],description='Changed after approval')
    db.update_server(record['id'],{'spec':changed})
    with pytest.raises(PermissionError,match='deployment changed'):service.execute(payload)
    current=db.get_server(record['id'])
    assert current['managed'] is True and current['version_history']==history
    assert current['spec']['description']=='Changed after approval'
    assert all(Path(item['spec']['path']).is_dir() for item in history)
    assert db.get_record('plans',plan['id'])['state']=='consumed'


def test_stale_uninstall_plan_cannot_change_newer_deployment(services):
    from mcp_manager.models import ServerSpec
    db,_,service=services
    def owned(identity):
        path=service.root/(identity*32);path.mkdir();(path/'.mcp-manager-owned').write_text(path.name);return str(path)
    first=ServerSpec(name='managed',command='python',path=owned('a'),version='1')
    record=db.upsert_server(first);db.update_server(record['id'],{'managed':True,'version_history':[{'spec':first.model_dump()}]})
    plan=service.uninstall(record['id'])
    second=ServerSpec(name='managed',command='python2',path=owned('b'),version='2')
    db.update_server(record['id'],{'spec':second.model_dump(),'version_history':[{'spec':first.model_dump()},{'spec':second.model_dump()}]})
    with pytest.raises(PermissionError,match='changed'):service.execute(plan['payload'])
    assert Path(first.path).exists() and Path(second.path).exists()
    assert db.get_server(record['id'])['managed'] is True
