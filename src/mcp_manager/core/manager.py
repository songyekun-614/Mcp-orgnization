"""Shared application API: GUI and MCP use identical discovery and approval logic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path,PureWindowsPath
import shutil
import threading
from uuid import uuid4

from ..models import ServerSpec
from ..adapters.platform import PlatformDetector
from ..adapters.clients import ClientConfigAdapter,restore_backup
from .database import Database
from .discovery import DiscoveryEngine
from .security import ApprovalStore,redact,safe_spec,digest_payload
from .runtime import RuntimeManager


class ManagerCore:
    def __init__(self,data_dir=None,home=None,environ=None):
        self.platform=PlatformDetector(home,environ).detect();self.home=Path(self.platform['home'])
        self.environ=dict(os.environ if environ is None else environ)
        self.data_dir=Path(data_dir or self.platform['paths']['data']).expanduser().resolve();self.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=Database(self.data_dir/'registry.sqlite');self.approvals=ApprovalStore(self.db)
        self.runtime=RuntimeManager(self.data_dir/'logs',self.environ);self.discovery=DiscoveryEngine(self.home,self.environ)
        self._executor=ThreadPoolExecutor(max_workers=4,thread_name_prefix='mcp-manager');self._tasks={};self._task_lock=threading.RLock()
        self._client_plans={}
    @property
    def installer(self):
        from .installer import InstallationService
        return InstallationService(self.db,self.approvals,self.data_dir/'servers')
    @property
    def extensions(self):
        from .extensions import ExtensionService
        return ExtensionService(self.db,self.approvals,self.data_dir/'extensions')
    def detect_platform(self):return self.platform
    def register_server(self,spec,source=None):
        model=ServerSpec.model_validate(spec)
        record=self.db.upsert_server(model,source or {'kind':'user-registration'})
        self.db.audit('server_registered',{'server_id':record['id'],'name':model.name})
        return record
    def discover(self,roots=None,mode='quick',**limits):
        if mode=='deep':raise PermissionError('Use plan_scan for a locally approved deep scan')
        result=self.discovery.discover(roots=roots or self.db.get_setting('scan_roots',[]),mode=mode,**limits)
        return self._register_discovery(result)
    def _register_discovery(self,result):
        registered=[]
        for item in result['servers']:
            spec=ServerSpec.model_validate(item['spec']);record=None
            for source in item.get('sources',[item['source']]):record=self.db.upsert_server(spec,source)
            if record:registered.append(record)
        result['registered']=registered;self.db.save_record('discovery','latest',result)
        self.db.audit('discovery_completed',{'count':len(registered),'stats':result['stats']});return result
    def list_servers(self):return self.db.list_servers()
    def get_server(self,server_id):return self.db.get_server(server_id)
    def list_tools(self,server_id,offset=0,limit=50):
        if offset<0 or not 1<=limit<=100:raise ValueError('Invalid tool page')
        tools=self.get_server(server_id).get('validation',{}).get('tools',[]);end=min(offset+limit,len(tools))
        return dict(server_id=server_id,tools=tools[offset:end],total=len(tools),next_offset=end if end<len(tools) else None)
    def get_status(self,server_id):
        record=self.get_server(server_id)
        return dict(server_id=server_id,enabled=record['enabled'],runtime=self.runtime.status(server_id),last_validation=record['validation'])
    def dashboard(self):
        records=self.list_servers();tasks=self.list_tasks();connected=sum(self.runtime.status(r['id']).get('status')=='running' for r in records)
        return dict(registered=len(records),connected=connected,tools=sum(r.get('validation',{}).get('tool_count',0) for r in records),
            updates=sum(bool(r.get('update_available')) for r in records),recent_errors=[r for r in records if r.get('validation',{}).get('status')=='failed'][:10],recent_tasks=tasks[:10])
    def search_registry(self,query,cursor=None,offline=False):
        from .registry import RegistryClient
        result=RegistryClient(self.db,self.db.get_setting('registry_url','https://registry.modelcontextprotocol.io')).search(query,cursor=cursor,offline=offline)
        installed={(r['spec'].get('package'),r['spec'].get('version')) for r in self.list_servers()}
        for item in result.get('servers',[]):
            server=item.get('server',item)
            item['installed']=any((p.get('identifier'),p.get('version')) in installed for p in server.get('packages',[]))
        return result
    def registry_detail(self,name,version='latest',offline=False):
        from .registry import RegistryClient
        return RegistryClient(self.db,self.db.get_setting('registry_url','https://registry.modelcontextprotocol.io')).detail(name,version,offline)
    def plan_connection(self,server_id,tool_name=None,arguments=None):
        server=self.get_server(server_id)
        if not server['enabled']:raise PermissionError('Enable this MCP before connecting')
        payload=dict(server_id=server_id,spec_digest=digest_payload(server['spec']),launch=server['spec'],tool_name=tool_name,arguments=arguments or {},timeout=30,
            notice='Starts the exact configured program or connects to the remote endpoint; optional tool call may modify target software')
        return self.approvals.create('connect',payload)
    def plan_scan(self,roots,max_depth=8,max_files=50000,max_seconds=60):
        if not roots:raise ValueError('Select explicit roots for deep scanning')
        return self.approvals.create('scan',dict(roots=roots,max_depth=max_depth,max_files=max_files,max_seconds=max_seconds))
    def _installation_request(self,request):
        request=dict(request)
        if request.get('kind',request.get('installation_type','python'))=='python':
            request.setdefault('index_url',self.db.get_setting('mirror','https://pypi.org/simple'))
        return request
    def plan_install(self,request):return self.installer.plan(self._installation_request(request))
    def plan_client_change(self,client,path,name,server_id=None,remove=False):
        spec=None if remove else ServerSpec.model_validate(self.get_server(server_id)['spec'])
        if spec is not None:spec.name=name
        candidate=ClientConfigAdapter(client,path).plan_change(name,spec)
        # Recompute after approval instead of storing original secrets in a patch.
        payload=dict(client=client,path=candidate['path'],name=name,server_id=server_id,remove=remove,
            spec=spec.model_dump() if spec else None,before_sha256=candidate['before_sha256'],
            after_sha256=hashlib.sha256(candidate['after_text'].encode()).hexdigest(),
            preview={'target':name,'operation':'remove' if remove else 'add_or_update','configuration':safe_spec(spec) if spec else None})
        return self.approvals.create('configure_client',payload)
    def plan_gateway(self,server_ids,**kwargs):
        from .gateway import plan_gateway
        return plan_gateway(self,server_ids,**kwargs)
    def approve_plan(self,plan_id):return self.approvals.approve_local(plan_id)
    def execute_plan(self,plan_id):
        record=self.db.get_record('plans',plan_id);action=record['action'];plan=self.approvals.consume(plan_id,action);p=plan['payload']
        try:
            if action=='connect':
                server=self.get_server(p['server_id']);self._verify_spec(server,p)
                result=self.runtime.probe(ServerSpec.model_validate(server['spec']),p.get('tool_name'),p.get('arguments'),p.get('timeout',30))
                self.db.record_validation(server['id'],result)
            elif action=='scan':result=self._register_discovery(self.discovery.discover(mode='deep',authorized=True,**p))
            elif action=='configure_client':
                adapter=ClientConfigAdapter(p['client'],p['path']);spec=ServerSpec.model_validate(p['spec']) if p.get('spec') else None
                patch=adapter.plan_change(p['name'],spec)
                if patch['before_sha256']!=p['before_sha256'] or hashlib.sha256(patch['after_text'].encode()).hexdigest()!=p['after_sha256']:raise ValueError('Configuration or plan changed after preview')
                result=adapter.apply_plan(patch);self.db.save_record('client_backups',plan_id,result)
                if p.get('server_id'):
                    server=self.get_server(p['server_id']);clients=server.get('clients',[])
                    connection={'client':p['client'],'path':p['path'],'name':p['name']}
                    if p['remove']:clients=[c for c in clients if c!=connection]
                    elif connection not in clients:clients.append(connection)
                    self.db.update_server(server['id'],{'clients':clients})
            elif action=='restore_client':result=restore_backup(self.db.get_record('client_backups',p['backup_id']))
            elif action=='restore_database':
                if any(t['status'] in ('queued','running') for t in self.list_tasks()):raise ValueError('Stop background tasks before restoring the database')
                if any(self.runtime.status(r['id']).get('status')=='running' for r in self.list_servers()):raise ValueError('Stop owned MCP connections before restoring the database')
                path=Path(p['path'])
                if hashlib.sha256(path.read_bytes()).hexdigest()!=p['sha256']:raise ValueError('Database backup changed after approval')
                self.db.backup(self.data_dir/('before-restore-'+uuid4().hex+'.sqlite'));result=self.db.restore_from(path)
            elif action=='configure_gateway':
                from .gateway import apply_gateway
                result=apply_gateway(self,p)
            elif action=='install':result=self.installer.execute(p)
            elif action=='server_action':result=self._server_action(p)
            elif action=='test_extension':result=self.extensions.execute_test(p)
            elif action=='install_extension':result=self.extensions.install(p)
            elif action=='extension_state':result=self.extensions.execute_state(p)
            else:raise ValueError('Unsupported approved action: '+action)
            self.db.save_record('plan_results',plan_id,{'status':'completed','result':result});self.db.audit('operation_completed',{'plan_id':plan_id,'action':action})
            return result
        except Exception as exc:
            self.db.save_record('plan_results',plan_id,{'status':'failed','error':{'type':type(exc).__name__,'message':str(exc)}})
            self.db.audit('operation_failed',{'plan_id':plan_id,'action':action,'error_type':type(exc).__name__});raise
    @staticmethod
    def _verify_spec(server,payload):
        if digest_payload(server['spec'])!=payload['spec_digest']:raise ValueError('Server launch configuration changed after approval')
    def plan_action(self,action,server_id,**kwargs):
        server=self.get_server(server_id)
        if action=='update':return self.installer.plan_update(server_id,self._installation_request(kwargs.get('request',kwargs)))
        if action=='rollback':return self.installer.rollback(server_id)
        if action=='uninstall':return self.installer.uninstall(server_id)
        if action not in ('enable','disable','start','stop','restart','unmanage'):raise ValueError('Unknown lifecycle action')
        if action in ('start','restart') and not server['managed']:
            raise PermissionError('External MCP is not taken over; use a temporary connection test or explicitly install/register a managed deployment')
        return self.approvals.create('server_action',dict(action=action,server_id=server_id,spec_digest=digest_payload(server['spec']),launch=server['spec']))
    def _server_action(self,p):
        server=self.get_server(p['server_id']);self._verify_spec(server,p);action=p['action']
        if action in ('enable','disable'):
            enabled=action=='enable';spec=dict(server['spec'],enabled=enabled)
            return self.db.update_server(server['id'],{'enabled':enabled,'spec':spec})
        if action=='unmanage':
            if self.runtime.status(server['id']).get('status')=='running':raise ValueError('Stop the owned connection before removing registration')
            self.db.remove_server(server['id']);return {'status':'completed','removed_registration':True,'files_deleted':False}
        if action=='stop':return self.runtime.stop(server['id'])
        if action=='restart':self.runtime.stop(server['id'])
        result=self.runtime.start(server['id'],ServerSpec.model_validate(server['spec']))
        self.db.record_validation(server['id'],result);return result
    def plan_restore_client(self,backup_id):
        record=self.db.get_record('client_backups',backup_id)
        return self.approvals.create('restore_client',{'backup_id':backup_id,'path':record['path']})
    def list_clients(self):
        sources={}
        for record in self.list_servers():
            for source in record.get('sources',[]):
                if source.get('kind') in ('codex','dsh'):sources[(source['kind'],source['path'])]=source
        defaults=[('codex',Path(self.environ.get('CODEX_HOME',str(self.home/'.codex')))/'config.toml')]
        profiles=Path(self.environ.get('DSH_HOME',str(self.home/'.dsh')))/'profiles'
        if profiles.is_dir():defaults += [('dsh',p) for p in profiles.glob('*/cordis*.yml') if p.name in ('cordis.yml','cordis.patch.yml')]
        for client,path in defaults:sources.setdefault((client,str(path)),{'kind':client,'path':str(path)})
        result=[]
        for (client,path),source in sources.items():
            item=dict(client=client,path=path,exists=Path(path).is_file())
            try:item['servers']=[safe_spec(s) for s in ClientConfigAdapter(client,path).read()]
            except (OSError,ValueError,TypeError) as exc:item['error']={'type':type(exc).__name__,'message':'Configuration could not be parsed'}
            result.append(item)
        return {'clients':result}
    def get_logs(self,server_id=None):
        records=self.db.audit_records(100)
        if server_id:records=[r for r in records if r['detail'].get('server_id') in (None,server_id)]
        result={'audit':records,'tasks':self.list_tasks()[:20]}
        if server_id:
            validation=self.get_server(server_id).get('validation',{});path=validation.get('log_path')
            if path:
                resolved=Path(path).resolve()
                if not resolved.is_relative_to(self.data_dir/'logs') or resolved.is_symlink():raise PermissionError('Log path outside Manager data')
                result['text']=redact(resolved.read_text()[-65536:]) if resolved.is_file() else ''
        return result
    def get_settings(self):
        return dict(theme=self.db.get_setting('theme','light'),scan_roots=self.db.get_setting('scan_roots',[]),registry_url=self.db.get_setting('registry_url','https://registry.modelcontextprotocol.io'),
            mirror=self.db.get_setting('mirror','https://pypi.org/simple'),agent_permission=self.db.get_setting('agent_permission','read_only'),extension_sandbox_image=self.db.get_setting('extension_sandbox_image',''),data_dir=str(self.data_dir))
    def save_settings(self,values):
        allowed={'theme','scan_roots','registry_url','mirror','scan_interval_seconds','agent_permission','extension_sandbox_image'}
        if set(values)-allowed:raise ValueError('Unsupported setting; permissions are changed only by the local trusted interface')
        if values.get('theme','light') not in ('light','dark','system'):raise ValueError('Invalid theme')
        if values.get('agent_permission','read_only') not in ('read_only','approved_only'):raise ValueError('Unknown Agent permission policy')
        image=values.get('extension_sandbox_image')
        if image:
            import re
            if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('Extension test image must be a locally available immutable sha256 ID')
        for key in ('registry_url','mirror'):
            if key in values and not values[key].startswith('https://'):raise ValueError('HTTPS is required for package sources')
        for key,value in values.items():self.db.set_setting(key,value)
        self.db.audit('local_settings_updated',{'keys':sorted(values)})
        return self.get_settings()
    def start_task(self,operation,parameters):
        if operation not in ('discover','execute_plan','search_registry'):raise ValueError('Unsupported background operation')
        task_id=uuid4().hex;record=dict(id=task_id,operation=operation,status='queued',created_at=datetime.now(timezone.utc).isoformat())
        cancel=threading.Event();pause=threading.Event()
        def worker():
            record['status']='running';self.db.save_record('tasks',task_id,record)
            try:
                if operation=='discover':result=self.discover(cancel=cancel,pause=pause,**parameters)
                else:result=getattr(self,operation)(**parameters)
                record.update(status='cancelled' if isinstance(result,dict) and result.get('stats',{}).get('cancelled') else 'completed',result=result)
            except Exception as exc:record.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)})
            self.db.save_record('tasks',task_id,record)
        self.db.save_record('tasks',task_id,record)
        with self._task_lock:self._tasks[task_id]={'cancel':cancel,'pause':pause,'future':self._executor.submit(worker)}
        return record
    def list_tasks(self):return self.db.list_records('tasks')
    def task_status(self,task_id):return self.db.get_record('tasks',task_id)
    def control_task(self,task_id,action):
        with self._task_lock:item=self._tasks.get(task_id)
        if not item:raise KeyError('Task is not running in this Manager')
        if self.task_status(task_id)['operation']!='discover':raise ValueError('Only directory scans support pause/cancel controls')
        if action=='cancel':item['cancel'].set()
        elif action=='pause':item['pause'].set()
        elif action=='resume':item['pause'].clear()
        else:raise ValueError('Unknown task control')
        return {'task_id':task_id,'requested':action}
    def export_configuration(self):
        servers=[]
        def portable(value):
            if isinstance(value,str) and (Path(value).is_absolute() or PureWindowsPath(value).is_absolute()):return '${RELOCATE:'+Path(value.replace('\\','/')).name+'}'
            if isinstance(value,list):return [portable(x) for x in value]
            if isinstance(value,dict):return {k:portable(v) for k,v in value.items()}
            return value
        for record in self.list_servers():
            spec=safe_spec(ServerSpec.model_validate(record['spec']));spec['path']=None
            servers.append(dict(spec=portable(spec),clients=sorted({s['kind'] for s in record['sources'] if s.get('kind') in ('codex','dsh')})))
        return {'schema_version':1,'source_system':self.platform['system'],'servers':servers,'secrets_included':False,'engineering_data_included':False}
    def import_configuration(self,bundle):
        if bundle.get('schema_version')!=1 or not isinstance(bundle.get('servers'),list) or len(bundle['servers'])>1000:raise ValueError('Invalid migration bundle')
        known=self.list_servers();resolutions=[]
        for item in bundle['servers']:
            spec=item['spec'];matches=[r['id'] for r in known if spec.get('package') and (r['spec'].get('package'),r['spec'].get('version'))==(spec.get('package'),spec.get('version'))]
            resolutions.append({'name':spec['name'],'existing_matches':matches,'status':'found' if matches else 'relocation_or_install_plan_required','template':spec})
        return {'status':'completed','target_platform':self.platform['system'],'resolutions':resolutions,'installed_automatically':False,'configuration_written':False}
    def close(self):
        for task in self._tasks.values():task['cancel'].set()
        self.runtime.close();self._executor.shutdown(wait=False,cancel_futures=True)

    def check_updates(self,server_id):
        """Query fixed public package APIs; never install or execute a package."""
        import httpx
        from urllib.parse import quote
        from packaging.version import Version,InvalidVersion
        record=self.get_server(server_id);spec=record['spec'];kind=spec.get('installation_type');package=spec.get('package')
        if kind not in ('python','node') or not package:
            return {'server_id':server_id,'status':'not_supported','reason':'Automatic version metadata is available for pinned Python/Node packages; Git/Docker/remote require explicit reviewed versions'}
        url=('https://pypi.org/pypi/'+quote(package,safe='')+'/json') if kind=='python' else 'https://registry.npmjs.org/'+quote(package,safe='')+'/latest'
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:
            response=client.get(url);response.raise_for_status();data=response.json()
        latest=data['info']['version'] if kind=='python' else data['version'];current=spec.get('version')
        try:available=Version(latest)>Version(current)
        except (InvalidVersion,TypeError):available=latest!=current
        result={'server_id':server_id,'status':'completed','current':current,'latest':latest,'update_available':available,'source':url}
        self.db.update_server(server_id,{'update_available':available,'update_check':result});return result

    def plan_database_restore(self,path):
        candidate=Path(path).expanduser().resolve()
        if not candidate.is_file() or candidate.stat().st_size>128*1024*1024:raise ValueError('Select a SQLite backup <=128 MiB')
        return self.approvals.create('restore_database',{'path':str(candidate),'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest()})
