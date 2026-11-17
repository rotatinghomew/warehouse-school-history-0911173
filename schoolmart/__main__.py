import argparse
import json
import tempfile

from .demo import run


def main():
    parser=argparse.ArgumentParser(description='学校活动事实与机构归属历史入仓')
    parser.add_argument('--root',help='持久化数据目录；省略时使用隔离临时目录')
    args=parser.parse_args()
    if args.root:
        result=run(args.root)
    else:
        with tempfile.TemporaryDirectory() as directory:
            result=run(directory)
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))


if __name__=='__main__':
    main()
