# `.svm` 清单格式

版本 1。所有整数均为小端序。

一个 `.svm` 文件描述**某一个游戏构建**的官方文件。它是 `steamverify` 唯一信任的东西，
因此格式刻意做得小、明确、且加载时严格校验。

## 设计目标

| 目标 | 带来的取舍 |
|---|---|
| 能放进 Git 仓库 | 二进制格式、路径与哈希去重、不做美化输出 |
| 扫描可复现、离线 | 自包含；不依赖网络与 Steam API |
| 能识别过期清单 | 头部记录游戏名、app id、构建号 |
| 能向前演进 | 显式的版本闸门；末尾未知数据被忽略 |
| 与游戏无关 | 布局里没有任何特定游戏的内容 |

一个 70 GB、7.3 万个分块的游戏，清单约 2.7 MiB。

## 布局

```
偏移    长度   字段
------  -----  ---------------------------------------------------------
0       8     magic  b"SVHASH\x00\x01"
8       4     u32    格式版本（1）
12      4     u32    头部 JSON 字节长度（H）
16      H     bytes  头部 JSON（UTF-8）
16+H    4     u32    路径数（P）
...           路径    每条：u16 长度 + UTF-8 字节
        4     u32    哈希数（N）
...           哈希    每条：20 字节裸 SHA-1
        4     u32    文件记录数（F）
...           记录    每条 20 字节，见下
        4     u32    分块记录数（C）
...           分块    每条 16 字节，见下
```

### 文件记录（20 字节）

| 类型 | 字段 | 含义 |
|---|---|---|
| `u32` | 路径索引 | 指向路径表 |
| `u64` | size | 精确的磁盘字节数 |
| `u32` | sha1 索引 | 指向哈希表；整文件摘要 |
| `u32` | 首个分块 | 该文件第一个分块记录的索引 |
| `u32` | 分块数 | 普通文件为 `0` |

### 分块记录（16 字节）

| 类型 | 字段 | 含义 |
|---|---|---|
| `u64` | offset | 该分块在文件中的字节偏移 |
| `u32` | length | 分块字节数，必须 `> 0` |
| `u32` | sha1 索引 | 指向哈希表 |

## 语义

* `分块数 == 0` —— 文件按原样存放。其内容必须哈希为记录中的 `sha1`，长度必须等于 `size`。
* `分块数 > 0` —— 文件按 SteamPipe 分块存放。`[offset, offset + length)` 区间的字节必须
  哈希为该分块的 `sha1`。所有分块长度之和必须等于 `size`，且偏移必须互不重复。
  这两条共同保证文件的每一个字节都被恰好检查一次。

显式记录 `size` 意味着被截断或被填充的文件在开始哈希之前就会被拒绝，这让明显损坏的
安装扫描得很快。

## 头部 JSON

必需字段 —— 缺任何一个都会拒绝加载：

| 键 | 类型 | 含义 |
|---|---|---|
| `game` | string | 显示名 |
| `platform` | string | `windows`、`linux`、`macos` |
| `file_count` | int | 必须等于记录条数 |
| `chunk_count` | int | 必须等于分块记录条数 |
| `total_bytes` | int | 所有 `size` 之和 |
| `source` | string | 清单的生成方式 |

当前生成器还会写入：

| 键 | 含义 |
|---|---|
| `slug` | `--game` 使用的短标识 |
| `app_id` | Steam app id |
| `build_id` | Steam 构建号 —— **过期判断依据** |
| `vendor` | 发行商，用于署名 |
| `depots` | 参与贡献文件的 depot id |
| `language` | 安装时使用的 Steam 语言 |
| `generated` | UTC 时间戳 |
| `generator` | 生成脚本 |
| `replay` | 可复现的完整命令 |
| `notes` | 自由文本 |
| `chunk_size` | 由目录生成时的名义分块大小 |

## 加载时执行的校验

1. magic 正确；
2. 格式版本可识别；
3. 头部能解析为 JSON，且包含全部必需键；
4. 每个路径 / 哈希 / 分块索引都在范围内；
5. `file_count` 与 `chunk_count` 与实际记录条数一致。

`steamverify doctor` 还会对每个文件运行 `validate_coverage`：分块长度之和必须等于 `size`，
偏移必须唯一且非负，普通文件必须带 40 字符的摘要。`tools/build_manifest.py` 在写出之前
会跑同样的检查，因此损坏的清单不会被误提交。

## 为什么要做哈希去重

重复摘要在真实数据里非常常见：一个由相同 1 MiB 零填充块构成的文件会有成千上万个完全
相同的分块哈希，多个 depot 也会重复声明共享文件。把每个摘要只存一次、再用 `u32` 索引
引用，成本从 20 字节降到 4 字节。在真实清单上，这决定了体积是几 MB 还是几十 MB。

## 读写示例

```python
from steamverify import manifest

mf = manifest.load("manifests/witcher3/witcher3-25575366.svm",
                   verify_digest="cb2ddd35...")   # 可选的 SHA-256 固定校验
for entry in mf.files:
    print(entry.path, entry.size, entry.chunk_count)

manifest.write("out.svm", header_dict, entries)    # 生成端
manifest.write_json("dump.json", mf)               # 便于人读的调试转储
```

## 未来版本 2 的兼容规则

* 提升 `FORMAT_VERSION`；绝不在同一版本号下重新解释已有字节。
* 旧版读取器遇到更新版本必须明确失败 —— 它们确实会（`unsupported manifest format version`）。
* 新的可选数据放进头部 JSON 或最后一个区段之后，读取器本来就会忽略。

## 与 Steam 自带清单的关系

`manifests/<slug>/*.svm` 派生自 `<steam>/depotcache/` 下的 Steam 二进制 depot 清单，
后者使用 protobuf，格式记录在 `src/steamverify/depot.py` 中。之所以还要有 `.svm`：
depot 格式是按 depot 分开的、protobuf 编码、每个动辄数 MB；`.svm` 把同一个构建的所有
depot 合并成一个文件，任何语言用几次 `struct.unpack` 就能读取。
