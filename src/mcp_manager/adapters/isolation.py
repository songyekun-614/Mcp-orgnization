"""OS sandbox adapters. No virtual-environment fallback for untrusted tests."""
import os
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from uuid import uuid4
from ..core.security import redact,SENSITIVE


class IsolationAdapter:
    def __init__(self,root,read,write,sha):
        self.root,self.read,self.write,self.sha=root,read,write,sha

    def run(self, record, sandbox_image=None):
        result=self._bwrap_host(record)
        if result['status']=='blocked' and sandbox_image:
            container=self._docker_host(record,sandbox_image)
            container['bwrap_unavailable_reason']=result.get('stderr',result.get('reason'))
            return container
        return result

    def _bwrap_host(self, record):
        bwrap=shutil.which('bwrap'); prlimit=shutil.which('prlimit')
        if sys.platform!='linux' or not bwrap or not prlimit:
            return dict(status='blocked',reason='Linux bubblewrap and prlimit required; no weaker sandbox fallback')
        run=self.root/'runs'/uuid4().hex; run.mkdir(mode=0o700)
        work=run/'work';work.mkdir(mode=0o700)
        # Private test copy: Agent cannot race mutation of the approved snapshot.
        source=run/'source';source.mkdir(mode=0o700)
        for name, expected in record['files'].items():
            data = self.read(Path(record['snapshot'])/name, self.root)
            if self.sha(data) != expected:raise PermissionError('Snapshot changed during test copy')
            self.write(source/name, data)
        env=dict(PATH='/usr/bin:/bin',HOME='/work',TMPDIR='/work',LANG='C.UTF-8',PYTHONHASHSEED='0')
        command=[prlimit,'--cpu=10','--as=1073741824','--fsize=1048576','--nofile=128','--nproc=64','--',
                 bwrap,'--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL']
        for directory in ('/usr','/lib','/lib64','/bin','/sbin'):
            if Path(directory).exists():command += ['--ro-bind',directory,directory]
        command += ['--ro-bind',str(Path(sys.prefix).resolve()),'/runtime','--ro-bind',str(source),'/src',
                    '--bind',str(work),'/work','--dir','/tmp','--proc','/proc','--dev','/dev','--chdir','/work',
                    '--remount-ro','/','--','/runtime/bin/python','-I','-B']
        try:
            probe=self._run(command+['-c','pass'],env)
            if probe['returncode']!=0:return dict(status='blocked',reason='OS sandbox unavailable or rejected',sandbox='linux-bwrap',**probe)
            # Import only snapshot modules and execute Agent's declared host test.
            script="import sys,runpy;sys.path.insert(0,'/src');runpy.run_path('/src/test_host.py',run_name='__main__')"
            result=self._run(command+['-c',script],env)
            return dict(status='passed' if result['returncode']==0 else 'failed',sandbox='linux-bwrap',**result)
        except (OSError,subprocess.SubprocessError) as exc:
            return dict(status='blocked',reason=redact(str(exc)))
        finally:shutil.rmtree(run)

    def _docker_host(self, record, image):
        docker=shutil.which('docker');prlimit=shutil.which('prlimit')
        if sys.platform!='linux' or not docker or not prlimit:
            return dict(status='blocked',reason='Configured Linux Docker sandbox unavailable')
        if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):raise ValueError('Immutable local Docker image ID required')
        env=dict(PATH='/usr/bin:/bin',HOME='/nonexistent',LANG='C.UTF-8')
        inspect_result=self._run([docker,'image','inspect',image,'--format','{{json .}}'],env)
        if inspect_result['returncode']!=0:return dict(status='blocked',reason='Configured immutable image is not locally available; no pull attempted',**inspect_result)
        try:metadata=json.loads(inspect_result['stdout'])
        except (ValueError,TypeError):return dict(status='blocked',reason='Unable to verify local image inspection output')
        if metadata.get('Id')!=image:return dict(status='blocked',reason='Local Docker image ID mismatch')
        run=self.root/'runs'/uuid4().hex;run.mkdir(mode=0o700)
        source=run/'source';source.mkdir(mode=0o755)
        for name,expected in record['files'].items():
            data=self.read(Path(record['snapshot'])/name,self.root)
            if self.sha(data)!=expected:raise PermissionError('Snapshot changed during container copy')
            self.write(source/name,data);(source/name).chmod(0o444)
        if ',' in str(source):
            shutil.rmtree(run)
            raise ValueError('Docker source path may not contain a comma')
        prefix=[prlimit,'--fsize=1048576','--cpu=10','--',docker,'run','--rm','--pull=never',
                '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
                '--pids-limit','64','--memory','256m','--memory-swap','256m','--cpus','1','--user','65534:65534',
                '--ulimit','cpu=10:10','--ulimit','nofile=128:128','--ulimit','fsize=1048576:1048576',
                '--log-driver','none','--workdir','/work','--mount','type=bind,source='+str(source)+',destination=/src,readonly',
                '--tmpfs','/work:rw,noexec,nosuid,size=16777216,mode=1777','--tmpfs','/tmp:ro,noexec,nosuid,size=1048576']
        # Image-declared volumes must not create writable anonymous mounts.
        for volume in metadata.get('Config',{}).get('Volumes',{}) or {}:
            if volume in ('/work','/src','/tmp') or not volume.startswith('/') or ':' in volume or ',' in volume:
                shutil.rmtree(run)
                return dict(status='blocked',reason='Unsupported image volume declaration')
            prefix += ['--tmpfs',volume+':ro,noexec,nosuid,size=1048576']
        for item in metadata.get('Config',{}).get('Env',[]) or []:
            key=item.split('=',1)[0]
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',key):prefix+=['--env',key+'=']
        prefix+=['--env','PATH=/usr/local/bin:/usr/bin:/bin','--env','HOME=/work','--env','TMPDIR=/work','--env','LANG=C.UTF-8']
        def run_container(interpreter,script):
            name='mcp-extension-test-'+uuid4().hex
            try:
                return self._run(prefix+['--name',name,'--entrypoint',interpreter,image,'-I','-B','-c',script],env)
            finally:
                try:self._run([docker,'rm','--force',name],env)
                except (OSError,subprocess.SubprocessError):pass
        try:
            errors=[]
            for interpreter in ('python3','/usr/local/searxng/.venv/bin/python'):
                probe=run_container(interpreter,'pass')
                if probe['returncode']==0:break
                errors.append(probe.get('stderr',''))
            else:return dict(status='blocked',reason='Container sandbox/interpreter unavailable',stderr=redact('\n'.join(errors)))
            script="import sys,runpy;sys.path.insert(0,'/src');runpy.run_path('/src/test_host.py',run_name='__main__')"
            result=run_container(interpreter,script)
            return dict(status='passed' if result['returncode']==0 else 'failed',sandbox='docker',image_sha256=image,interpreter=interpreter,**result)
        except (OSError,subprocess.SubprocessError,ValueError) as exc:
            return dict(status='blocked',reason=redact(str(exc)))
        finally:shutil.rmtree(run)

    @staticmethod
    def _run(command, env):
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
            timed_out=False
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                timed_out=True
                if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
                else:process.kill()
                process.wait()
            stdout.seek(0);stderr.seek(0)
            def clean(stream):
                value=stream.read(65536).decode('utf-8','replace')
                # Strip known ambient secrets even if an error unexpectedly echoes one.
                for key,secret in os.environ.items():
                    if SENSITIVE.search(key) and len(secret)>=4:value=value.replace(secret,'<redacted>')
                return redact(value)
            return dict(returncode=process.returncode,stdout=clean(stdout),stderr=clean(stderr),timed_out=timed_out)
