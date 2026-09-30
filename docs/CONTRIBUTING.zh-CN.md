# 为 steamverify 做贡献

[English](../CONTRIBUTING.md) · 简体中文

感谢帮忙。有三类贡献最有价值，大致按价值排序：**新增游戏清单**、**其它平台的缺陷报告**、**代码**。

---

## 一、新增游戏清单

清单是数据，不是代码 —— 扫描引擎里没有任何游戏专属逻辑。

### 生成清单

在一台已通过 Steam 安装了该游戏的机器上：

```bash
python tools/build_manifest.py \
    --app-id 292030 \
    --slug witcher3 \
    --name "The Witcher 3: Wild Hunt" \
    --vendor "CD Projekt Red" \
    --store-url "https://store.steampowered.com/app/292030/" \
    --out manifests
```

加 `--verbose` 可以看到逐个 depot 的进度。脚本会：

1. 读取 `steamapps/appmanifest_<appid>.acf`，得知装了哪些 depot、哪个构建；
2. 从 `<steam>/depotcache/` 读取每个 depot 的二进制清单；
3. 合并成 `manifests/<slug>/<slug>-<build>.svm`；
4. 登记进 `manifests/registry.json`；
5. 校验分块覆盖，发现问题就拒绝写出。

常用参数：`--default-dir windows=<目录名>`（自动定位提示）、`--alias <名称>`（额外别名）、`--dry-run`、`--no-write`。

### 提交 PR 前的检查清单

- [ ] **来源必须是干净安装。** 全新下载，从未装过 MOD，也不是整合版。用已经被改过的副本来生成清单，会把正常文件标成"外来"，反之亦然。
- [ ] **`steamverify doctor` 报告无问题。**
- [ ] **字节总数能对上。** 脚本是从 depot 清单推导出来的；请对照商店页面或 ACF 里的 `SizeOnDisk` 确认数量级合理。差距过大说明有 depot 没读到。
- [ ] **PR 里写明**游戏名、app id、构建号、平台，以及安装来源。

### 如果某个 depot 清单缺失

Steam 只会为它真正安装并更新过的 depot 缓存清单。若脚本提示跳过了某些 depot：

- 确认该游戏在这台机器上被 Steam **安装并至少更新过一次**；
- 检查 `<steam>/depotcache/` 下有没有 `<depotid>_*.manifest`；
- 语言类 depot：在 Steam 属性里切换游戏语言，等它下载完再跑一次。你从未安装过的语言，其文件无法从本地数据得到哈希。

**绝不要编造哈希。** 带猜测条目的清单比没有清单更糟。

### 非 Steam 版本

GOG、Epic、便携版或存档副本，改为对一个你信任的目录做哈希：

```bash
python tools/build_from_directory.py \
    --dir "D:/Games/SomeGame" --slug somegame --name "Some Game" \
    --version "1.2.3" --out manifests
```

请在 PR 中注明该清单是"目录哈希"而非"Steam 派生"。可选用 `--chunk-size` 把文件切成固定分块，便于日后定位局部损坏，代价是清单变大。

### 手动登记清单

```bash
python tools/add_game.py --manifest manifests/somegame/somegame-1.2.3.svm \
    --slug somegame --name "Some Game" --app-id 1234
python tools/add_game.py --list
python tools/add_game.py --prune      # 清理文件已不存在的记录
```

---

## 二、报告缺陷

请附上：

- `steamverify --version` 与操作系统；
- **执行的完整命令**；
- 如果判定本身有问题，附 `steamverify verify ... --json` 的输出；
- 如果涉及内置清单，附 `steamverify doctor` 与 `steamverify list` 的输出；
- 解析类错误请注明游戏名和商店。

**绝不要粘贴 Steam 账号凭据** —— 本工具任何环节都不需要。介意的话可以把绝对路径抹掉，只有出错的那段相对路径是必要的。

## 三、代码

### 目录结构

```
src/steamverify/
    manifest.py     .svm 格式：解析、写出、校验
    acf.py          Valve KeyValues 解析 + AppManifest 视图
    depot.py        Steam 二进制 depot 清单（protobuf）解析
    steam.py        定位 Steam 根目录、库与已安装游戏
    registry.py     内置游戏注册表
    scanner.py      扫描引擎（与游戏无关）
    reporter.py     text / JSON / CSV 输出
    i18n.py         英文 / 简体中文文案
    cli.py          参数解析与子命令
tools/              清单生成器（不随包安装）
tests/              pytest 测试；无需安装任何游戏
manifests/          内置游戏数据 + registry.json
```

请保持 `scanner.py` 不包含任何游戏专属知识。任何与特定游戏相关的东西都属于 `manifests/`。

### 开发环境

```bash
git clone https://github.com/Jayve/steamverify.git
cd steamverify
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
ruff check src tools tests
```

测试必须在**没有安装任何游戏**的机器上通过 —— 它是逐字节合成 Steam 清单来测解析器的，而不是依赖一个可能漂移的样本文件。

### 约定

- **零运行时依赖。** 只用标准库。用户往往是在一台已经出问题的机器上运行它，先让人解决依赖树是很糟糕的取舍。
- **宁可报错，不要猜。** 可能解析错的路径必须抛异常。`depot.py` 里关于"第 5 字段曾被当成符号链接目标"的注释，就是猜错的后果。
- **把反直觉的地方写下来。** Steam 的格式里坑很多（depot id 藏在文件名里、分块偏移不是你以为的东西、目录伪装成 64 字节的文件）。花了你一小时才搞明白的，请写进模块 docstring。
- **每个行为变更都配一个测试**，尤其是以缺陷命名的回归测试。
- **报告要易读。** 用户应该在第一屏就知道哪里不对、下一步做什么。
- 风格与现有一致：带类型标注、`from __future__ import annotations`、不用裸 `except`。

### 多语言文案

界面文案集中在 `src/steamverify/i18n.py`：

- **英文是基准。** 新文案先写英文；缺翻译时回退到英文，而不是显示原始 key。
- **机读输出不翻译。** JSON 键名、CSV 表头、状态值（`verified`/`extra`…）永远保持英文，只有给人看的散文会本地化。这样 `--lang` 不会影响脚本。
- 新增语言 = 在 `CATALOG` 里加一个 dict + 在 `LANGUAGE_ALIASES` 里加别名。
- 测试会强制：两个语言的 key 集合完全一致、`{}` 占位符一致、没有"复制粘贴未翻译"的条目。

### 修改清单格式

先读 [manifest-format.md](manifest-format.md)（[中文版](manifest-format.zh-CN.md)）。提升 `FORMAT_VERSION` 对旧客户端是破坏性变更 —— 它们会拒绝读取该文件，这正是预期行为。绝不要在同一个版本号下重新解释字节含义。

## 提交与 PR 约定

- 一个提交只做一件逻辑上的事；标题用祈使句（`fix: locate depot manifests in every library`，而不是 `fixes`）。
- 正文写清 **为什么**，而不只是做了什么。
- 新增游戏清单的 PR 请一并提交重新生成的 `manifests/registry.json`。

## 行为准则

做个体面的人。对解析器有分歧很正常，但对人不尊重不行。
