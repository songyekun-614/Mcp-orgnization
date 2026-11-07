"""A backend worker owns its SDK cancellation scopes for their entire lifetime."""
import asyncio
import os
from datetime import timedelta
import tempfile
import time
import anyio


class GatewayConnection:
    def __init__(self, runtime, spec, timeout, validate):
        self.runtime=runtime;self.spec=spec;self.timeout=timeout;self.validate=validate
        self.queue=asyncio.Queue(maxsize=32);self.task=None;self.catalog={};self.updated=0;self.instructions='';self.secrets=()

    async def request(self, operation, *arguments):
        self.validate()
        if self.task is None or self.task.done():
            self.task=asyncio.create_task(self._run())
        future=asyncio.get_running_loop().create_future()
        try:self.queue.put_nowait((operation,arguments,future))
        except asyncio.QueueFull:raise RuntimeError('Backend request queue is full')
        return await future

    async def _run(self):
        current=None
        with tempfile.TemporaryFile(mode='w+t') as log:
            try:
                credentials=(self.runtime._credentials(self.spec.env_refs),self.runtime._credentials(self.spec.headers_refs))
                self.secrets=tuple(value for group in credentials for value in group.values())
                async with self.runtime._session(self.spec,log,self.timeout,credentials=credentials) as session:
                    with anyio.fail_after(min(20,self.timeout)):
                        initialized=await session.initialize()
                        self.instructions=self.sanitize(initialized.instructions or '')
                        await self._refresh(session)
                    while True:
                        operation,args,current=await self.queue.get()
                        if current.cancelled():current=None;continue
                        try:
                            self.validate()
                            with anyio.fail_after(self.timeout):
                                if operation=='catalog':
                                    if time.monotonic()-self.updated>30:await self._refresh(session)
                                    value=self.catalog
                                elif operation=='call':
                                    await self._refresh(session)
                                    name,arguments,check=args
                                    check(self.catalog)
                                    value=await session.call_tool(name,arguments,read_timeout_seconds=timedelta(seconds=self.timeout))
                                else:raise ValueError('Unknown gateway operation')
                        except (ValueError,KeyError,PermissionError) as exc:
                            if not current.done():current.set_exception(exc)
                            current=None
                            continue
                        if not current.done():current.set_result(value)
                        current=None
            except BaseException as exc:
                error=RuntimeError('Backend connection ended: '+self.runtime._clean(type(exc).__name__+': '+str(exc),self.spec,self.secrets)[:1200])
                if current is not None and not current.done():current.set_exception(error)
                while not self.queue.empty():
                    _,_,future=self.queue.get_nowait()
                    if not future.done():future.set_exception(error)
            finally:
                self.catalog={};self.updated=0
                log.seek(0)
                from uuid import uuid4
                path=self.runtime.log_root/('gateway-'+uuid4().hex+'.log')
                descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(descriptor,'w') as stream:stream.write(self.runtime._clean(log.read(65536),self.spec,self.secrets))

    def sanitize(self,value):
        return self.runtime._sanitize(value,self.spec,self.secrets)

    async def _refresh(self,session):
        tools=await self.runtime._discover_tools(session)
        safe=self.sanitize(tools)
        names=[tool['name'] for tool in safe]
        if len(names)!=len(set(names)):raise ValueError('Backend advertised duplicate tool names')
        self.catalog={tool['name']:tool for tool in safe};self.updated=time.monotonic()

    async def close(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await self.task
