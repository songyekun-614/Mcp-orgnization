"""Official Registry JSON discovery. Publisher provenance is not a security review."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
import hashlib
import httpx


class RegistryClient:
    def __init__(self, db, base_url='https://registry.modelcontextprotocol.io', cache_dir=None):
        parts=urlsplit(base_url)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError('Registry requires an HTTPS URL without credentials')
        self.db=db;self.base_url=base_url.rstrip('/');self.cache_dir=Path(cache_dir) if cache_dir else None

    def _get(self,path,params=None,offline=False):
        url=self.base_url+'/v0.1'+path
        if params:url+='?'+urlencode(params)
        key=hashlib.sha256(url.encode()).hexdigest()
        try:cached=self.db.get_record('registry_cache',key)
        except KeyError:cached=None
        if offline:
            if cached is None:raise FileNotFoundError('No cached Registry response for this request')
            return dict(cached,cached=True,offline=True)
        try:
            with httpx.Client(timeout=20,follow_redirects=False,trust_env=False) as client:
                response=client.get(url,headers={'Accept':'application/json'});response.raise_for_status()
                if len(response.content)>8*1024*1024:raise ValueError('Registry response too large')
                body=response.json()
            if not isinstance(body,dict):raise ValueError('Invalid Registry JSON object')
        except (httpx.HTTPError,OSError) as exc:
            if cached is None:raise
            return dict(cached,cached=True,offline=True,network_error=type(exc).__name__)
        body=dict(body,source={'kind':'official-registry' if self.base_url=='https://registry.modelcontextprotocol.io' else 'custom-registry', 'url':url,'security_certified':False,'notice':'Registry publishing provenance does not certify code safety.'},cached=False,offline=False,fetched_at=datetime.now(timezone.utc).isoformat())
        self.db.save_record('registry_cache',key,body)
        return body

    def search(self,query,cursor=None,limit=20,offline=False):
        if not isinstance(query,str) or len(query.strip())<3:raise ValueError('Search requires at least three characters')
        if not isinstance(limit,int) or not 1<=limit<=100:raise ValueError('Limit must be 1–100')
        params={'search':query,'version':'latest','limit':limit}
        if cursor:params['cursor']=cursor
        return self._get('/servers',params,offline)

    def detail(self,name,version='latest',offline=False):
        if not name or not version:raise ValueError('Name and version required')
        return self._get('/servers/'+quote(name,safe='')+'/versions/'+quote(version,safe=''),offline=offline)

    def versions(self,name,offline=False):
        if not name:raise ValueError('Name required')
        return self._get('/servers/'+quote(name,safe='')+'/versions',offline=offline)
