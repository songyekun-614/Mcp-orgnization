"""Progressive tool discovery and forwarding; decisions and code remain with the Agent."""
import hashlib
import json
import os
import re
from uuid import uuid4
from jsonschema import validators,SchemaError
from mcp.types import CallToolResult,TextContent
from ..models import ServerSpec
from .security import digest_payload
from .gateway_connection import GatewayConnection

POLICY_KEY='gateway_policy'
ALIASES={'材料':['material','elastic'],'创建':['create'],'几何':['geometry','part'],'打开':['open'],'读取':['read','inspect'],'参考点':['reference_point'],'截面':['section'],'分析步':['step'],'装配':['assembly','instance'],
         '网格':['mesh','seed'],'求解':['job','submit'],'载荷':['load','gravity'],'边界':['boundary','bc'],
         '后处理':['odb','field','history','contour'],'云图':['contour'],'位移':['displacement','U'],
         '文档':['documentation'],'模型':['model'],'连接':['coupling','connector','tie']}


def plan_gateway(core,server_ids,tool_allowlist=None,result_limit_bytes=8192,call_timeout=240):
    if not isinstance(server_ids,list) or not 1<=len(server_ids)<=16 or len(set(server_ids))!=len(server_ids):raise ValueError('Select 1-16 distinct registered servers')
    if type(result_limit_bytes) is not int or not 2048<=result_limit_bytes<=65536:raise ValueError('Result limit must be 2048-65536 bytes')
    if type(call_timeout) is not int or not 1<=call_timeout<=3600:raise ValueError('Call timeout must be 1-3600 seconds')
    if tool_allowlist is not None and (not isinstance(tool_allowlist,dict) or set(tool_allowlist)-set(server_ids)):raise ValueError('Allowlist must select the chosen backend IDs')
    backends=[];names=set()
    for server_id in server_ids:
        row=core.get_server(server_id);spec=ServerSpec.model_validate(row['spec']);core.runtime._validate(spec)
        if not row['enabled']:raise PermissionError('Backend is disabled')
        namespace=re.sub('[^A-Za-z0-9_-]','_',spec.name)[:48]
        if not namespace or namespace in names:raise ValueError('Backend names must yield distinct namespaces')
        names.add(namespace);allowed=(tool_allowlist or {}).get(server_id)
        if allowed is not None and (not isinstance(allowed,list) or not allowed or any(not isinstance(n,str) or not 1<=len(n)<=128 for n in allowed)):raise ValueError('Select a nonempty list of allowed tool names')
        backends.append(dict(server_id=server_id,namespace=namespace,spec_digest=digest_payload(row['spec']),launch=row['spec'],generation=row.get('gateway_generation',0),requested_tools=allowed))
    return core.approvals.create('configure_gateway',dict(backends=backends,result_limit_bytes=result_limit_bytes,call_timeout=call_timeout,
        notice='Authorizes these exact backend programs and selected tools through a separate three-tool gateway. Existing backend permissions, execute flags and validations still apply. Newly advertised names are not automatically granted.'))


def apply_gateway(core,payload):
    grants=[]
    for entry in payload['backends']:
        row=core.get_server(entry['server_id']);core._verify_spec(row,entry)
        if not row['enabled'] or row.get('gateway_generation',0)!=entry['generation']:raise PermissionError('Backend state changed; create a new local approval')
        result=core.runtime.probe(ServerSpec.model_validate(row['spec']),timeout=20)
        if result['status']!='completed':raise RuntimeError('Backend discovery failed: '+str(result.get('error'))[:1000])
        discovered={tool['name'] for tool in result['tools']}
        allowed=entry['requested_tools'] if entry['requested_tools'] is not None else sorted(discovered)
        if any(not isinstance(name,str) or not 1<=len(name)<=128 for name in allowed):raise ValueError('Backend tool name must contain 1-128 characters')
        if set(allowed)-discovered:raise ValueError('Allowed tool is absent from the actual backend')
        core.db.record_validation(row['id'],result)
        grants.append(dict(server_id=row['id'],namespace=entry['namespace'],spec_digest=entry['spec_digest'],generation=entry['generation'],allowed_tools=sorted(set(allowed))))
    # Recheck every launch after all probes; a partial failure leaves the old policy active.
    for grant in grants:
        row=core.get_server(grant['server_id']);core._verify_spec(row,grant)
        if not row['enabled'] or row.get('gateway_generation',0)!=grant['generation']:raise PermissionError('Backend state changed during discovery')
    policy=dict(schema_version=1,id=uuid4().hex,backends=grants,result_limit_bytes=payload['result_limit_bytes'],call_timeout=payload['call_timeout'])
    core.db.set_setting(POLICY_KEY,policy);core.db.audit('gateway_policy_configured',{'policy_id':policy['id'],'backends':[g['server_id'] for g in grants]})
    return dict(status='completed',policy=policy,model_visible_tools=['search_tools','get_tool_details','call_tool'],reconnect_required=True)


def validate_arguments(schema,arguments):
    def check_refs(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in ('$ref','$dynamicRef') and isinstance(item,str) and not item.startswith('#'):
                    raise ValueError('External schema references are not allowed; bundle local definitions')
                check_refs(item)
        elif isinstance(value,list):
            for item in value:check_refs(item)
    check_refs(schema)
    try:
        validator=validators.validator_for(schema);validator.check_schema(schema)
        error=next(validator(schema).iter_errors(arguments),None)
    except SchemaError:raise ValueError('Backend advertises an invalid input schema')
    if error is not None:raise ValueError('Invalid tool arguments at '+str(error.json_path)[:300]+' ('+str(error.validator)+')')


def schema_hash(tool):
    return digest_payload({key:tool.get(key) for key in ('name','description','inputSchema','outputSchema')})


def encoded(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()


class GatewayService:
    def __init__(self,core):self.core=core;self.connections={}
    async def __aenter__(self):return self
    async def __aexit__(self,*args):await self.close()
    async def close(self):
        for connection in self.connections.values():await connection.close()
        self.connections.clear()
    async def _sync_policy(self):
        active=self.policy()['id']
        for key in list(self.connections):
            if key[0]!=active:
                await self.connections.pop(key).close()
    def policy(self):
        policy=self.core.db.get_setting(POLICY_KEY,dict(schema_version=1,id='none',backends=[],result_limit_bytes=8192,call_timeout=240))
        if policy.get('schema_version')!=1:raise PermissionError('Unsupported gateway policy; configure it locally')
        return policy
    def _check(self,grant,policy_id):
        policy=self.policy()
        if policy['id']!=policy_id or grant not in policy['backends']:raise PermissionError('Gateway grant was replaced or revoked')
        row=self.core.get_server(grant['server_id'])
        if not row['enabled'] or row.get('gateway_generation',0)!=grant.get('generation') or digest_payload(row['spec'])!=grant['spec_digest']:raise PermissionError('Backend configuration changed or was disabled; local approval is required again')
        spec=ServerSpec.model_validate(row['spec']);self.core.runtime._validate(spec)
        return spec
    def _connection(self,grant):
        policy=self.policy();spec=self._check(grant,policy['id']);key=(policy['id'],grant['server_id'])
        if key not in self.connections:
            self.connections[key]=GatewayConnection(self.core.runtime,spec,policy['call_timeout'],lambda:self._check(grant,policy['id']))
        return self.connections[key]
    def _grant(self,tool_id):
        if not isinstance(tool_id,str) or '/' not in tool_id:raise KeyError('Use namespace/tool_name from search_tools')
        namespace,name=tool_id.split('/',1)
        grant=next((g for g in self.policy()['backends'] if g['namespace']==namespace),None)
        if grant is None or name not in grant['allowed_tools']:raise PermissionError('Tool is not allowed by the local gateway grant')
        return grant,name
    async def search_tools(self,query='',server=None,offset=0,limit=3):
        await self._sync_policy()
        if not isinstance(query,str) or len(query)>300 or type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=10:raise ValueError('Invalid search or page bounds')
        words=re.findall('[a-z0-9]+',query.lower())
        for term,aliases in ALIASES.items():
            if term in query:words.extend(w.lower() for w in aliases)
        words=set(words);matches=[];servers=[]
        for grant in self.policy()['backends']:
            if server and server!=grant['namespace']:continue
            self._check(grant,self.policy()['id'])
            connection=self.connections.get((self.policy()['id'],grant['server_id']))
            if connection and connection.task and not connection.task.done():catalog=await connection.request('catalog')
            else:catalog={t['name']:t for t in self.core.get_server(grant['server_id'])['validation'].get('tools',[])}
            servers.append(dict(server=grant['namespace'],tools=len(set(catalog)&set(grant['allowed_tools']))))
            for name in grant['allowed_tools']:
                tool=catalog.get(name)
                if tool is None:continue
                description=tool.get('description') or ''
                score=sum(8*(w in name.lower())+2*(w in description.lower()) for w in words)
                if query and not score:continue
                matches.append(dict(tool_id=grant['namespace']+'/'+name,description=description.split('\n')[0][:180],_score=score))
        matches.sort(key=lambda t:(-t['_score'],t['tool_id']))
        page=dict(tools=[],total=len(matches),next_offset=offset if offset<len(matches) else None,servers=servers)
        for row in matches[offset:offset+limit]:
            item={k:v for k,v in row.items() if k!='_score'}
            candidate=dict(page,tools=page['tools']+[item])
            if len(encoded(candidate))>self.policy()['result_limit_bytes']:break
            page=candidate
        count=len(page['tools']);page['next_offset']=offset+count if offset+count<len(matches) else None
        if len(encoded(page))>self.policy()['result_limit_bytes'] or (offset<len(matches) and not count):raise ValueError('Search page exceeds result limit; select a server')
        return page
    async def get_tool_details(self,tool_id):
        await self._sync_policy()
        grant,name=self._grant(tool_id);connection=self._connection(grant);catalog=await connection.request('catalog')
        if name not in catalog:raise KeyError('Tool is no longer advertised')
        tool=catalog[name]
        value=dict(tool_id=tool_id,description=tool.get('description',''),input_schema=tool['inputSchema'],schema_hash=schema_hash(tool),
            annotations=tool.get('annotations'),backend_instructions=connection.instructions,result_hint='call_tool forwards validated arguments. Large results return a local artifact and bounded summary; use the backend pagination parameters when available.')
        if len(encoded(value))>65536:raise ValueError('Tool definition exceeds 64 KiB; it cannot be safely loaded into this small-context interface')
        return value
    async def call_tool(self,tool_id,arguments,expected_hash):
        await self._sync_policy()
        if not isinstance(arguments,dict) or len(encoded(arguments))>262144:raise ValueError('Arguments must be a JSON object <=256 KiB')
        arguments=json.loads(encoded(arguments))
        grant,name=self._grant(tool_id);connection=self._connection(grant)
        def check(catalog):
            self._check(grant,self.policy()['id'])
            tool=catalog.get(name)
            if tool is None:raise KeyError('Tool is no longer advertised')
            if schema_hash(tool)!=expected_hash:raise ValueError('Tool schema changed; call get_tool_details again')
            validate_arguments(tool['inputSchema'],arguments)
        # Validate before queueing, then revalidate against a fresh catalog in the worker.
        catalog=await connection.request('catalog');check(catalog)
        result=await connection.request('call',name,arguments,check)
        safe=connection.sanitize(result.model_dump(mode='json'))
        value=safe.get('structuredContent')
        if value is None:value={'content':safe.get('content',[])}
        response=dict(tool_id=tool_id,schema_hash=expected_hash,is_error=bool(safe.get('isError')),result=value,truncated=False)
        limit=self.policy()['result_limit_bytes']
        if len(encoded(response))>limit:
            raw=encoded(safe)
            if len(raw)>8*1024*1024:raise ValueError('Backend result exceeds 8 MiB; request a smaller page or backend file export')
            directory=self.core.data_dir/'gateway-results'
            if directory.is_symlink():raise PermissionError('Result directory must not be a symlink')
            directory.mkdir(exist_ok=True,mode=0o700);result_id=uuid4().hex;path=directory/(result_id+'.json')
            descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(descriptor,'wb') as stream:stream.write(raw)
            summary={}
            if isinstance(value,dict):
                for key in ('status','operation','task_id','job_status','cae_path','odb_path','artifacts','error','context','statistics','summary'):
                    if key in value and len(encoded(value[key]))<=768 and len(encoded(summary))<1024:summary[key]=value[key]
            response=dict(tool_id=tool_id,is_error=bool(safe.get('isError')),truncated=True,summary=summary,output_file=str(path),result_id=result_id,original_bytes=len(raw))
            if len(encoded(response))>limit:response['summary']={}
            if len(encoded(response))>limit:raise ValueError('Result artifact metadata exceeds the configured limit')
        self.core.db.audit('gateway_tool_called',{'server_id':grant['server_id'],'tool':name,'is_error':response['is_error'],'truncated':response['truncated']})
        return CallToolResult(isError=response['is_error'],structuredContent=response,content=[TextContent(type='text',text=encoded(response).decode())])
