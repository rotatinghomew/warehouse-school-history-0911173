import argparse
import json
import tempfile

from .demo import run
from .facts import detail, detail_as_of, unresolved
from .organizations import claims, intervals, view
from .projection import audit_log, rebuild
from .releases import Warehouse, read_release, snapshot, summary, summary_as_of


def _report(database, arguments):
    if arguments.command == 'view':
        return view(database, arguments.school, arguments.day, as_of=arguments.as_of)
    if arguments.command == 'timeline':
        return intervals(database, arguments.school, as_of=arguments.as_of)
    if arguments.command == 'claims':
        return claims(database, arguments.school)
    if arguments.command == 'detail':
        return detail_as_of(database, arguments.as_of) if arguments.as_of else detail(database)
    if arguments.command == 'summary':
        return summary_as_of(database, arguments.as_of) if arguments.as_of else summary(database)
    if arguments.command == 'snapshot':
        return snapshot(database, as_of=arguments.as_of)
    if arguments.command == 'unresolved':
        return unresolved(database)
    if arguments.command == 'audit':
        return audit_log(database, arguments.school)
    if arguments.command == 'release':
        return read_release(database, arguments.name)
    if arguments.command == 'rebuild':
        return {'schools_recomputed': rebuild(database)}
    raise SystemExit(f"未知子命令: {arguments.command}")


def _query(parser):
    parser.add_argument('--root', required=True, help='持久化数据目录')
    parser.add_argument('--as-of', dest='as_of', help='按该已知时间还原当时掌握的信息')
    return parser


def main():
    parser = argparse.ArgumentParser(description='学校活动事实与机构归属历史入仓')
    parser.add_argument('--root', help='持久化数据目录；省略时运行隔离示例')
    commands = parser.add_subparsers(dest='command')

    for name, help_text in (('view', '某业务日期的生效归属'),
                            ('timeline', '学校归属时间线'),
                            ('claims', '学校全部补报（含已撤回）'),
                            ('audit', '学校的变更审计')):
        sub = _query(commands.add_parser(name, help=help_text))
        sub.add_argument('--school', required=True)
        if name == 'view':
            sub.add_argument('--day', required=True)

    for name, help_text in (('detail', '当前明细'), ('summary', '当前分学段汇总'),
                            ('snapshot', '一致读取明细与汇总'), ('unresolved', '未归属活动'),
                            ('rebuild', '由原始事实重建派生投影')):
        _query(commands.add_parser(name, help=help_text))

    release = commands.add_parser('release', help='读取已对外发布的月度摘录')
    release.add_argument('--root', required=True)
    release.add_argument('--name', required=True)

    arguments = parser.parse_args()
    if arguments.command is None:
        if arguments.root:
            result = run(arguments.root)
        else:
            with tempfile.TemporaryDirectory() as directory:
                result = run(directory)
    else:
        warehouse = Warehouse(arguments.root)
        try:
            result = _report(warehouse.db, arguments)
        finally:
            warehouse.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
