"""Static client configuration adapters. Mutation is invoked only by approved Core plans.

Round-trip parsers never evaluate Cordis !!js expressions. Plans contain the exact
configuration text and must be held in local protected storage, never audit logs.
"""
import copy
import difflib
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

import tomlkit
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ..models import ServerSpec
from ..core.security import SENSITIVE, safe_spec

MAX_CONFIG_BYTES = 4 * 1024 * 1024
PLUGIN = '@deepseek-ai/dsh-mcp-client'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    if path.is_symlink():
        raise ValueError('Symlink configuration is not supported')
    if not path.exists():
        return b''
    with path.open('rb') as stream:
        data = stream.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise ValueError('Configuration exceeds size limit')
    return data


def _yaml():
    parser = YAML(typ='rt')
    parser.preserve_quotes = True
    parser.allow_duplicate_keys = False
    return parser


def _entries(document):
    """Yield plugin entries at any Cordis patch nesting, without evaluating tags."""
    seen = set()
    stack = [document]
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, dict):
            if item.get('name') == PLUGIN and isinstance(item.get('config'), dict):
                yield item
            stack.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            stack.extend(reversed(item))


def _reference(value):
    text = str(value)
    match = re.fullmatch(r'process\.env\.([A-Za-z_][A-Za-z_0-9]*)', text)
    if match:
        return match.group(1)
    match = re.fullmatch(r'\$\{(?:env|credential):([A-Za-z_][A-Za-z_0-9]*)\}', text)
    return match.group(1) if match else None


def _spec(name, config, client):
    env, refs = {}, {}
    for key, value in config.get('env', {}).items():
        reference = _reference(value)
        if reference:
            refs[str(key)] = reference
        elif SENSITIVE.search(str(key)):
            refs[str(key)] = str(key)
        elif not isinstance(value, TaggedScalar):
            env[str(key)] = str(value)
    # Codex env_vars forwards ambient variables under the same name.
    for item in config.get('env_vars', []):
        if isinstance(item, dict):
            if item.get('source', 'local') != 'local':
                raise ValueError('Remote environment reference requires a remote credential resolver')
            key = item.get('name')
        else:
            key = item
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
            raise ValueError('Invalid environment variable name')
        refs[key] = key
    headers = dict(config.get('env_http_headers', {})) if client == 'codex' else {}
    for key, value in config.get('headers', {}).items():
        reference = _reference(value)
        headers[str(key)] = reference or str(key).upper().replace('-', '_')
    if config.get('bearer_token_env_var'):
        headers['Authorization'] = str(config['bearer_token_env_var'])
    transport = config.get('transport', 'streamable-http' if config.get('url') else 'stdio')
    return ServerSpec(name=str(name), transport=transport, command=str(config['command']) if config.get('command') else None,
                      args=[str(a) for a in config.get('args', [])], cwd=str(config['cwd']) if config.get('cwd') else None,
                      env=env, env_refs=refs, headers_refs=headers, url=str(config['url']) if config.get('url') else None,
                      enabled=bool(config.get('enabled', True)))


class ClientConfigAdapter:
    def __init__(self, client, path):
        if client not in ('codex', 'dsh'):
            raise ValueError('Unknown MCP client')
        self.client = client
        self.path = Path(path).expanduser().absolute()
        self.issues = []

    def _load(self, text):
        if self.client == 'codex':
            return tomlkit.parse(text)
        return _yaml().load(text) if text.strip() else CommentedMap(insert=CommentedSeq())

    def read(self):
        self.issues = []
        document = self._load(_read(self.path).decode('utf-8'))
        if self.client == 'codex':
            specs = []
            for name, config in document.get('mcp_servers', {}).items():
                remote = [item.get('name') for item in config.get('env_vars', []) if isinstance(item, dict) and item.get('source', 'local') != 'local']
                if remote:
                    self.issues.append(dict(path=str(self.path), server=str(name), code='unsupported_remote_env_reference', environment_names=remote, unresolved=True))
                    continue
                specs.append(_spec(name, config, self.client))
            return specs
        specs = []
        for entry in _entries(document):
            spec = _spec(entry['config'].get('serverName', ''), entry['config'], self.client)
            spec.enabled = not bool(entry.get('disabled', False))
            specs.append(spec)
        return specs

    def plan_change(self, name, spec):
        if not name or (spec is not None and name != spec.name):
            raise ValueError('MCP target name must match specification')
        before = _read(self.path)
        document = self._load(before.decode('utf-8'))
        config = None
        if spec is not None:
            value = safe_spec(spec)
            config = {key: value[key] for key in ('command', 'args', 'cwd', 'url') if value[key] is not None}
            config['env'] = value['env']
            if self.client == 'codex':
                if any(key != ref for key, ref in value['env_refs'].items()):
                    # Official env_vars has no renaming syntax. Fail rather than emit an invalid client config.
                    raise ValueError('Codex env_vars requires matching environment variable names')
                config.update(enabled=spec.enabled, env_vars=list(value['env_refs']))
                if spec.transport == 'streamable-http':
                    for key in ('command', 'args', 'cwd', 'env', 'env_vars'):
                        config.pop(key, None)
                if spec.headers_refs:
                    config['env_http_headers'] = spec.headers_refs
            else:
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', name):
                    raise ValueError('DSH serverName must be 1-32 ASCII letters, numbers, underscores or hyphens')
                config.update(serverName=name, transport=spec.transport)
                config['env'] = CommentedMap(config['env'])
                for key, reference in value['env_refs'].items():
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', reference):
                        raise ValueError('Invalid environment reference')
                    config['env'][key] = TaggedScalar('process.env.' + reference, tag='tag:yaml.org,2002:js')
                if spec.headers_refs:
                    config['headers'] = CommentedMap({key: TaggedScalar('process.env.' + ref, tag='tag:yaml.org,2002:js') for key, ref in spec.headers_refs.items()})
        if self.client == 'codex':
            if 'mcp_servers' not in document:
                document['mcp_servers'] = tomlkit.table()
            table = document['mcp_servers']
            if config is None:
                table.pop(name, None)
            else:
                previous = table.get(name, {})
                # Retain unrelated target options (timeouts/tool allowlists).
                merged = copy.deepcopy(previous)
                for key in ('command', 'args', 'cwd', 'url', 'env', 'env_vars', 'env_http_headers', 'bearer_token_env_var', 'http_headers'):
                    merged.pop(key, None)
                merged.update(config)
                table[name] = merged
            after = tomlkit.dumps(document)
        else:
            matches = [entry for entry in _entries(document) if entry['config'].get('serverName') == name]
            if len(matches) > 1:
                raise ValueError('Ambiguous duplicate DSH serverName; choose a unique configuration file')
            if matches:
                target = matches[0]
                if config is None:
                    self._remove(document, target)
                else:
                    original = target['config']
                    for key in ('command', 'args', 'cwd', 'url', 'env', 'headers'):
                        original.pop(key, None)
                    original.update(config)
                    target['disabled'] = not spec.enabled
            elif config is not None:
                if not isinstance(document, dict):
                    if isinstance(document, list):
                        insertion = document
                    else:
                        raise ValueError('Unsupported Cordis root')
                else:
                    insertion = document.setdefault('insert', CommentedSeq())
                if not isinstance(insertion, list):
                    raise ValueError('Cordis insert must be a list')
                insertion.append(CommentedMap(name=PLUGIN, config=CommentedMap(config), disabled=not spec.enabled))
            buffer = io.StringIO(); _yaml().dump(document, buffer); after = buffer.getvalue()
        diff = ''.join(difflib.unified_diff(before.decode('utf-8').splitlines(True), after.splitlines(True), fromfile=str(self.path), tofile=str(self.path)))
        return dict(client=self.client, name=name, path=str(self.path), before_sha256=_sha(before), before_exists=self.path.exists(), after_text=after, diff=diff)

    @staticmethod
    def _remove(document, target, seen=None):
        seen = set() if seen is None else seen
        if id(document) in seen:
            return
        seen.add(id(document))
        if isinstance(document, list):
            for index, child in enumerate(document):
                if child is target:
                    del document[index]; return
                ClientConfigAdapter._remove(child, target, seen)
        elif isinstance(document, dict):
            for child in document.values():
                ClientConfigAdapter._remove(child, target, seen)

    def apply_plan(self, payload):
        if payload.get('client') != self.client or Path(payload['path']).absolute() != self.path:
            raise ValueError('Plan target mismatch')
        return apply_patch_plan(payload)


def _atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            if hasattr(os, 'fchmod'):
                os.fchmod(stream.fileno(), mode)
            else:
                os.chmod(temporary, mode)
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _has_literal_secret(document):
    if isinstance(document,dict):
        for key,value in document.items():
            if SENSITIVE.search(str(key)) and isinstance(value,str) and value and not isinstance(value,TaggedScalar) and _reference(value) is None:
                return True
            if _has_literal_secret(value):return True
    elif isinstance(document,list):return any(_has_literal_secret(v) for v in document)
    return False


def _backup_key():
    """Encryption keys stay in the native OS vault; never fall back to a key file."""
    import keyring
    from cryptography.fernet import Fernet
    backend=keyring.get_keyring()
    if type(backend).__module__ not in ('keyring.backends.SecretService','keyring.backends.Windows','keyring.backends.macOS'):
        raise PermissionError('A native secure keyring is required for backups containing literal credentials')
    try:
        key=keyring.get_password('mcp-manager','configuration-backups')
        if key is None:
            key=Fernet.generate_key().decode('ascii');keyring.set_password('mcp-manager','configuration-backups',key)
        return key.encode('ascii')
    except Exception as exc:raise PermissionError('Unlock the OS keyring before changing configurations containing literal credentials') from exc


def apply_patch_plan(payload):
    """Apply an already approved exact payload; this function grants no authorization."""
    path = Path(payload['path']).expanduser().absolute()
    before = _read(path)
    if _sha(before) != payload['before_sha256'] or path.exists() != payload.get('before_exists', bool(before)):
        raise ValueError('Configuration changed since preview')
    after = payload['after_text'].encode('utf-8')
    # Reject malformed output before writing anything.
    adapter = ClientConfigAdapter(payload['client'], path)
    before_doc = adapter._load(before.decode('utf-8'))
    after_doc = adapter._load(payload['after_text'])
    def unrelated(document):
        document = copy.deepcopy(document)
        if payload['client'] == 'codex':
            table = document.get('mcp_servers', {})
            table.pop(payload['name'], None)
            if not table:
                document.pop('mcp_servers', None)
        else:
            for entry in list(_entries(document)):
                if entry['config'].get('serverName') == payload['name']:
                    adapter._remove(document, entry)
            if isinstance(document, dict) and document.get('insert') == []:
                document.pop('insert', None)
        def plain(value):
            if isinstance(value, TaggedScalar):
                return ('tagged', str(value.tag), str(value))
            if isinstance(value, dict):
                return {str(key): plain(item) for key, item in value.items()}
            if isinstance(value, list):
                return [plain(item) for item in value]
            return value
        return plain(document)
    if unrelated(before_doc) != unrelated(after_doc):
        raise ValueError('Plan changes settings outside the target MCP')
    backup = path.with_name(path.name + '.mcp-manager-' + uuid4().hex + '.bak')
    encrypted=_has_literal_secret(before_doc)
    backup_bytes=before
    if encrypted:
        from cryptography.fernet import Fernet
        backup_bytes=b'MCPMGR-ENC1\n'+Fernet(_backup_key()).encrypt(before)
    _atomic_write(backup, backup_bytes)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    try:
        # Recheck after backup to reduce accidental concurrent writer races.
        if _sha(_read(path)) != payload['before_sha256']:
            raise ValueError('Configuration changed during backup')
        _atomic_write(path, after, mode)
    except BaseException:
        # Atomic replacement never leaves a partial file; do not overwrite another writer.
        raise
    return dict(path=str(path), backup_path=str(backup), backup_encrypted=encrypted, before_sha256=_sha(before), after_sha256=_sha(after), before_exists=payload.get('before_exists', bool(before)))


def restore_backup(record):
    path = Path(record['path']).absolute(); backup = Path(record['backup_path']).absolute()
    current = _read(path)
    if record.get('backup_encrypted'):
        from cryptography.fernet import Fernet
        with backup.open('rb') as stream:encoded=stream.read(6*1024*1024+1)
        if len(encoded)>6*1024*1024 or not encoded.startswith(b'MCPMGR-ENC1\n'):raise ValueError('Invalid encrypted backup')
        original=Fernet(_backup_key()).decrypt(encoded.split(b'\n',1)[1])
    else:original = _read(backup)
    if _sha(current) != record['after_sha256'] or _sha(original) != record['before_sha256']:
        raise ValueError('Restore hash mismatch')
    if record.get('before_exists', True):
        _atomic_write(path, original, path.stat().st_mode & 0o777)
    else:
        path.unlink()
    return dict(path=str(path), restored=True)
