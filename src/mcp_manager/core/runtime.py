"""Real SDK sessions; temporary probes never take ownership of external processes."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta,datetime,timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4

import anyio
import httpx
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from .security import redact

AMBIENT={'PATH','HOME','USER','USERNAME','SYSTEMROOT','SystemRoot','WINDIR','TEMP','TMP','TMPDIR','APPDATA','LOCALAPPDATA','XDG_RUNTIME_DIR','DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','LANG','LANGUAGE','LC_ALL','PYTHONUTF8'}


class RuntimeManager:
    def __init__(self,log_root,environ=None):
        self.log_root=Path(log_root);self.log_root.mkdir(parents=True,exist_ok=True)
        self.environ=dict(os.environ if environ is None else environ);self._sessions={};self._lock=threading.RLock()
    def _validate(self,spec):
        command=Path(spec.command or '').name.lower()
        if command in ('npx','npx.cmd','uvx','uvx.exe') and not any(x in spec.args for x in ('--no-install','--offline')):
            raise PermissionError('Connection probes cannot implicitly download code; install a pinned package first')
        if 'mcp-manager-server' in command or any('mcp_manager'==x for x in spec.args) and any(x in spec.args for x in ('serve','server')):
            raise PermissionError('Manager cannot connect recursively to itself')
        if spec.url and spec.url.rstrip('/')==self.environ.get('MCP_MANAGER_ENDPOINT','').rstrip('/'):
            raise PermissionError('Manager cannot connect recursively to its own HTTP endpoint')
        if not spec.enabled:raise PermissionError('MCP is disabled')
    def _credentials(self,refs):
        values={}
        for name,ref in refs.items():
            if ref.startswith('keyring:'):
                import keyring
                value=keyring.get_password('mcp-manager',ref[8:])
            else:value=self.environ.get(ref.removeprefix('env:'))
            if value is None:raise ValueError('Unresolved credential/environment reference: '+ref)
            values[name]=value
        return values
    @asynccontextmanager
    async def _session(self,spec,errlog,timeout):
        if spec.transport=='stdio':
            env={k:v for k,v in self.environ.items() if k in AMBIENT};env.update(spec.env);env.update(self._credentials(spec.env_refs))
            parameters=StdioServerParameters(command=spec.command,args=spec.args,cwd=spec.cwd,env=env)
            async with stdio_client(parameters,errlog=errlog) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=timeout)) as session:yield session
        else:
            headers=self._credentials(spec.headers_refs)
            if 'Authorization' in headers and not headers['Authorization'].lower().startswith(('bearer ','basic ')):headers['Authorization']='Bearer '+headers['Authorization']
            async with httpx.AsyncClient(headers=headers,timeout=timeout,follow_redirects=False,trust_env=False) as client:
                async with streamable_http_client(spec.url,http_client=client) as (read,write,_):
                    async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=timeout)) as session:yield session
    async def _discover_tools(self,session):
        tools=[];cursor=None;seen=set()
        for _ in range(50):
            page=await session.list_tools(cursor=cursor);tools.extend(t.model_dump(mode='json') for t in page.tools)
            if len(tools)>5000:raise ValueError('Tool discovery exceeds the 5000-tool limit')
            cursor=page.nextCursor
            if not cursor:break
            if cursor in seen:raise ValueError('Server repeats tool pagination cursor')
            seen.add(cursor)
        else:raise ValueError('Tool pagination exceeds limit')
        if len(json.dumps(tools))>8*1024*1024:raise ValueError('Tool schemas exceed 8 MiB')
        return tools

    async def _exercise(self,spec,record,errlog,timeout,tool_name=None,arguments=None,hold=None,ready=None):
        with anyio.fail_after(timeout):
            async with self._session(spec,errlog,timeout) as session:
                record['facts']['transport_connected']=True
                if spec.transport=='stdio':record['facts']['process_started']=True
                record['level']='transport_connected'
                initialized=await session.initialize();record['facts']['handshake_completed']=True;record['level']='handshake_completed'
                record['server_info']=self._sanitize(initialized.serverInfo.model_dump(mode='json'),spec);record['protocol_version']=initialized.protocolVersion
                tools=await self._discover_tools(session)
                record.update(tools=self._sanitize(tools,spec),tool_count=len(tools),level='tools_discovered');record['facts']['tools_discovered']=True
                if tool_name:
                    if tool_name not in {t['name'] for t in tools}:raise KeyError('Tool not found: '+tool_name)
                    result=await session.call_tool(tool_name,arguments or {})
                    record['call']=self._sanitize(result.model_dump(mode='json'),spec)
                    if result.isError:raise RuntimeError('Target tool returned isError; inspect the recorded response')
                    record['level']='tool_call_tested';record['facts']['tool_call_tested']=True
                record['status']='completed'
                if ready:ready.set()
                if hold is not None:
                    # The connection owner stays on this task until explicitly stopped.
                    while not hold.is_set():await anyio.sleep(.1)
    def _record(self,spec):
        return dict(id=uuid4().hex,name=spec.name,status='running',level='launch_config_read',tools=[],tool_count=0,
            facts=dict(process_started=False,transport_connected=False,handshake_completed=False,tools_discovered=False,tool_call_tested=False),started_at=datetime.now(timezone.utc).isoformat(),connection_closed=False)
    def _sanitize(self,value,spec):
        secrets=self._credentials_if_available(spec)
        def walk(item):
            if isinstance(item,str):
                for secret in secrets:
                    if secret:item=item.replace(secret,'<redacted>')
                return item
            if isinstance(item,list):return [walk(x) for x in item]
            if isinstance(item,dict):return {walk(k):walk(v) for k,v in item.items()}
            return item
        return redact(walk(value))

    def _clean(self,text,spec):
        for value in list(spec.env.values()):
            if len(value)>=6:text=text.replace(value,'<environment-value>')
        for value in self._credentials_if_available(spec):
            if value:text=text.replace(value,'<redacted>')
        return redact(text)
    def _credentials_if_available(self,spec):
        result=[]
        for refs in (spec.env_refs,spec.headers_refs):
            try:result.extend(self._credentials(refs).values())
            except (ValueError,RuntimeError):pass
        return result
    def probe(self,spec,tool_name=None,arguments=None,timeout=20):
        self._validate(spec)
        if not 0<timeout<=300:raise ValueError('Timeout must be in (0,300]')
        record=self._record(spec)
        with tempfile.TemporaryFile(mode='w+t') as errlog:
            try:asyncio.run(self._exercise(spec,record,errlog,timeout,tool_name,arguments))
            except Exception as exc:
                record.update(status='failed',error=dict(type=type(exc).__name__,message=self._clean(str(exc),spec)))
            finally:
                errlog.seek(0);text=self._clean(errlog.read(1024*1024),spec)
                path=self.log_root/(record['id']+'.log');path.write_text(text);path.chmod(0o600)
                record.update(log_path=str(path),connection_closed=True,finished_at=datetime.now(timezone.utc).isoformat())
        return self._sanitize(record,spec)
    def start(self,server_id,spec,timeout=20):
        self._validate(spec)
        with self._lock:
            old=self._sessions.get(server_id)
            if old and old['thread'].is_alive():return dict(old['record'],owned=True)
            hold=threading.Event();ready=threading.Event();record=self._record(spec)
            def worker():
                with tempfile.TemporaryFile(mode='w+t') as errlog:
                    try:
                        # Startup has its own deadline; holding a validated session does not.
                        async def run():
                            async with self._session(spec,errlog,timeout) as session:
                                with anyio.fail_after(timeout):
                                    initialized=await session.initialize();tools=await self._discover_tools(session)
                                record.update(status='running',level='tools_discovered',tools=self._sanitize(tools,spec),tool_count=len(tools),server_info=self._sanitize(initialized.serverInfo.model_dump(mode='json'),spec))
                                record['facts'].update(process_started=spec.transport=='stdio',transport_connected=True,handshake_completed=True,tools_discovered=True)
                                ready.set()
                                while not hold.is_set():await anyio.sleep(.1)
                        asyncio.run(run());record['status']='stopped'
                    except Exception as exc:record.update(status='failed',error=dict(type=type(exc).__name__,message=self._clean(str(exc),spec)))
                    finally:
                        record['connection_closed']=True;ready.set();errlog.seek(0)
                        path=self.log_root/(record['id']+'.log');path.write_text(self._clean(errlog.read(1024*1024),spec));path.chmod(0o600);record['log_path']=str(path)
            thread=threading.Thread(target=worker,name='mcp-session-'+server_id,daemon=True)
            self._sessions[server_id]=dict(record=record,stop=hold,thread=thread);thread.start()
        ready.wait(timeout+2)
        return dict(record,owned=True)
    def status(self,server_id):
        with self._lock:
            entry=self._sessions.get(server_id)
            return dict(entry['record'],owned=True) if entry else dict(status='not_owned',owned=False)
    def stop(self,server_id):
        with self._lock:entry=self._sessions.get(server_id)
        if not entry:return dict(status='not_owned',owned=False,message='External process was not signalled')
        entry['stop'].set();entry['thread'].join(timeout=10)
        return dict(entry['record'],owned=True)
    def close(self):
        for key in list(self._sessions):self.stop(key)
