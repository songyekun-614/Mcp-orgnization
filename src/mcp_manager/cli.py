"""Local trusted CLI: plan approval is available here, never as an MCP tool."""
import argparse
import json
from pathlib import Path
import sys
from .core.manager import ManagerCore


def main(argv=None):
    parser=argparse.ArgumentParser(prog='mcp-manager')
    parser.add_argument('--data-dir',default=None)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('detect','list','dashboard','clients','tasks','export'):commands.add_parser(name)
    scan=commands.add_parser('discover');scan.add_argument('roots',nargs='*');scan.add_argument('--mode',choices=['quick','standard'],default='quick')
    serve=commands.add_parser('serve');serve.add_argument('--data-dir',dest='serve_data_dir');serve.add_argument('--transport',choices=['stdio','streamable-http'],default='stdio');serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=8791)
    gui=commands.add_parser('gui');gui.add_argument('--data-dir',dest='gui_data_dir')
    register=commands.add_parser('register');register.add_argument('spec_file')
    connect=commands.add_parser('plan-connect');connect.add_argument('server_id');connect.add_argument('--tool');connect.add_argument('--arguments',default='{}')
    install=commands.add_parser('plan-install');install.add_argument('request_file')
    approve=commands.add_parser('approve');approve.add_argument('plan_id');approve.add_argument('--yes',action='store_true')
    execute=commands.add_parser('execute');execute.add_argument('plan_id')
    plan=commands.add_parser('plan');plan.add_argument('plan_id')
    permission=commands.add_parser('permissions');permission.add_argument('policy',choices=['read_only','approved_only'])
    search=commands.add_parser('search');search.add_argument('query');search.add_argument('--offline',action='store_true')
    backup=commands.add_parser('backup-db');backup.add_argument('destination')
    restore=commands.add_parser('plan-restore-db');restore.add_argument('backup')
    args=parser.parse_args(argv)
    data_dir=getattr(args,'serve_data_dir',None) or getattr(args,'gui_data_dir',None) or args.data_dir
    if args.command=='gui':
        from .gui.app import main as gui_main
        return gui_main(['--data-dir',data_dir] if data_dir else [])
    core=ManagerCore(data_dir)
    try:
        if args.command=='serve':
            if args.host not in ('127.0.0.1','localhost','::1'):raise PermissionError('HTTP management is loopback-only; use an authenticated external transport for remote access')
            from .server import create_server
            if args.transport=='streamable-http':core.runtime.environ['MCP_MANAGER_ENDPOINT']=f'http://{args.host}:{args.port}/mcp'
            create_server(core,args.host,args.port).run(transport=args.transport);return 0
        if args.command=='detect':result=core.detect_platform()
        elif args.command=='list':result=core.list_servers()
        elif args.command=='dashboard':result=core.dashboard()
        elif args.command=='clients':result=core.list_clients()
        elif args.command=='tasks':result=core.list_tasks()
        elif args.command=='discover':result=core.discover(args.roots,args.mode)
        elif args.command=='register':result=core.register_server(json.loads(Path(args.spec_file).read_text()))
        elif args.command=='plan-connect':result=core.plan_connection(args.server_id,args.tool,json.loads(args.arguments))
        elif args.command=='plan-install':result=core.plan_install(json.loads(Path(args.request_file).read_text()))
        elif args.command=='plan':result=core.db.get_record('plans',args.plan_id)
        elif args.command=='approve':
            proposal=core.db.get_record('plans',args.plan_id)
            if not args.yes:
                print(json.dumps(proposal,ensure_ascii=False,indent=2))
                if input('Approve this exact plan? [yes/no] ').strip()!='yes':return 1
            result=core.approve_plan(args.plan_id)
        elif args.command=='execute':result=core.execute_plan(args.plan_id)
        elif args.command=='permissions':
            core.db.set_setting('agent_permission',args.policy);core.db.audit('local_agent_policy_changed',{'policy':args.policy});result={'policy':args.policy}
        elif args.command=='backup-db':result={'backup':core.db.backup(args.destination)}
        elif args.command=='plan-restore-db':result=core.plan_database_restore(args.backup)
        elif args.command=='search':result=core.search_registry(args.query,offline=args.offline)
        elif args.command=='export':result=core.export_configuration()
        else:raise ValueError('Unknown operation')
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error':{'type':type(exc).__name__,'message':str(exc)}},ensure_ascii=False),file=sys.stderr);return 1
    finally:core.close()
