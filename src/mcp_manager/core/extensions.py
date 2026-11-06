"""Agent-authored extension snapshots and locally approved, OS-isolated tests.

Static review is not a sandbox. Approved runtime Python has server privileges.
Native/e2e adapters are deliberately unavailable until an actual native sandbox
and result verifier are provided; an arbitrary Python script is not native proof.
"""
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4

from jsonschema.validators import validator_for
from .security import digest_payload, redact, SENSITIVE

_NAME = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
_TOOL = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_DEP = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:[=<>!~]=?[A-Za-z0-9.*+_-]+(?:,[=<>!~]=?[A-Za-z0-9.*+_-]+)*)?\Z')
_MAX = 1024 * 1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _plain(path, root):
    """Reject symlinks in every component, not only the last component."""
    path = Path(path).absolute(); root = Path(root).absolute()
    if '..' in path.parts or not path.is_relative_to(root):
        raise PermissionError('Path outside extension boundary')
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise PermissionError('Extension symlinks are forbidden')
    return path


def _read(path, root):
    path = _plain(path, root)
    fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_BINARY',0))
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Extension files must be regular files')
        data = stream.read(_MAX + 1)
    if len(data) > _MAX:
        raise ValueError('Extension file exceeds 1 MiB')
    return data


def _filename(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', value) or value in ('.', '..'):
        raise ValueError('Files must be direct children with plain names')
    return value


def _write(path, data):
    """Private atomic files, never follow an existing link."""
    _plain(path, path.parent)
    tmp = path.with_name('.' + uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():tmp.unlink()


def _json_bytes(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2).encode()


class ExtensionService:
    def __init__(self, db, approvals, root):
        self.db, self.approvals = db, approvals
        self.root = _plain(Path(root).absolute(), Path(root).absolute())
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        for name in ('workspaces', 'snapshots', 'evidence', 'installed', 'runs'):
            path = _plain(self.root / name, self.root)
            path.mkdir(exist_ok=True, mode=0o700)

    def workspace(self, target_server_id):
        self.db.get_server(target_server_id)
        identity = uuid4().hex
        path = self.root / 'workspaces' / identity
        path.mkdir(mode=0o700)
        record = dict(id=identity, target_server_id=target_server_id, path=str(path), status='draft')
        self.db.save_record('extension_workspaces', identity, record)
        return record

    def submit(self, workspace_id, manifest):
        ws = self.db.get_record('extension_workspaces', workspace_id)
        if not isinstance(manifest, dict):raise ValueError('Manifest must be an object')
        # JSON canonicalization rejects non-JSON data and detaches caller-owned values.
        manifest = json.loads(json.dumps(manifest, allow_nan=False))
        required = {'id', 'version', 'target', 'module', 'tools', 'dependencies', 'permissions', 'tests'}
        if set(manifest) != required:raise ValueError('Manifest fields must be: ' + ', '.join(sorted(required)))
        if not isinstance(manifest['id'], str) or not _NAME.fullmatch(manifest['id']):raise ValueError('Invalid extension identity')
        if not isinstance(manifest['version'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}', manifest['version']):raise ValueError('Invalid version')
        if manifest['target'] not in ('generic', 'abaqus'):raise ValueError('Supported targets: generic, abaqus')
        if manifest['module'] != 'module.py':raise ValueError('Source must be module.py')
        if not isinstance(manifest['tests'], dict) or set(manifest['tests']) - {'host', 'native', 'e2e'}:raise ValueError('Invalid tests declaration')
        files = {'module.py'}
        for level, name in manifest['tests'].items():
            if name != 'test_' + level + '.py':raise ValueError('Test files must use test_LEVEL.py')
            files.add(_filename(name))
        source = _plain(Path(ws['path']), self.root)
        blobs = {name:_read(source/name, self.root) for name in sorted(files)}
        total = sum(map(len, blobs.values()))
        if total > 4*_MAX:raise ValueError('Snapshot too large')
        identity = uuid4().hex; snapshot = self.root/'snapshots'/identity
        snapshot.mkdir(mode=0o700)
        for name, data in blobs.items():_write(snapshot/name, data)
        _write(snapshot/'manifest.json', _json_bytes(manifest))
        hashes = {name:_sha(data) for name,data in blobs.items()}
        record = dict(id=identity, workspace_id=workspace_id, target_server_id=ws['target_server_id'], manifest=manifest,
                      snapshot=str(snapshot), files=hashes, module_sha256=hashes['module.py'],
                      snapshot_sha256=digest_payload(dict(manifest=manifest, files=hashes)), status='submitted', tests={})
        self.db.save_record('extensions', identity, record)
        self.db.audit('extension_submitted', dict(extension_id=identity, snapshot_sha256=record['snapshot_sha256']))
        return record

    def _record(self, extension_id):
        record = self.db.get_record('extensions', extension_id)
        path = _plain(Path(record['snapshot']), self.root/'snapshots')
        for name, expected in record['files'].items():
            if _sha(_read(path/_filename(name), self.root)) != expected:raise PermissionError('Extension snapshot changed; resubmit and retest')
        # Schemas may contain property names such as password: DB redaction is
        # intentionally lossy. Reconstruct from the hash-bound private snapshot.
        record['manifest'] = json.loads(_read(path/'manifest.json', self.root))
        if digest_payload(dict(manifest=record['manifest'], files=record['files'])) != record['snapshot_sha256']:raise PermissionError('Snapshot digest changed')
        return record

    def validate(self, extension_id):
        record = self._record(extension_id); manifest = record['manifest']; errors=[]; findings=[]
        tools = manifest['tools']; names=[]
        if not isinstance(tools, list) or not 1 <= len(tools) <= 32:errors.append('Require 1..32 declared tools'); tools=[]
        for tool in tools:
            if not isinstance(tool, dict) or set(tool) != {'name','input_schema','output_schema'}:
                errors.append('Tool requires name/input_schema/output_schema');continue
            name = tool['name']
            if not isinstance(name, str) or not _TOOL.fullmatch(name):errors.append('Invalid tool name');continue
            names.append(name)
            for key in ('input_schema','output_schema'):
                try:validator_for(tool[key]).check_schema(tool[key])
                except Exception as exc:errors.append(key + ': ' + str(exc)[:300])
        if len(set(names)) != len(names):errors.append('Duplicate tool name')
        target = self.db.get_server(record['target_server_id'])
        existing = {t if isinstance(t,str) else t.get('name') for t in target.get('validation',{}).get('tools',[]) if isinstance(t,(str,dict))}
        for other in self.db.list_records('extensions'):
            if other['id'] != extension_id and other.get('status') == 'installed' and other['target_server_id']==record['target_server_id'] and other['manifest']['id'] != manifest['id']:
                existing.update(t['name'] for t in other['manifest']['tools'])
        if set(names) & existing:errors.append('Tool name conflicts with target or installed extension')
        dependencies = manifest['dependencies']
        if not isinstance(dependencies, list) or any(not isinstance(x,str) or not _DEP.fullmatch(x) for x in dependencies):errors.append('Dependencies must be package/version declarations; URLs and commands forbidden');dependencies=[]
        permissions = manifest['permissions']
        if not isinstance(permissions, list) or any(x not in ('read_workspace','write_workspace','native_software','network') for x in permissions):errors.append('Invalid permission declaration')
        declared = {re.split(r'[<>=!~]', x)[0].lower().replace('-','_') for x in dependencies}
        for filename in record['files']:
            try:tree = ast.parse(_read(Path(record['snapshot'])/filename,self.root), filename=filename)
            except (SyntaxError,ValueError) as exc:errors.append(filename + ': '+str(exc));continue
            if filename=='module.py' and not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='register' for n in tree.body):errors.append('module.py must define register(registry)')
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or '']
                    for module in modules:
                        name = module.split('.')[0]
                        if name and name not in sys.stdlib_module_names and name not in ('module',) and name not in declared:errors.append('Undeclared dependency: '+name)
                if isinstance(node,ast.Call):
                    name = ast.unparse(node.func)
                    if any(word in name for word in ('exec','eval','compile','subprocess','system','popen','socket','ctypes','unlink','rmtree','open')):
                        findings.append(dict(file=filename,line=node.lineno,call=name))
        report = dict(valid=not errors,errors=errors,review_findings=findings,
                      warning='AST findings are review hints, not a sandbox or a security verdict',
                      module_sha256=record['module_sha256'],snapshot_sha256=record['snapshot_sha256'])
        self.db.save_record('extension_validations', extension_id, report)
        return report

    def test(self, extension_id, level='host'):
        if level not in ('host','native','e2e'):raise ValueError('Unknown test level')
        record = self._record(extension_id)
        if not self.validate(extension_id)['valid']:raise ValueError('Static validation failed')
        image=self.db.get_setting('extension_sandbox_image')
        if image is not None and (not isinstance(image,str) or not re.fullmatch(r'sha256:[a-f0-9]{64}',image)):
            raise ValueError('Sandbox image must be an operator-selected immutable sha256 image ID')
        return self.approvals.create('test_extension', dict(extension_id=extension_id,level=level,sandbox_image=image,
                                     snapshot_sha256=record['snapshot_sha256'],nonce=uuid4().hex))

    plan_test = test

    def _claim(self, action, payload):
        # Core consumes the local approval first. This durable second claim prevents
        # direct API calls and replay after Core has consumed the plan.
        with self.db.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            rows=connection.execute("SELECT id,data FROM records WHERE kind='plans'").fetchall()
            for plan_id, data in rows:
                plan=json.loads(data)
                if plan['action']==action and plan['state']=='consumed' and datetime.fromisoformat(plan['expires'])>=datetime.now(timezone.utc) and plan['digest']==digest_payload(payload) and plan['payload']==payload:
                    if connection.execute("SELECT 1 FROM records WHERE kind='extension_claims' AND id=?",(plan_id,)).fetchone():continue
                    connection.execute("INSERT INTO records VALUES('extension_claims',?,?)",(plan_id,json.dumps({'action':action})))
                    return
        raise PermissionError('A fresh locally approved and consumed extension plan is required')

    def execute_test(self, payload):
        self._claim('test_extension', payload)
        record=self._record(payload['extension_id'])
        if record['snapshot_sha256']!=payload['snapshot_sha256']:raise PermissionError('Test approval snapshot mismatch')
        level=payload['level']
        report=dict(id=uuid4().hex,level=level,status='not_run',module_sha256=record['module_sha256'],snapshot_sha256=record['snapshot_sha256'])
        if level!='host':report['reason']='No verified native software sandbox/result adapter available; Python scripts are not native evidence'
        elif level not in record['manifest']['tests']:report['reason']='Agent has not supplied test_host.py'
        else:report.update(self._host(record,payload.get('sandbox_image')))
        evidence=self.root/'evidence'/(report['id']+'.json')
        _write(evidence,_json_bytes(report))
        proof=dict(evidence=str(evidence),sha256=_sha(_read(evidence,self.root)),report=report)
        record['tests'][level]=proof
        self.db.save_record('extensions', record['id'], record)
        self.db.audit('extension_test',dict(extension_id=record['id'],level=level,status=report['status']))
        return report

    def _host(self,record,sandbox_image=None):
        from ..adapters.isolation import IsolationAdapter
        return IsolationAdapter(self.root,_read,_write,_sha).run(record,sandbox_image)

    @staticmethod
    def _run(command,env):
        from ..adapters.isolation import IsolationAdapter
        return IsolationAdapter._run(command,env)

    def _publishable(self, record):
        if not self.validate(record['id'])['valid']:raise ValueError('Static validation failed')
        required=('host','native') if record['manifest']['target']=='abaqus' else ('host',)
        # Generic host-only publication allows workspace-only declared rights.
        if record['manifest']['target']=='generic' and set(record['manifest']['permissions'])-{'read_workspace','write_workspace'}:
            raise PermissionError('Generic host-only policy permits workspace permissions only')
        for level in required:
            proof=record['tests'].get(level)
            if not proof:raise PermissionError('Required '+level+' evidence missing')
            data=_read(Path(proof['evidence']),self.root/'evidence')
            if _sha(data)!=proof['sha256']:raise PermissionError('Evidence changed')
            report=json.loads(data)
            if report.get('sandbox') not in ('linux-bwrap','docker') or report.get('returncode')!=0 or report.get('timed_out') or report!=proof['report'] or report.get('status')!='passed' or report.get('level')!=level or report.get('module_sha256')!=record['module_sha256'] or report.get('snapshot_sha256')!=record['snapshot_sha256']:
                raise PermissionError('Required '+level+' test not passed for this snapshot')
        return required

    def plan_install(self, extension_id, target_registry=None):
        record=self._record(extension_id);self._publishable(record)
        registry=self._registry_path(record['target_server_id'],target_registry)
        before=_sha(_read(registry,self.root)) if registry.exists() else None
        return self.approvals.create('install_extension',dict(extension_id=extension_id,snapshot_sha256=record['snapshot_sha256'],target_registry=str(registry),registry_before=before,nonce=uuid4().hex))

    def _registry_path(self, server_id, configured=None):
        directory=self.root/'installed'/server_id
        path=Path(configured).absolute() if configured else directory/'registry.json'
        _plain(path,self.root/'installed')
        if path!=directory/'registry.json':raise PermissionError('Registry must be Manager-owned per-server registry.json')
        return path

    def install(self, payload):
        self._claim('install_extension',payload)
        with self._lock:
            record=self._record(payload['extension_id']);levels=self._publishable(record)
            if record['snapshot_sha256']!=payload['snapshot_sha256']:raise PermissionError('Install approval snapshot mismatch')
            registry=self._registry_path(record['target_server_id'],payload['target_registry']);registry.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            self._check_before(registry,payload['registry_before'])
            document=json.loads(_read(registry,self.root)) if registry.exists() else dict(schema_version=1,extensions=[])
            extension_name=record['manifest']['id'];prefix=extension_name+'_'+record['id']
            module=prefix+'.py'
            module_data=_read(Path(record['snapshot'])/'module.py',self.root)
            if _sha(module_data)!=record['module_sha256']:raise PermissionError('Snapshot changed during install')
            _write(registry.parent/module,module_data)
            tests={}
            for level in levels:
                name=prefix+'_'+level+'.json';data=_read(Path(record['tests'][level]['evidence']),self.root)
                if _sha(data)!=record['tests'][level]['sha256']:raise PermissionError('Evidence changed during install')
                _write(registry.parent/name,data);tests[level]=dict(evidence=name,sha256=_sha(data))
            entry=dict(id=extension_name,module=module,sha256=record['module_sha256'],tools=[t['name'] for t in record['manifest']['tools']],tests=tests)
            if record['manifest']['target']=='generic':
                manifest_name=prefix+'_manifest.json';manifest_data=_json_bytes(record['manifest'])
                _write(registry.parent/manifest_name,manifest_data)
                entry['manifest']=dict(file=manifest_name,sha256=_sha(manifest_data))
            previous=[e for e in document['extensions'] if e['id']==extension_name]
            document['extensions']=[e for e in document['extensions'] if e['id']!=extension_name]+[entry]
            if len(document['extensions'])>32:raise ValueError('Registry extension limit reached')
            _write(registry,_json_bytes(document))
            record.update(status='installed',registry=str(registry),entry=entry,enabled=True)
            self.db.save_record('extensions',record['id'],record)
            for old in self.db.list_records('extensions'):
                if old['id']!=record['id'] and old.get('status')=='installed' and old['target_server_id']==record['target_server_id'] and old['manifest']['id']==extension_name:
                    old.update(status='superseded',enabled=False);self.db.save_record('extensions',old['id'],old)
            self.db.audit('extension_installed',dict(extension_id=record['id'],registry=str(registry),previous=previous))
            return dict(status='installed',extension_id=record['id'],registry=str(registry),restart_required=True,
                        configuration_suggestion={'env':{'ABAQUS_EXTENSION_REGISTRY':str(registry)}} if record['manifest']['target']=='abaqus' else {},
                        runtime='target_startup_loader' if record['manifest']['target']=='abaqus' else 'mcp_manager.extension_host',
                        launch_suggestion=None if record['manifest']['target']=='abaqus' else dict(command=sys.executable,args=['-m','mcp_manager.extension_host','--registry',str(registry)]))

    def _check_before(self, registry, expected):
        actual=_sha(_read(registry,self.root)) if registry.exists() else None
        if actual!=expected:raise PermissionError('Registry changed since approval plan')

    def _state_plan(self, extension_id, operation):
        record=self.db.get_record('extensions',extension_id) if operation=='disable' else self._record(extension_id)
        if record.get('status') not in ('installed','superseded'):raise ValueError('Extension has never been installed')
        if operation!='disable':self._publishable(record)
        registry=self._registry_path(record['target_server_id'],record['registry'])
        return self.approvals.create('extension_state',dict(extension_id=extension_id,operation=operation,
                     snapshot_sha256=record['snapshot_sha256'],registry_before=_sha(_read(registry,self.root)),nonce=uuid4().hex))

    def enable(self, extension_id):return self._state_plan(extension_id,'enable')
    def disable(self, extension_id):return self._state_plan(extension_id,'disable')
    def rollback(self, extension_id):return self._state_plan(extension_id,'rollback')

    def execute_state(self, payload):
        self._claim('extension_state',payload)
        with self._lock:
            operation=payload['operation']
            if operation not in ('enable','disable','rollback'):raise ValueError('Unknown extension state operation')
            record=self.db.get_record('extensions',payload['extension_id']) if operation=='disable' else self._record(payload['extension_id'])
            if operation!='disable':self._publishable(record)
            if record['snapshot_sha256']!=payload['snapshot_sha256']:raise PermissionError('Extension state snapshot mismatch')
            registry=self._registry_path(record['target_server_id'],record['registry']);self._check_before(registry,payload['registry_before'])
            document=json.loads(_read(registry,self.root));name=record['manifest']['id']
            if operation=='disable' and any(e['id']==name and e!=record['entry'] for e in document['extensions']):
                raise ValueError('This version is superseded; disable the active version')
            document['extensions']=[e for e in document['extensions'] if e['id']!=name]
            enabled=operation!='disable'
            if enabled:
                self._check_entry(record,registry.parent)
                document['extensions'].append(record['entry'])
            _write(registry,_json_bytes(document))
            for old in self.db.list_records('extensions'):
                if old['target_server_id']==record['target_server_id'] and old['manifest']['id']==name and old.get('status') in ('installed','superseded'):
                    active=enabled and old['id']==record['id']
                    old.update(enabled=active,status='installed' if active or old['id']==record['id'] else 'superseded')
                    self.db.save_record('extensions',old['id'],old)
            return dict(status='enabled' if enabled else 'disabled',extension_id=record['id'],restart_required=True)

    def _check_entry(self, record, directory):
        entry=record['entry']
        if _sha(_read(directory/entry['module'],self.root))!=entry['sha256']:raise PermissionError('Installed module changed')
        if 'manifest' in entry:
            proof=entry['manifest']
            if _sha(_read(directory/proof['file'],self.root))!=proof['sha256']:raise PermissionError('Installed manifest changed')
        for proof in entry['tests'].values():
            if _sha(_read(directory/proof['evidence'],self.root))!=proof['sha256']:raise PermissionError('Installed evidence changed')
