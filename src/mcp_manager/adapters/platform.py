"""Platform-specific paths; WSL is explicitly identified as its own environment."""
import os
from pathlib import Path
import platform
import shutil
import sys


class PlatformDetector:
    def __init__(self,home=None,environ=None,system=None):
        self.home=Path(home) if home else Path.home();self.env=dict(os.environ if environ is None else environ);self.system=system or platform.system()
    def detect(self):
        if self.system=='Windows':
            local=Path(self.env.get('LOCALAPPDATA',str(self.home/'AppData/Local')))
            config=Path(self.env.get('APPDATA',str(self.home/'AppData/Roaming')))/'MCPManager'
            data=local/'MCPManager';state=data/'state';service='windows-user-process'
        else:
            config=Path(self.env.get('XDG_CONFIG_HOME',str(self.home/'.config')))/'mcp-manager'
            data=Path(self.env.get('XDG_DATA_HOME',str(self.home/'.local/share')))/'mcp-manager'
            state=Path(self.env.get('XDG_STATE_HOME',str(self.home/'.local/state')))/'mcp-manager';service='systemd-user' if shutil.which('systemctl') else 'user-process'
        paths={k:str(v) for k,v in dict(home=self.home,config=config,data=data,state=state,install=data/'servers',cache=data/'cache').items()}
        tools={name:shutil.which(name,path=self.env.get('PATH',os.defpath)) for name in ('python3','python','node','npm','npx','uv','uvx','docker','git','bwrap','codex','dsh')}
        return dict(system=self.system,release=platform.release(),architecture=platform.machine(),python={'version':platform.python_version(),'executable':sys.executable},
            environment_kind='wsl' if self.system=='Linux' and ('microsoft' in platform.release().lower() or self.env.get('WSL_DISTRO_NAME')) else 'native',
            home=str(self.home),path=self.env.get('PATH',''),tools=tools,paths=paths,service_manager=service,
            desktop=self.env.get('XDG_CURRENT_DESKTOP'),display_protocol=self.env.get('XDG_SESSION_TYPE'))
