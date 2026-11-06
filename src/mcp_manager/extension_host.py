"""Startup-only MCP runtime for locally approved generic extension adapters.

This is a trusted-code loader, not a runtime sandbox. Tests use OS isolation;
installation approval grants this dedicated server process's OS privileges.
"""
import argparse
import asyncio
from contextlib import redirect_stdout
import inspect
import json
from pathlib import Path
import sys

from jsonschema.validators import validator_for
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, ToolAnnotations

from .core.extensions import _read, _sha, _filename, _plain, _NAME, _TOOL


class _Registry:
    def __init__(self, declarations):
        self.declarations={t['name']:t for t in declarations};self.tools={}

    def tool(self, name, description, annotations=None):
        if name not in self.declarations or name in self.tools:raise ValueError('Unapproved or duplicate tool: '+str(name))
        if not isinstance(description,str) or not 1<=len(description)<=2000:raise ValueError('Tool description required')
        def decorate(function):
            if not inspect.iscoroutinefunction(function):raise ValueError('Extension tools must be typed async functions')
            signature=inspect.signature(function)
            declaration=self.declarations[name]
            schema=declaration['input_schema']
            if not isinstance(schema,dict) or schema.get('type','object')!='object':raise ValueError('MCP input schema must describe an object')
            schema=dict(schema,type='object',additionalProperties=False)
            parameters=signature.parameters
            for parameter in parameters.values():
                if parameter.kind not in (inspect.Parameter.POSITIONAL_OR_KEYWORD,inspect.Parameter.KEYWORD_ONLY) or parameter.annotation is inspect.Parameter.empty:
                    raise ValueError('Tool parameters must be named and typed; no variadic arguments')
            if set(parameters)!=set(schema.get('properties',{})):raise ValueError('Tool signature differs from approved input schema')
            required={p.name for p in parameters.values() if p.default is inspect.Parameter.empty}
            if required!=set(schema.get('required',[])):raise ValueError('Required arguments differ from approved schema')
            if signature.return_annotation is inspect.Signature.empty:raise ValueError('Tool return annotation required')
            output=declaration['output_schema']
            if not isinstance(output,dict) or output.get('type','object')!='object':raise ValueError('Generic outputs must be JSON objects')
            validator_for(schema).check_schema(schema);validator_for(output).check_schema(output)
            definition=Tool(name=name,description=description,inputSchema=schema,outputSchema=output,
                            annotations=ToolAnnotations(**annotations) if isinstance(annotations,dict) else annotations)
            self.tools[name]=(definition,function)
            return function
        return decorate


class ExtensionHost:
    def __init__(self, registry):
        self.registry=_plain(Path(registry).absolute(),Path(registry).absolute().parent)
        root=self.registry.parent;raw=_read(self.registry,root)
        document=json.loads(raw)
        if not isinstance(document,dict) or set(document)!={'schema_version','extensions'} or document['schema_version']!=1:raise ValueError('Invalid registry schema')
        if not isinstance(document['extensions'],list) or len(document['extensions'])>32:raise ValueError('Invalid registry extensions')
        self.hashes={self.registry.name:_sha(raw)};self.tools={};packages=[];ids=set();names=set()
        # Validate every module, manifest and evidence before executing any code.
        for entry in document['extensions']:
            if not isinstance(entry,dict) or set(entry)!={'id','module','sha256','tools','tests','manifest'}:raise ValueError('Generic entry requires hashed manifest and host evidence')
            identity=entry['id']
            if not isinstance(identity,str) or not _NAME.fullmatch(identity) or identity in ids:raise ValueError('Invalid/duplicate extension ID')
            ids.add(identity)
            module=self._verified(entry['module'],entry['sha256'])
            manifest_proof=entry['manifest']
            if not isinstance(manifest_proof,dict) or set(manifest_proof)!={'file','sha256'}:raise ValueError('Invalid manifest proof')
            manifest=json.loads(self._verified(manifest_proof['file'],manifest_proof['sha256']))
            if manifest.get('target')!='generic' or manifest.get('id')!=identity or set(manifest.get('permissions',[]))-{'read_workspace','write_workspace'}:raise ValueError('Generic host policy mismatch')
            declarations=manifest.get('tools')
            if not isinstance(declarations,list) or not 1<=len(declarations)<=32:raise ValueError('Invalid manifest tools')
            tool_names=[t.get('name') for t in declarations]
            if tool_names!=entry['tools'] or len(set(tool_names))!=len(tool_names) or any(not isinstance(n,str) or not _TOOL.fullmatch(n) or n in names for n in tool_names):raise ValueError('Unapproved/conflicting tools')
            names.update(tool_names)
            tests=entry['tests']
            if not isinstance(tests,dict) or set(tests)!={'host'}:raise ValueError('Generic policy requires host evidence only')
            for level,proof in tests.items():
                if not isinstance(proof,dict) or set(proof)!={'evidence','sha256'}:raise ValueError('Invalid test evidence proof')
                report=json.loads(self._verified(proof['evidence'],proof['sha256']))
                if report.get('status')!='passed' or report.get('level')!=level or report.get('module_sha256')!=entry['sha256'] or report.get('sandbox') not in ('linux-bwrap','docker') or report.get('returncode')!=0:raise ValueError('Host test has not passed in OS isolation')
            packages.append((entry,module,manifest))
        for entry,module,manifest in packages:
            scope={'__name__':'approved_extension_'+entry['id'],'__file__':str(root/entry['module']),'__package__':None}
            with redirect_stdout(sys.stderr):
                exec(compile(module,scope['__file__'],'exec'),scope)
                register=scope.get('register')
                if not callable(register) or inspect.iscoroutinefunction(register):raise ValueError('Require sync register(registry)')
                facade=_Registry(manifest['tools']);register(facade)
            if set(facade.tools)!=set(entry['tools']):raise ValueError('Module did not register exactly approved tools')
            self.tools.update(facade.tools)
        self.server=Server('MCP Manager Extension Host',version='2.0.0');self._call_lock=asyncio.Lock()
        @self.server.list_tools()
        async def list_tools():
            self.verify_current()
            return [item[0] for item in self.tools.values()]
        @self.server.call_tool()
        async def call_tool(name,arguments):
            async with self._call_lock:
                self.verify_current()
                if name not in self.tools:raise ValueError('Unknown approved tool')
                definition,function=self.tools[name]
                validator_for(definition.inputSchema)(definition.inputSchema).validate(arguments)
                with redirect_stdout(sys.stderr):result=await function(**arguments)
                if not isinstance(result,dict):raise ValueError('Extension tool must return a JSON object')
                validator_for(definition.outputSchema)(definition.outputSchema).validate(result)
                # Also reject non-JSON objects and NaN before MCP serialization.
                json.dumps(result,allow_nan=False)
                return result

    def _verified(self,name,digest):
        name=_filename(name);data=_read(self.registry.parent/name,self.registry.parent)
        if _sha(data)!=digest:raise ValueError('Approved artifact digest mismatch: '+name)
        self.hashes[name]=digest
        return data

    def verify_current(self):
        for name,expected in self.hashes.items():
            if _sha(_read(self.registry.parent/name,self.registry.parent))!=expected:raise PermissionError('Approval registry or extension changed; restart required')

    async def run(self):
        async with stdio_server() as (read,write):
            await self.server.run(read,write,self.server.create_initialization_options())


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--registry',required=True)
    options=parser.parse_args()
    asyncio.run(ExtensionHost(options.registry).run())


if __name__=='__main__':main()
