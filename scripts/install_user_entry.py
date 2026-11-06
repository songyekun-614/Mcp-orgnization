"""Install user-level launchers without modifying system packages or other MCPs."""
import argparse
import os
from pathlib import Path
import shlex
import sys


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--python',default=sys.executable);parser.add_argument('--app-dir',default=None);parser.add_argument('--desktop',action='store_true');args=parser.parse_args()
    python=Path(args.python).expanduser().absolute();default_app=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))/'MCPManager/app' if os.name=='nt' else Path.home()/'Apps/MCP-Manager';root=Path(args.app_dir or default_app).expanduser();root.mkdir(parents=True,exist_ok=True)
    if not python.is_file():raise ValueError('Select the installed MCP Manager Python interpreter')
    if os.name=='nt':
        launcher=root/'MCP-Manager.cmd';launcher.write_text('@echo off\r\n"'+str(python)+'" -m mcp_manager.gui.app %*\r\n',encoding='utf-8')
    else:
        launcher=root/'start';launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(python))+' -m mcp_manager.gui.app "$@"\n');launcher.chmod(0o755)
        if args.desktop:
            data=Path(os.environ.get('XDG_DATA_HOME',str(Path.home()/'.local/share')));applications=data/'applications';applications.mkdir(parents=True,exist_ok=True)
            desktop=applications/'mcp-manager.desktop'
            desktop.write_text('[Desktop Entry]\nType=Application\nName=MCP Manager\nComment=Discover and manage MCP servers\nExec="'+str(launcher)+'"\nIcon=applications-system\nTerminal=false\nCategories=Development;Utility;\n')
    print(launcher)

if __name__=='__main__':main()
