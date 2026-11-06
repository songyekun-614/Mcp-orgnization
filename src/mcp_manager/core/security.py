"""Redaction, bounded filesystem access and local approval plans."""
import hashlib
import json
import re
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4

SENSITIVE=re.compile(r'(token|password|passwd|secret|api[_-]?key|authorization|credential)',re.I)


def redact(value,key=''):
    if key.endswith('_refs') and isinstance(value,dict):return dict(value)
    if SENSITIVE.search(key) and not isinstance(value,(dict,list)):return '<redacted>'
    if isinstance(value,dict):return {str(k):redact(v,str(k)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str):
        value=re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+',r'\1<redacted>',value)
        value=re.sub(r'(?i)((?:token|password|api_key|secret)=)[^&\s]+',r'\1<redacted>',value)
    return value


def safe_spec(spec):
    value=spec.model_dump() if hasattr(spec,'model_dump') else dict(spec)
    refs=dict(value.get('env_refs',{}));env={}
    for key,item in value.get('env',{}).items():
        if SENSITIVE.search(key):refs.setdefault(key,key)
        else:env[key]=item
    value.update(env=env,env_refs=refs)
    return value


def within(path,root):
    path=Path(path).expanduser();root=Path(root).expanduser().resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise PermissionError('Path is outside the managed boundary')
    return path.resolve()


def digest_payload(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


class ApprovalStore:
    def __init__(self,db):self.db=db
    def create(self,action,payload):
        record=dict(id=uuid4().hex,action=action,payload=payload,digest=digest_payload(payload),state='pending',expires=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        self.db.save_record('plans',record['id'],record);self.db.audit('plan_created',{'plan_id':record['id'],'action':action})
        return record
    def approve_local(self,plan_id):
        record=self.db.get_record('plans',plan_id)
        if record['state']!='pending' or datetime.fromisoformat(record['expires'])<datetime.now(timezone.utc):raise PermissionError('Plan is not pending or has expired')
        if digest_payload(record['payload'])!=record['digest']:raise PermissionError('Plan payload changed')
        record['state']='approved';self.db.save_record('plans',plan_id,record);self.db.audit('plan_approved_locally',{'plan_id':plan_id})
        return record
    def consume(self,plan_id,action):
        # Database transaction makes concurrent GUI/MCP consumption single-use.
        return self.db.consume_plan(plan_id,action)
