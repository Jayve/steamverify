# steamverify 使用说明

**用内置的官方哈希清单校验 Steam 游戏安装，并列出所有不属于官方版本的文件。**

[English](../README.md) · **简体中文**

工具本身支持中英双语：`--lang zh` 切换中文输出，也可以让它跟随系统区域设置。
详见[输出语言](#输出语言)。

---

Steam 自带的「验证游戏文件完整性」只能告诉你*有问题*，却告诉不了你**多了什么** —— 因为它只认识自己装下去的文件。
`steamverify` 补上的正是这块空白：

1. **官方文件是否完好？** 逐文件、逐 SteamPipe 分块比对哈希，基准直接取自 Steam 自己的 depot 清单。
2. **目录里多了什么？** MOD、修改器、作弊菜单、整合版残留、崩溃转储、存档、第三方工具包，全部单独列出，不会被静默忽略。

**完全离线运行**：清单内置于仓库，扫描结果可复现，不需要联网、不需要 Steam 登录、不需要 API Key。

## 安装

需要 Python 3.9+，无任何第三方依赖。

```bash
git clone https://github.com/Jayve/steamverify.git
cd steamverify
pip install -e .
steamverify --help
```

也可以免安装直接运行：

```bash
python run.py verify --game witcher3
```

## 常用命令

```bash
# 查看内置了哪些游戏
steamverify list

# 校验游戏（自动定位安装目录）
steamverify verify --game witcher3

# 指定目录（移动硬盘、拷贝副本、其它库）
steamverify verify --game witcher3 --dir "D:/Games/The Witcher 3"

# 列出全部多出的文件，而不是只显示前 25 个
steamverify verify --game witcher3 -v

# 机器可读输出，便于接入 CI 或二次处理
steamverify verify --game witcher3 --json > report.json
steamverify verify --game witcher3 --csv report.csv

# 快速模式：只比对大小，不做内容哈希（几秒而不是几分钟）
steamverify verify --game witcher3 --no-hash

# 忽略自己的 MOD 目录，让异常更醒目
steamverify verify --game witcher3 --ignore 'mods/*' --ignore 'dlc/*'

# 校验内置清单本身有没有被改动
steamverify doctor

# 查看本机 Steam 库
steamverify steam -v

# 中文输出（等价于 STEAMVERIFY_LANG=zh，或系统区域设置为 zh_*）
steamverify --lang zh verify --game witcher3
```

### 输出语言

报告是给人看的散文，因此会本地化；机器可读的部分不会。

| | English | 简体中文 |
|---|---|---|
| 指定方式 | `--lang en` | `--lang zh` |
| 接受的别名 | `en`、`en-US`、`english` | `zh`、`zh-CN`、`zh-Hans`、`chs`、`cn`、`中文`、`简体中文` |
| 环境变量 | `STEAMVERIFY_LANG=en` | `STEAMVERIFY_LANG=zh` |
| 自动检测来源 | `LC_ALL` / `LC_MESSAGES` / `LANG` | 同上 |

优先级：`--lang` → `STEAMVERIFY_LANG` → 系统区域设置 → 英文。

**会翻译**的内容：

* 整份文本报告，包括各节标题、结论、每个文件的判定原因，以及"处理建议"；
* 进度输出与 `--help`；
* `list`、`info`、`doctor`、`steam` 以及全部错误提示。

**永不翻译**（保证脚本与 CI 不受 `--lang` 影响）：

* JSON 的键与值，包括 `status`（`verified`/`extra`/…）与 `reason` 字段
  （稳定的 key，如 `scan.reason.content_differs`，旁边附英文原文 `reason_text`）；
* CSV 表头与 `status` 列；
* 退出码。

缺翻译时回退英文，而不是暴露原始 key；CI 会断言两个语言的 key 集合与 `{}` 占位符完全一致。

## 输出样例

以下均为**真实运行结果**（69 GiB 的实际安装），仅做了脱敏：目录替换为 `<GAME_DIR>`，
时间替换为 `<UTC>`，耗时替换为 `<elapsed>`。完整文件见
[`examples/`](examples/)。

### 场景一：发现大量非官方文件

一台装了整合版工具包的机器 —— 官方数据完好，但多出 4,960 个文件：

```
  游戏     The Witcher 3: Wild Hunt
  app id   292030
  构建     25575366
  清单     1,937 个文件，73,477 个分块，69.19 GiB
  清单标识 sha256:cb2ddd355f0d42c5
  目录     <GAME_DIR>   [S:\SteamLibrary, build 25575366]
  扫描时间 <UTC>  (<elapsed>)
------------------------------------------------------------------------

汇总
  # 一致      1,915   与官方清单一致的文件
  . 已修改        0   清单内但内容不同的文件
  # 占位         22   无内容的占位条目（DLC 授权标记）
  # 多余      4,960   不在任何官方清单中的文件
  . 缺失          0   缺失的官方文件
  - 已忽略      297   被忽略规则跳过

  已按官方版本校验 1,915 个文件 / 69.19 GiB
  多余文件 4,960 个 / 754.88 MiB，不属于官方版本

  结论：游戏数据完好；仅有无内容的 DLC 标记文件存在差异。

多余文件分布
    4,952 个文件    746.38 MiB   tools/
        3 个文件      7.25 MiB   dlc/bob/
        2 个文件      1.00 MiB   mods/mod0001/
        2 个文件    256.00 KiB   dlc/dlc10/
        1 个文件           8 B   content/notes.txt
  路径相对于游戏目录；结尾带 / 表示该文件夹内的文件全部都是多余的。想保留的可用 --ignore '<路径>/*' 忽略，加 -v 可查看每个文件的大小与时间。

占位条目（不是损坏信号）
  S dlc-tombstones/bob/bob.tombstone
  S dlc-tombstones/bob/bob_speech_cn.tombstone
  S dlc-tombstones/bob/bob_speech_en.tombstone
  S dlc-tombstones/dlc1/dlc1.tombstone
  S dlc-tombstones/dlc10/dlc10.tombstone
  …… 另有 17 项
  这些官方条目只带标识哈希，本地本就应为空文件。

处理建议
  1. 多余文件不属于官方版本。MOD、修改器、
     存档、崩溃转储和整合版残留都会出现在这里。
     不需要的可以直接删除，不影响游戏本体。

  退出码：2
```

完整输出：[`examples/report-full.zh-CN.txt`](examples/report-full.zh-CN.txt)

### 场景二：排除已知的第三方目录后

用 `--ignore` 把这些目录排除掉，结果就只剩 22 个 DLC 授权标记：

```bash
steamverify --lang zh verify --game witcher3 \
    --ignore 'tools/*' --ignore 'mods/*' --ignore 'dlc/bob/*' \
    --ignore 'dlc/dlc10/*' --ignore 'content/notes.txt' --ignore '*.md' \
    --ignore '*.stamp' --ignore 'metadata.store'
```

```
汇总
  # 一致      1,915   与官方清单一致的文件
  . 已修改        0   清单内但内容不同的文件
  # 占位         22   无内容的占位条目（DLC 授权标记）
  . 多余          0   不在任何官方清单中的文件
  . 缺失          0   缺失的官方文件
  - 已忽略    5,259   被忽略规则跳过

  已按官方版本校验 1,915 个文件 / 69.19 GiB

  结论：游戏数据完好；仅有无内容的 DLC 标记文件存在差异。

占位条目（不是损坏信号）
  S dlc-tombstones/bob/bob.tombstone
  S dlc-tombstones/bob/bob_speech_cn.tombstone
  S dlc-tombstones/bob/bob_speech_en.tombstone
  S dlc-tombstones/dlc1/dlc1.tombstone
  S dlc-tombstones/dlc10/dlc10.tombstone
  …… 另有 17 项
  这些官方条目只带标识哈希，本地本就应为空文件。

处理建议
  无需修复 —— 这些占位条目本就应为空。退出码仍非 0，因为目录并非逐字节完全一致；在 CI 中可用 --fail-on missing 忽略它们。

  退出码：2
```

完整输出：[`examples/report-clean.zh-CN.txt`](examples/report-clean.zh-CN.txt)

英文样例见 [`examples/report-full.txt`](examples/report-full.txt) 与
[`examples/report-clean.txt`](examples/report-clean.txt)。

### 关于退出码

场景二里所有内容其实都是"对的"，退出码却是 2 而不是 0。这是刻意的：

* `stub` 表示官方清单里的条目在本地是 0 字节文件。它**不是**损坏信号，
  但也确实意味着目录并非逐字节等同于官方版本，所以不报 0；
* 想让这类差异不触发失败，用 `--fail-on missing`（或 `--fail-on modified`）；
* 只想确认"官方游戏数据有没有被动过"时，看 JSON 里的 `game_data_intact` 字段即可 ——
  它不受 stub 影响。

样例由 [`tools/generate_examples.py`](../tools/generate_examples.py) 从真实安装生成，
CI 会运行 [`tools/check_docs.py`](../tools/check_docs.py)
验证本文引用的每一段输出仍然逐行一致。

### 多余文件的呈现方式

报告只有告诉你"删什么"才有用，因此每个多余项都会给出相对游戏目录的具体路径。
唯一会被折叠的，是**里面每个文件都多余**的文件夹：

| 显示 | 含义 | 处理方式 |
|---|---|---|
| `tools/`（结尾带 /） | `tools/` 下所有文件都多余，官方清单在这里没有任何文件 | 整个文件夹可以删除；想保留就用 `--ignore 'tools/*'` |
| `content/notes.txt` | 只有这一个文件多余，它和官方文件混在一起 | 删除该文件，或用 `--ignore 'content/notes.txt'` |

只要文件夹里还有任何一个官方文件，就不会被折叠成一行，因此清单内的文件不可能
被目录行掩盖。路径过长时会完整换行显示，不做截断 —— 复制不出来的路径等于没用。
加 `-v` 会额外列出每个多余文件的大小与修改时间；`--json` / `--csv` 始终包含逐文件明细。

## 工作原理

1. **哈希来自 Steam 自己。** Steam 客户端把每个已安装 depot 的二进制清单缓存在
   `<steam>/depotcache/`，其中包含每个文件的官方路径、大小、SHA-1，以及每个
   SteamPipe 分块的 SHA-1。`tools/build_manifest.py` 读取它们并打包成紧凑的
   `.svm` 文件。**哈希不是猜的，也不是从别处抄的。**

2. **清单会与 Steam 的账本对账。** 从各 depot 清单重建出的字节总数，必须等于
   `appmanifest_<appid>.acf` 里记录的 `SizeOnDisk`。对不上就说明解析错了，
   构建会直接失败，而不是产出错误数据。

3. **扫描逐层判定。** 先比大小，再比内容：普通文件比整文件 SHA-1，SteamPipe
   归档逐分块比 SHA-1。31 GB 的 `.cache` 出问题时，报告的是**第几个分块坏了**，
   而不是笼统的一句「文件不一致」。

4. **不在清单里的文件就是多出来的。** 这正是 Steam 自己无法给出的那一类。

### 文件分类

| 分类 | 含义 |
|---|---|
| `verified` | 大小与内容都与官方清单一致 |
| `modified` | 清单内的文件，但大小或内容不同 |
| `stub` | 官方条目只带一个标识哈希，本地文件本就应为空（DLC 授权标记）——**不是损坏信号** |
| `extra` | 不在任何官方清单中 |
| `missing` | 官方文件缺失 |

默认会忽略少数永远不值得关注的路径：本工具自己的输出、`*.log`、`*.dmp`、
`__pycache__`、`steam_appid.txt` 等。加 `--no-default-ignores` 可以全部显示，
或用 `--ignore GLOB` 追加自己的规则。

## 为其它游戏生成清单

支持新游戏是**加数据**，不是改代码：

```bash
python tools/build_manifest.py \
    --app-id 292030 \
    --slug witcher3 \
    --name "The Witcher 3: Wild Hunt" \
    --vendor "CD Projekt Red" \
    --out manifests
```

它会自动在本机 Steam 安装中找到 ACF 与 depot 清单，重建官方文件列表，写出
`manifests/<slug>/<slug>-<build>.svm`，并登记到 `manifests/registry.json`。
之后发 PR 即可。

非 Steam 平台（GOG、Epic、便携版）可以改为对一个你信任的目录做哈希：

```bash
python tools/build_from_directory.py --dir "D:/Games/SomeGame" \
    --slug somegame --name "Some Game" --out manifests
```

详见 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md) 与
[清单格式规范](manifest-format.zh-CN.md)。

### 提交清单的要求

- 必须来自**全新安装、从未装过 MOD**的副本；
- depot 数量与字节总数必须能与 ACF 对上；
- `steamverify doctor` 必须报告无问题。

## 局限性（如实说明）

- **一份清单只对应一个构建版本。** 游戏更新后清单即过期，届时所有文件都会显示
  为 modified。用 `steamverify info` 对比构建号，或重新生成清单；当 ACF 构建号
  与清单不一致时，工具会给出提示。
- **扫描干净 ≠ 安全保证。** 它只证明文件与某个已知良好版本一致，不能说明某个
  MOD 的行为是否安全；如果清单来源本身被控制，结论也会被带偏。请与第二个来源
  交叉核对清单的 SHA-256。
- **`stub` 判定刻意收得很窄**（本地 0 字节且官方大小也为 0），不会用来掩盖真实损坏。
- **加密的 depot 清单**（需要密码的测试分支）无法解析，会明确报错而不是误读。
- 70 GB 全量哈希在 SSD 上约一分钟，机械硬盘会慢很多。只需要文件清单时用 `--no-hash`。

## 许可

MIT 许可。本仓库**不包含任何游戏代码或游戏数据**，只有文件路径、字节大小和
SHA-1 哈希 —— 与 Steam 本地缓存并分发到每个客户端的信息相同。游戏名称与商标
归各自权利人所有，详见 [NOTICE](../NOTICE)。
