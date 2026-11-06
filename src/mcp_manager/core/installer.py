"""Reviewable isolated installation plans; only ManagerCore consumes approval.

Venvs isolate dependencies, not untrusted code. Installing/building packages executes
publisher code and requires the explicit local approval represented by the plan.
"""
from email.parser import BytesParser
import zipfile
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4
from ..models import ServerSpec
from .security import within, redact, digest_payload


def _https(value):
    parts=urlsplit(value)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('HTTPS URL without credentials or query required')
    return value


def _match(value,pattern,label):
    if not isinstance(value,str) or not re.fullmatch(pattern,value):raise ValueError('Invalid '+label)
    return value


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


class InstallationService:
    def __init__(self,db,approvals,install_root):
        self.db=db;self.approvals=approvals
        root=Path(install_root).expanduser()
        if root.is_symlink():raise PermissionError('Install root cannot be a symlink')
        root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root=root.resolve()

    def _owned(self,path):
        path=within(path,self.root)
        if path.parent!=self.root or not re.fullmatch('[0-9a-f]{32}',path.name):raise PermissionError('Not an owned deployment path')
        marker=path/'.mcp-manager-owned'
        if marker.is_symlink() or not marker.is_file() or marker.read_text()!=path.name:raise PermissionError('Missing deployment ownership marker')
        return path

    def _payload(self,request,server_id=None):
        req=dict(request);kind=req.get('kind',req.get('installation_type','python'))
        deployment=uuid4().hex;path=self.root/deployment;commands=[]
        name=req.get('name') or req.get('package') or 'MCP server'
        version=req.get('version');package=req.get('package');dependencies=req.get('dependencies',True)
        source={'kind':kind,'package':package,'version':version,'registry':req.get('source'),'security_certified':False}
        if req.get('build_commands'):raise ValueError('Arbitrary build commands are unsupported; use an explicitly built local wheel')
        if kind in ('python','node'):
            _match(package,r'(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9][a-zA-Z0-9_.-]*','package')
            _match(version,r'[0-9][a-zA-Z0-9.+_-]*','fixed version')
        spec={'name':name,'package':package,'version':version,'installation_type':kind,'path':str(path)}
        local=None
        if kind=='python':
            if package.startswith('@') or '/' in package:raise ValueError('Invalid Python package')
            python=path/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
            installer=req.get('installer','pip')
            if installer not in ('pip','uv'):raise ValueError('Python installer must be pip or uv')
            commands.append([sys.executable,'-m','venv',str(path)])
            local_path=req.get('local_path')
            target=package+'=='+version
            if local_path:
                candidate=Path(local_path).expanduser().resolve(strict=True)
                if not candidate.is_file() or candidate.suffix!='.whl':raise ValueError('Local installations require a prebuilt wheel; unknown build scripts are not run')
                try:
                    with zipfile.ZipFile(candidate) as archive:
                        metadata=[entry for entry in archive.namelist() if entry.endswith('.dist-info/METADATA')]
                        if len(metadata)!=1 or archive.getinfo(metadata[0]).file_size>1024*1024:raise ValueError('Invalid wheel identity metadata')
                        metadata=BytesParser().parsebytes(archive.read(metadata[0]))
                    normalize=lambda value:re.sub(r'[-_.]+','-',value).lower()
                    if normalize(metadata.get('Name',''))!=normalize(package) or metadata.get('Version')!=version:raise ValueError('Wheel identity differs from requested package/version')
                except (zipfile.BadZipFile,KeyError) as exc:raise ValueError('Invalid wheel identity metadata') from exc
                target=str(candidate);local={'path':target,'sha256':_sha(candidate)}
                source.update(local_path=target,sha256=local['sha256'])
            cmd=([str(python),'-m','pip','--isolated','install','--disable-pip-version-check','--no-input'] if installer=='pip' else ['uv','pip','install','--python',str(python)])
            if not dependencies:cmd.append('--no-deps')
            if req.get('index_url'):cmd.extend(['--index-url',_https(req['index_url'])])
            if local:cmd.append('--no-index')
            cmd.append(target);commands.append(cmd)
            module=req.get('module',package.replace('-','_'))
            _match(module,r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*','Python module')
            spec.update(command=str(python),args=['-m',module],cwd=str(path))
        elif kind=='node':
            cmd=['npm','install','--prefix',str(path),'--ignore-scripts','--no-audit','--no-fund','--save-exact']
            if req.get('registry_url'):cmd.extend(['--registry',_https(req['registry_url'])])
            cmd.append(package+'@'+version);commands.append(cmd)
            executable=_match(req.get('executable',package.split('/')[-1]),r'[A-Za-z0-9][A-Za-z0-9_.-]*','Node executable')
            spec.update(command=str(path/'node_modules'/'.bin'/(executable+('.cmd' if os.name=='nt' else ''))),args=[],cwd=str(path))
        elif kind=='docker':
            _match(package,r'[a-z0-9][a-z0-9.-]*(?::[0-9]+)?(?:/[a-z0-9][a-z0-9._-]*)*(?:@sha256:[a-f0-9]{64}|:v?[0-9][a-zA-Z0-9_.-]*)','pinned Docker image')
            if package.rsplit(':',1)[-1] in ('latest','edge','main','master'):raise ValueError('Docker requires a fixed tag or digest')
            commands=[['docker','pull',package]]
            spec.update(command='docker',args=['run','--rm','-i','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges',package],version=package.split('@')[-1] if '@' in package else package.rsplit(':',1)[-1])
        elif kind=='git':
            repository=_https(req['repository']);commit=_match(req.get('commit'),r'[0-9a-fA-F]{40}','full Git commit')
            commands=[['git','-c','core.hooksPath='+os.devnull,'clone','--no-checkout','--',repository,str(path/'repo')],['git','-C',str(path/'repo'),'-c','core.hooksPath='+os.devnull,'checkout','--detach',commit]]
            command=_match(req.get('command','python'),r'[A-Za-z0-9][A-Za-z0-9_.-]*','launch command')
            args=req.get('args',[])
            if not isinstance(args,list) or any(not isinstance(x,str) or '\0' in x for x in args):raise ValueError('Invalid launch arguments')
            source.update(repository=repository,commit=commit)
            spec.update(command=command,args=args,cwd=str(path/'repo'),version=commit)
        elif kind=='remote':
            spec.update(transport='streamable-http',url=_https(req['url']),path=None,installation_type='remote')
        else:raise ValueError('Unsupported installation kind')
        spec=ServerSpec(**spec).model_dump()
        return {'operation':'install','deployment_id':deployment,'path':str(path),'request':req,'server_id':server_id,'commands':commands,'spec':spec,'source':source,'local_artifact':local,'dependencies':dependencies,'warning':'Installing executes publisher code. Dependency isolation is not an OS sandbox; Registry provenance is not a security certification.'}

    def plan(self,request):return self.approvals.create('install',self._payload(request))

    @staticmethod
    def _state_digest(record):
        return digest_payload({k:record.get(k) for k in ('spec','managed','version_history')})

    def plan_update(self,server_id,request):
        record=self.db.get_server(server_id)
        if not record['managed']:raise PermissionError('External deployments require a separate managed install')
        payload=self._payload(request,server_id);payload['state_digest']=self._state_digest(record)
        return self.approvals.create('install',payload)

    def rollback(self,server_id):
        record=self.db.get_server(server_id);history=record.get('version_history',[])
        if not record['managed'] or len(history)<2:raise ValueError('No managed previous version')
        previous=history[-2];self._owned(previous['spec']['path'])
        return self.approvals.create('install',{'operation':'rollback','server_id':server_id,'target':previous,'state_digest':self._state_digest(record)})

    def uninstall(self,server_id):
        record=self.db.get_server(server_id)
        paths=[item['spec'].get('path') for item in record.get('version_history',[])] if record['managed'] else []
        paths=list(dict.fromkeys(p for p in paths if p))
        for path in paths:self._owned(path)
        return self.approvals.create('install',{'operation':'uninstall','server_id':server_id,'paths':paths,'external':not record['managed'],'state_digest':self._state_digest(record)})

    def execute(self,payload):
        """Trusted Core entry point: caller MUST already consume this exact plan."""
        operation=payload.get('operation','install')
        if payload.get('server_id'):
            current=self.db.get_server(payload['server_id'])
            if payload.get('state_digest')!=self._state_digest(current):raise PermissionError('Managed deployment changed after preview; generate a fresh plan')
        if operation=='uninstall':
            record=self.db.get_server(payload['server_id'])
            paths=[self._owned(path) for path in payload['paths']]
            if not record['managed'] and paths:raise PermissionError('External project cannot be removed')
            for path in paths:shutil.rmtree(path)
            result=self.db.update_server(record['id'],{'managed':False,'enabled':False,'version_history':[],'installation_status':'uninstalled' if paths else 'unmanaged'})
            self.db.audit('uninstall',{'server_id':record['id'],'paths':payload['paths']})
            return result
        if operation=='rollback':
            record=self.db.get_server(payload['server_id']);target=payload['target']
            if target not in record.get('version_history',[]):raise PermissionError('Unknown rollback target')
            self._owned(target['spec']['path'])
            history=record['version_history'];index=history.index(target)
            history=history[:index]+history[index+1:]+[target]
            return self.db.update_server(record['id'],{'spec':target['spec'],'version_history':history,'installation_status':'installed'})
        if operation!='install':raise ValueError('Unknown installation operation')
        deployment=_match(payload['deployment_id'],r'[0-9a-f]{32}','deployment ID')
        path=within(payload['path'],self.root)
        if path!=self.root/deployment:raise PermissionError('Invalid deployment destination')
        artifact=payload.get('local_artifact')
        if artifact and _sha(artifact['path'])!=artifact['sha256']:raise PermissionError('Local wheel changed since plan')
        spec=ServerSpec(**payload['spec']);is_remote=spec.transport=='streamable-http'
        log=[];installation={'id':deployment,'source':payload['source'],'version':spec.version,'commands':payload['commands'],'dependencies':payload['dependencies'],'status':'installing','logs':log,'path':str(path)}
        self.db.save_record('installations',deployment,installation)
        created=False
        try:
            if not is_remote:
                path.mkdir(mode=0o700);created=True;(path/'.mcp-manager-owned').write_text(deployment)
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','USERPROFILE','SYSTEMROOT','WINDIR','TEMP','TMP','LANG','LC_ALL')}
            env.update(PIP_CONFIG_FILE=os.devnull,GIT_TERMINAL_PROMPT='0',GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
            for command in payload['commands']:
                with tempfile.TemporaryFile() as output:
                    run=subprocess.run(command,shell=False,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,timeout=300,env=env,cwd=str(self.root))
                    output.seek(0,2);size=output.tell();output.seek(max(0,size-32768));log.append(redact(output.read().decode('utf-8','replace')))
                if run.returncode:raise RuntimeError('Installation command failed (exit '+str(run.returncode)+'): '+log[-1][-2000:])
            if payload.get('server_id'):
                record=self.db.get_server(payload['server_id']);history=record.get('version_history',[])
            else:
                record=self.db.upsert_server(spec,payload['source']);history=[]
            history.append({'installation_id':deployment,'spec':spec.model_dump(),'source':payload['source']})
            record=self.db.update_server(record['id'],{'spec':spec.model_dump(),'managed':not is_remote,'version_history':history,'installation_status':'registered' if is_remote else 'installed'})
            installation.update(status='registered' if is_remote else 'installed',server_id=record['id'])
            self.db.save_record('installations',deployment,installation)
            return {'server_id':record['id'],'spec':record['spec'],'managed':record['managed'],'installation':installation}
        except Exception as exc:
            installation.update(status='failed',error=redact(str(exc)));self.db.save_record('installations',deployment,installation)
            if created:shutil.rmtree(self._owned(path))
            raise
