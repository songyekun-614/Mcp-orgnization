"""Versioned SQLite registry; every operation opens its own thread-safe connection."""
from contextlib import contextmanager
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from .security import redact,safe_spec,digest_payload
from ..models import ServerSpec


class Database:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve();self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c:
            c.execute('PRAGMA journal_mode=WAL')
            version=c.execute('PRAGMA user_version').fetchone()[0]
            if version>1:raise ValueError('Database version is newer than this Manager')
            c.execute('CREATE TABLE IF NOT EXISTS servers(id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL,id TEXT NOT NULL,data TEXT NOT NULL, PRIMARY KEY(kind,id))')
            c.execute('CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL)')
            c.execute('PRAGMA user_version=1')
        try:self.path.chmod(0o600)
        except OSError:pass
    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path,timeout=15)
        try:
            yield connection;connection.commit()
        except BaseException:
            connection.rollback();raise
        finally:connection.close()
    def upsert_server(self,spec,source=None):
        identity=spec.identity();now=datetime.now(timezone.utc).isoformat()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT data FROM servers WHERE identity=?',(identity,)).fetchone()
            if row:
                record=json.loads(row[0]);sources=record['sources']
                if source and redact(source) not in sources:sources.append(redact(source))
                record['updated_at']=now
            else:record=dict(id=uuid4().hex,spec=safe_spec(spec),sources=[redact(source)] if source else [],validation={'level':'launch_config_read','tools':[]},enabled=spec.enabled,managed=False,created_at=now,updated_at=now)
            c.execute('INSERT OR REPLACE INTO servers VALUES(?,?,?)',(record['id'],identity,json.dumps(record,ensure_ascii=False)))
        return record
    def list_servers(self):
        with self.connect() as c:return [json.loads(row[0]) for row in c.execute('SELECT data FROM servers ORDER BY rowid')]
    def get_server(self,server_id):
        with self.connect() as c:row=c.execute('SELECT data FROM servers WHERE id=?',(server_id,)).fetchone()
        if not row:raise KeyError('Unknown MCP ID: '+server_id)
        return json.loads(row[0])
    def update_server(self,server_id,fields):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT data FROM servers WHERE id=?',(server_id,)).fetchone()
            if not row:raise KeyError('Unknown MCP ID')
            record=json.loads(row[0]);old_spec=record['spec'];old_enabled=record['enabled'];generation=record.get('gateway_generation',0)
            record.update(redact(fields));record['gateway_generation']=generation;record['updated_at']=datetime.now(timezone.utc).isoformat()
            if ('spec' in fields and safe_spec(ServerSpec.model_validate(fields['spec']))!=old_spec) or record['enabled']!=old_enabled:
                record['gateway_generation']=generation+1
            if 'spec' in fields:
                model=ServerSpec.model_validate(fields['spec']);record['spec']=safe_spec(model)
                c.execute('UPDATE servers SET data=?, identity=? WHERE id=?',(json.dumps(record,ensure_ascii=False),model.identity(),server_id))
            else:c.execute('UPDATE servers SET data=? WHERE id=?',(json.dumps(record,ensure_ascii=False),server_id))
        return record
    def remove_server(self,server_id):
        with self.connect() as c:c.execute('DELETE FROM servers WHERE id=?',(server_id,))
    def record_validation(self,server_id,result):return self.update_server(server_id,{'validation':result})
    def save_record(self,kind,record_id,data):
        with self.connect() as c:c.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(kind,record_id,json.dumps(redact(data),ensure_ascii=False)))
        return data
    def get_record(self,kind,record_id):
        with self.connect() as c:row=c.execute('SELECT data FROM records WHERE kind=? AND id=?',(kind,record_id)).fetchone()
        if not row:raise KeyError('Unknown '+kind+' record: '+record_id)
        return json.loads(row[0])
    def list_records(self,kind):
        with self.connect() as c:return [json.loads(row[0]) for row in c.execute('SELECT data FROM records WHERE kind=? ORDER BY rowid DESC',(kind,))]
    def get_setting(self,key,default=None):
        try:return self.get_record('settings',key)['value']
        except KeyError:return default
    def set_setting(self,key,value):return self.save_record('settings',key,{'value':value})
    def audit(self,action,detail):
        with self.connect() as c:c.execute('INSERT INTO audit(at,action,detail) VALUES(?,?,?)',(datetime.now(timezone.utc).isoformat(),action,json.dumps(redact(detail),ensure_ascii=False)))
    def audit_records(self,limit=100):
        with self.connect() as c:return [dict(id=r[0],at=r[1],action=r[2],detail=json.loads(r[3])) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT ?',(limit,))]
    def consume_plan(self,plan_id,action):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT data FROM records WHERE kind=? AND id=?',('plans',plan_id)).fetchone()
            if not row:raise PermissionError('Unknown approval plan')
            record=json.loads(row[0])
            if record['state']!='approved' or record['action']!=action or record['digest']!=digest_payload(record['payload']) or datetime.fromisoformat(record['expires'])<datetime.now(timezone.utc):raise PermissionError('Local approval required, consumed, changed or expired')
            record['state']='consumed';c.execute('UPDATE records SET data=? WHERE kind=? AND id=?',(json.dumps(record),'plans',plan_id))
        self.audit('plan_consumed',{'plan_id':plan_id,'action':action});return record
    def backup(self,destination):
        destination=Path(destination)
        if destination.exists():raise FileExistsError(destination)
        with self.connect() as source:
            target=sqlite3.connect(destination)
            try:source.backup(target)
            finally:target.close()
        return str(destination)

    def restore_from(self,source_path):
        source_path=Path(source_path).expanduser().resolve()
        if source_path==self.path:raise ValueError('Backup and active database must differ')
        source=sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)
        try:
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup integrity check failed')
            if source.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('Unsupported backup schema')
            expected={'servers':['id','identity','data'],'records':['kind','id','data'],'audit':['id','at','action','detail']}
            tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables!=set(expected) or source.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger')").fetchone():raise ValueError('Backup has unexpected database objects')
            for name,columns in expected.items():
                actual=[r[1] for r in source.execute('PRAGMA table_info('+name+')')]
                if actual!=columns:raise ValueError('Backup columns do not match Manager schema')
            with self.connect() as target:source.backup(target)
        finally:source.close()
        return {'status':'completed','source':str(source_path),'restored':True}
