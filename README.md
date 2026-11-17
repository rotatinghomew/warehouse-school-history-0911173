# 学校活动事实与机构归属历史入仓

这是一个处理本地数据交换的仓湖项目。示例使用虚构机构和匿名合成记录，不连接业务平台。

## 数据契约

学校归属使用左闭右开的业务日期区间，同一学校不可重叠。活动粒度是 event_id，一条活动携带参与人数而非自然人明细。尚未匹配的学校保留为空归属，发布结果将其列为未归属。发布名称不可重新绑定，源批次按规范化内容校验。

主管单位补报以追加方式入仓：原始主档、补报与活动都不可改写，当前归属是由它们派生的时间线。补报可以只覆盖原归属区间的一部分，也可以被后继补报显式撤回；互不替代的相交矛盾保留为争议，不按到达先后挑选。查询既能看某个业务日期的当前归属，也能按获知时间还原当时掌握的解释。已对外发出的月度摘录保持不变，当前明细与分学段汇总一起反映更正，并由审计记录追到哪些原始事实因此改变、为何改变。

## 代码职责

- `schoolmart/organizations.py`：主档归属、补报登记、争议判定与生效时间线。
- `schoolmart/facts.py`：活动接入、业务键校验、未归属明细与当时口径还原。
- `schoolmart/projection.py`：由原始事实派生活动归属与审计，事务内一致刷新。
- `schoolmart/releases.py`：学段汇总、一致快照及不可变月度摘录。
- `schoolmart/storage.py`：本地事务、内容摘要和查询工具。

## 安装与运行

使用 Python 3.11（兼容该系列补丁版本，独立容器镜像以 SHA256 摘要固定）。构建工具和运行依赖均按精确版本登记。先执行 `python3 -m pip install -r requirements-build.txt -r requirements.txt`；依赖仅在准备环境时安装，运行与测试均可断网。

```sh
python3 -m compileall -q schoolmart tests
python3 -m build --wheel --no-isolation
python3 -m unittest discover -s tests -v
python3 -m schoolmart
```

默认示例在独立临时目录执行，结束后删除目录。需要检查持久文件时可执行 `python3 -m schoolmart --root state`。测试自己创建临时目录，不依赖固定端口、外部服务或其他用例顺序。

## 容器

```sh
docker build -t school-history:local .
docker run --rm --network none school-history:local
docker run --rm --network none school-history:local python3 -m schoolmart
```

容器构建负责预装锁定依赖，启动不下载包。示例输出是真实接入、查询和发布的数据，不读取预制输出。测试及示例可分别运行，退出码非零表示失败。

## 交换口径

进一步的字段含义、范围及兼容边界见 `docs/data-contract.md`。
