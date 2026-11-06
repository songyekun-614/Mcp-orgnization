"""Shared strict launch contracts and deployment identity."""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit,parse_qsl
import re
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ServerSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=200)
    transport:Literal['stdio','streamable-http']='stdio'
    command:str|None=None
    args:list[str]=Field(default_factory=list,max_length=256)
    cwd:str|None=None
    env:dict[str,str]=Field(default_factory=dict)
    env_refs:dict[str,str]=Field(default_factory=dict)
    headers_refs:dict[str,str]=Field(default_factory=dict)
    url:str|None=None
    enabled:bool=True
    package:str|None=None
    version:str|None=None
    installation_type:str='external'
    path:str|None=None
    description:str=''

    @model_validator(mode='after')
    def valid_launch(self):
        if self.transport=='stdio' and (not self.command or not self.command.strip()):raise ValueError('stdio requires command')
        if self.transport=='streamable-http':
            parts=urlsplit(self.url or '')
            if parts.scheme not in ('http','https') or not parts.hostname:raise ValueError('HTTP MCP requires an http(s) endpoint')
            if parts.username or parts.password:raise ValueError('URL credentials must use secure references')
            if any(re.search(r'(token|password|secret|api[_-]?key|authorization)',k,re.I) for k,v in parse_qsl(parts.query)):raise ValueError('Endpoint credentials must use secure header references')
        if any('\0' in s for s in [self.command or '',self.cwd or '',*self.args]):raise ValueError('NUL is forbidden in launch configuration')
        for value in self.args:
            if re.search(r'(?:^--?(?:api[-_]?key|token|password|secret)(?:=|$)|authorization\s*:|bearer\s+)',value,re.I):raise ValueError('Launch credentials must use environment/keyring references')
        for ref in [*self.env_refs.values(),*self.headers_refs.values()]:
            if not re.fullmatch(r'(?:env:)?[A-Za-z_][A-Za-z0-9_]*|keyring:[A-Za-z0-9_.-]{1,128}',ref):raise ValueError('Invalid environment/keyring reference')
        return self

    def identity(self):
        if self.transport=='streamable-http':
            launch={'transport':self.transport,'url':self.url,'headers_refs':self.headers_refs}
        else:
            command=self.command or ''
            if '/' in command or '\\' in command:command=os.path.normcase(os.path.abspath(os.path.expanduser(command)))
            launch=dict(transport=self.transport,command=command,args=self.args,cwd=os.path.abspath(os.path.expanduser(self.cwd)) if self.cwd else None,env=self.env,env_refs=self.env_refs)
        return hashlib.sha256(json.dumps(launch,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
