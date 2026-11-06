"""Bounded, read-only discovery with explicit evidence and no project execution."""
import json
import ast
import importlib.metadata
import os
from pathlib import Path
import re
import shutil
import shlex
import time
import sys
import tomllib
from ruamel.yaml.error import YAMLError
from ..adapters.clients import ClientConfigAdapter, MAX_CONFIG_BYTES
from ..models import ServerSpec
from .security import safe_spec

IGNORED = {'.git', '.hg', '.svn', 'node_modules', '.venv', 'venv', '__pycache__', '.cache', 'dist', 'build', '.tox'}

def _server_entry_evidence(root,target):
    """Inspect declared Python entry source without importing or executing it."""
    module=str(target).split(':',1)[0].replace('.', '/')
    root=Path(root)
    for path in (root/(module+'.py'),root/module/'__init__.py',root/'src'/(module+'.py'),root/'src'/module/'__init__.py'):
        if path.is_symlink() or not path.is_file():continue
        try:
            if path.stat().st_size>MAX_CONFIG_BYTES:continue
            tree=ast.parse(path.read_text(encoding='utf-8'))
        except (OSError,SyntaxError,UnicodeError):continue
        factories=set();owners=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom) and node.module and (node.module.startswith('mcp') or node.module=='fastmcp'):
                factories.update(a.asname or a.name for a in node.names if a.name in ('FastMCP','Server'))
        for node in ast.walk(tree):
            if isinstance(node,ast.ClassDef) and any(isinstance(base,ast.Name) and base.id in factories for base in node.bases):factories.add(node.name)
            if isinstance(node,ast.Assign) and isinstance(node.value,ast.Call) and isinstance(node.value.func,ast.Name) and node.value.func.id in factories:
                owners.update(t.id for t in node.targets if isinstance(t,ast.Name))
        if any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id in owners and n.func.attr=='run' for n in ast.walk(tree)):return True
        if any(str(target).endswith(':'+owner+'.run') for owner in owners):return True
    return False



class DiscoveryEngine:
    def __init__(self, home=None, environ=None, metadata_paths=None, max_distributions=500, service_paths=None):
        self.home = Path(home) if home is not None else Path.home()
        self.environ = dict(os.environ if environ is None else environ)
        self.metadata_paths = list(metadata_paths) if metadata_paths is not None else ([path for path in sys.path if path and Path(path).name in ('site-packages', 'dist-packages')] if self.home.resolve() == Path.home().resolve() else [])
        self.max_distributions = max(0, int(max_distributions))
        self.service_paths = list(service_paths) if service_paths is not None else [self.home / '.config/systemd/user', self.home / '.local/share/systemd/user']

    def discover(self, roots=None, mode='quick', max_depth=4, max_files=10000, max_seconds=10,
                 authorized=False, cancel=None, pause=None, progress=None, ignore=None, follow_symlinks=False):
        if mode not in ('quick', 'standard', 'deep'):
            raise ValueError('Unknown discovery mode')
        if mode == 'deep' and not authorized:
            raise PermissionError('Deep scan requires an approved local Core plan')
        if max_depth < 0 or max_files < 1 or max_seconds <= 0:
            raise ValueError('Scan bounds must be positive')
        started = time.monotonic()
        result = dict(servers=[], candidates=[], issues=[], stats=dict(files=0, directories=0, cancelled=False, limited=False, elapsed_seconds=0))
        by_identity = {}; visited = set(); configs = set()
        ignored = IGNORED | set(ignore or [])

        def issue(path, code):
            result['issues'].append(dict(path=str(path), code=code))

        def proceed():
            while pause is not None and pause.is_set():
                if cancel is not None and cancel.wait(0.05):
                    result['stats']['cancelled'] = True; return False
                if cancel is None:
                    time.sleep(0.05)
                if time.monotonic() - started >= max_seconds:
                    result['stats']['limited'] = True; return False
            if cancel is not None and cancel.is_set():
                result['stats']['cancelled'] = True; return False
            if time.monotonic() - started >= max_seconds or result['stats']['files'] >= max_files:
                result['stats']['limited'] = True; return False
            return True

        def add(spec, source):
            spec = ServerSpec.model_validate(safe_spec(spec))
            identity = spec.identity()
            if identity in by_identity:
                entry = by_identity[identity]
                if source not in entry['sources']:
                    entry['sources'].append(source)
            else:
                entry = dict(spec=spec.model_dump(), source=source, sources=[source], level='launch_config_read')
                by_identity[identity] = entry; result['servers'].append(entry)

        def client(path, kind, scope):
            key = str(path.absolute())
            if key in configs or not path.exists() or not proceed():
                return
            configs.add(key); result['stats']['files'] += 1
            try:
                adapter = ClientConfigAdapter(kind, path)
                specs = adapter.read()
                result['issues'].extend(adapter.issues)
                for unresolved in adapter.issues:
                    result['candidates'].append(dict(path=str(path), name=unresolved['server'], level='launch_config_read', evidence=['client_configuration', 'unresolved_environment_reference']))
                for spec in specs:
                    add(spec, dict(kind=kind, path=str(path), scope=scope))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, YAMLError) as error:
                # Exception text may contain literal credentials from malformed input.
                issue(path, 'configuration_read_failed:' + type(error).__name__)

        codex_home = Path(self.environ.get('CODEX_HOME', str(self.home / '.codex'))).expanduser()
        dsh_home = Path(self.environ.get('DSH_HOME', self.environ.get('DSH_CONFIG_DIR', str(self.home / '.dsh')))).expanduser()
        client(codex_home / 'config.toml', 'codex', 'user')
        for name in ('cordis.yml', 'cordis.patch.yml'):
            client(dsh_home / name, 'dsh', 'user')
        profiles = dsh_home / 'profiles'
        if profiles.is_dir() and not profiles.is_symlink():
            try:
                with os.scandir(profiles) as entries:
                    for profile in entries:
                        if not proceed(): break
                        if profile.is_dir(follow_symlinks=False):
                            for name in ('cordis.yml', 'cordis.patch.yml'):
                                client(Path(profile.path) / name, 'dsh', 'profile')
            except OSError:
                issue(profiles, 'directory_read_failed')

        def candidate(path, evidence, **extra):
            result['candidates'].append(dict(path=str(path), level='project_present', evidence=evidence, **extra))

        def text(path):
            with path.open('rb') as stream:
                data = stream.read(MAX_CONFIG_BYTES + 1)
            if len(data) > MAX_CONFIG_BYTES:
                raise ValueError('File too large')
            return data.decode('utf-8')

        def inspect(path):
            if path.is_symlink(): return
            name = path.name
            try:
                if name == 'mcp-server.json':
                    raw = json.loads(text(path))
                    items = raw if isinstance(raw, list) else [raw]
                    for item in items:
                        add(ServerSpec.model_validate(item), dict(kind='registration', path=str(path), scope='project'))
                elif name == 'pyproject.toml':
                    document = tomllib.loads(text(path)); project = document.get('project', {})
                    dependencies = project.get('dependencies', [])
                    dependencies += document.get('tool', {}).get('poetry', {}).get('dependencies', {}).keys()
                    uses_mcp = any(re.match(r'^(?:mcp|fastmcp)(?:\b|\[)', str(dep), re.I) for dep in dependencies)
                    if uses_mcp:
                        # Dependency and entry point are evidence, but installation/executable existence is not assumed.
                        candidate(path.parent, ['python_mcp_dependency'], entry_points=dict(project.get('scripts', {})))
                        for script, target in project.get('scripts', {}).items():
                            if not re.fullmatch(r'[A-Za-z0-9_.-]+', script) or ':' not in str(target) or not _server_entry_evidence(path.parent,target):
                                continue
                            for relative in ('.venv/bin', 'venv/bin', '.venv/Scripts', 'venv/Scripts'):
                                executable = path.parent / relative / (script + '.exe' if relative.endswith('Scripts') else script)
                                if executable.is_file() and not executable.is_symlink() and (os.name == 'nt' or os.access(executable, os.X_OK)):
                                    add(ServerSpec(name=script, command=str(executable), cwd=str(path.parent)),
                                        dict(kind='python_project', path=str(path), scope='project', evidence=['python_mcp_dependency', 'installed_console_entry_point']))
                                    break
                elif name == 'package.json':
                    document = json.loads(text(path))
                    dependencies = {**document.get('dependencies', {}), **document.get('devDependencies', {})}
                    if '@modelcontextprotocol/sdk' in dependencies or 'fastmcp' in dependencies:
                        candidate(path.parent, ['node_mcp_sdk_dependency'], commands=document.get('bin', {}), scripts=document.get('scripts', {}))
                        node = shutil.which('node', path=self.environ.get('PATH', os.defpath))
                        bins = document.get('bin', {})
                        if isinstance(bins, str):
                            bins = {document.get('name', path.parent.name): bins}
                        if node and isinstance(bins, dict):
                            for command, relative in bins.items():
                                if not isinstance(relative, str):
                                    continue
                                entry = path.parent / relative
                                if entry.suffix in ('.js', '.mjs', '.cjs') and entry.is_file() and not entry.is_symlink() and entry.resolve().is_relative_to(path.parent.resolve()):
                                    add(ServerSpec(name=str(command), command=node, args=[str(entry)], cwd=str(path.parent)),
                                        dict(kind='node_project', path=str(path), scope='project', evidence=['node_mcp_sdk_dependency', 'existing_bin_entry_point']))
                elif name == 'config.toml' and path.parent.name == '.codex':
                    client(path, 'codex', 'project')
                elif name in ('cordis.yml', 'cordis.patch.yml'):
                    client(path, 'dsh', 'project')
            except (OSError, ValueError, TypeError, KeyError, AttributeError, YAMLError) as error:
                issue(path, 'project_read_failed:' + type(error).__name__)

        scan_roots = list(roots or [])
        if mode != 'quick' and not scan_roots:
            scan_roots = [str(self.home / name) for name in ('Projects', 'projects', 'code') if (self.home / name).is_dir()]
        for root in scan_roots:
            # A root is an explicit user selection; its symlink target is the scan boundary.
            root = Path(root).expanduser().resolve()
            if not root.exists():
                issue(root, 'root_missing'); continue
            client(root / '.codex/config.toml', 'codex', 'project')
            # Quick inspects root registrations/manifests plus configured clients, without recursion.
            stack = [(root, 0)]
            while stack and proceed():
                directory, depth = stack.pop()
                try:
                    resolved = directory.resolve()
                    stat = directory.stat(); identity = (stat.st_dev, stat.st_ino)
                    if identity in visited: continue
                    if directory.is_symlink() and not follow_symlinks: continue
                    if not resolved.is_relative_to(root.resolve()):
                        issue(directory, 'symlink_outside_root'); continue
                    visited.add(identity); result['stats']['directories'] += 1
                    with os.scandir(directory) as entries:
                        for entry in entries:
                            if not proceed(): break
                            if entry.name in ignored: continue
                            path = Path(entry.path)
                            if entry.is_symlink() and not follow_symlinks: continue
                            if entry.is_dir(follow_symlinks=follow_symlinks):
                                if mode != 'quick' and depth < max_depth:
                                    stack.append((path, depth + 1))
                            elif entry.is_file(follow_symlinks=False):
                                result['stats']['files'] += 1
                                inspect(path)
                                if progress:
                                    progress(dict(result['stats'], path=str(path)))
                except OSError:
                    issue(directory, 'directory_read_failed')
        # Inspect installed metadata, never import the installed package or invoke its entry point.
        result['stats']['distributions'] = 0
        for site_name in self.metadata_paths:
            if not proceed():
                break
            site = Path(site_name).expanduser().resolve()
            if not site.is_dir():
                continue
            try:
                with os.scandir(site) as distributions:
                    for entry in distributions:
                        if not proceed():
                            break
                        if not entry.name.endswith('.dist-info') or not entry.is_dir(follow_symlinks=False):
                            continue
                        if result['stats']['distributions'] >= self.max_distributions:
                            result['stats']['limited'] = True
                            break
                        result['stats']['distributions'] += 1
                        result['stats']['files'] += 1
                        metadata_path = Path(entry.path)
                        try:
                            # Enforce the same byte bound before importlib's metadata reader opens files.
                            text(metadata_path / 'METADATA')
                            entry_points = metadata_path / 'entry_points.txt'
                            if entry_points.exists():
                                text(entry_points)
                            distribution = importlib.metadata.PathDistribution(metadata_path)
                            dependencies = distribution.requires or []
                            if not any(re.match(r'^(?:mcp|fastmcp)(?:\b|\[)', dependency, re.I) for dependency in dependencies):
                                continue
                            scripts = [ep for ep in distribution.entry_points if ep.group == 'console_scripts']
                            candidate(metadata_path, ['installed_python_distribution', 'python_mcp_dependency'],
                                      distribution=distribution.metadata.get('Name', metadata_path.name), entry_points={ep.name: ep.value for ep in scripts})
                            for ep in scripts:
                                if not _server_entry_evidence(site,ep.value):continue
                                if not re.fullmatch(r'[A-Za-z0-9_.-]+', ep.name) or ':' not in ep.value:
                                    continue
                                prefixes = list(site.parents)[:3]
                                options = [prefix / folder / (ep.name + suffix) for prefix in prefixes
                                           for folder, suffix in (('bin', ''), ('Scripts', '.exe'))]
                                for command in options:
                                    if command.is_file() and not command.is_symlink() and (os.name == 'nt' or os.access(command, os.X_OK)):
                                        add(ServerSpec(name=ep.name, command=str(command), package=distribution.metadata.get('Name'), version=distribution.version),
                                            dict(kind='python_distribution', path=str(metadata_path), scope='environment', evidence=['python_mcp_dependency', 'installed_console_entry_point']))
                                        break
                        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                            issue(metadata_path, 'distribution_read_failed:' + type(error).__name__)
            except OSError:
                issue(site, 'metadata_directory_read_failed')
        # User service definitions provide launch evidence only; they establish no MCP protocol fact.
        for service_directory in self.service_paths:
            if not proceed():
                break
            service_directory = Path(service_directory).expanduser()
            if not service_directory.is_dir() or service_directory.is_symlink():
                continue
            try:
                with os.scandir(service_directory) as services:
                    for entry in services:
                        if not proceed():
                            break
                        if not entry.name.endswith('.service') or not entry.is_file(follow_symlinks=False):
                            continue
                        result['stats']['files'] += 1
                        service_path = Path(entry.path)
                        try:
                            for line in text(service_path).splitlines():
                                if line.strip().startswith('ExecStart='):
                                    launch = line.split('=', 1)[1].strip()
                                    if not launch:
                                        continue
                                    parts = shlex.split(launch)
                                    candidate(service_path, ['user_service_execstart'], executable=parts[0], argument_count=len(parts) - 1, mcp_status='unverified')
                        except (OSError, ValueError) as error:
                            issue(service_path, 'service_read_failed:' + type(error).__name__)
            except OSError:
                issue(service_directory, 'service_directory_read_failed')
        result['stats']['elapsed_seconds'] = round(time.monotonic() - started, 4)
        return result
