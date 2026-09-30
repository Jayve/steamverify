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

### 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | 干净，与清单完全一致 |
| `1` | 官方文件完好，但存在多出的文件 |
| `2` | 官方文件被改、缺失，或只剩空壳（stub） |
| `3` | 出错（文件不可读、清单损坏、找不到安装） |

`--fail-on modified` / `--fail-on missing` 可以放宽判定条件。

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
