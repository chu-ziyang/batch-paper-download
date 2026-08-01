---
name: batch-paper-download
description: 通过学校 VPN 或 CARSI + 浏览器自动化，从六大出版商批量下载论文 PDF。输入 DOI、精确标题或关键词，自动识别出版商、构造 PDF 链接、校验并跳过已下载。使用场景：按参考文献列表批量抓取全文、整理文献库。
---

# Batch Paper Download

通过学校 VPN 或 CARSI 机构登录 + 浏览器自动化，从 Elsevier / ACS / Springer / Wiley / RSC / Taylor & Francis 批量下载论文 PDF。

## 什么时候用

- 用户给出一批 DOI / 精确标题 / 关键词，需要批量下载 PDF 到本地目录
- 用户需要从学校订阅数据库中抓取全文（VPN 或 CARSI 认证）

## 前置条件

- Python 3.12+（Windows 上必须用 `py`；`python` 是 Store stub，会 exit 49）
- browser-skill：`bsk status` 显示 daemon running，`bsk browsers` 能拿到浏览器 ID
- Chrome + browser-skill 扩展已连接
- 首次运行需要用户手动完成学校 VPN/CARSI 登录（会弹出浏览器窗口）

## 快速开始

1. 确保 `config.yaml` 存在；缺失时运行 `py batch-wos-download.py` 自动生成模板
   - `browser`：`bsk browsers` 第一列 ID
   - `auth.method`：`vpn` 或 `carsi`
   - VPN 模式：填 `auth.vpn.url` 和 `auth.vpn.login_link`
   - CARSI 模式：填 `school.english_name`（用于在出版商机构列表搜索）
   - 可选 `auth.carsi.probe`：填一篇本校订阅的论文页 URL，登录后自动验证权限
   - 可选 `download.delay`：篇间等待秒数，防出版商反爬
2. 输入文件每行一篇：
   - DOI：`10.1016/j.chemosphere.2024.141264`
   - 精确标题：`"Molecular transformation of petroleum compounds by hydroxyl radicals"`
   - 关键词：`goethite Fenton hydroxyl radical`
   - 指定出版商：`acs: 10.1021/acs.est.3c01379`
3. 运行：`py batch-wos-download.py papers.txt ./papers`
4. 输出：目录下 PDF + `_log.json`；已存在的有效 PDF（>2KB 且 `%PDF-` 开头）自动跳过

## 关键实现点

- DOI 前缀 → 出版商映射；Elsevier 需要 CrossRef 查 PII 再构造 `/pdfft` URL
- 精确标题搜索优先（加引号），无结果自动回退关键词搜索
- PDF 传输：浏览器 `fetch` → POST 本地 pdf_server（随机端口 + token 鉴权）→ 失败回退 base64 分块
- PDF 校验：`%PDF-` magic + 最小体积，空包/HTML 错误页不会被当作成功
- 单篇失败自动重试 2 次（5s / 15s 退避）
- CARSI 登录成功判定需要"认证跳转证据"或 URL 离开入口页，避免首页入口误判

## 常见问题（代理应如何处理）

| 现象 | 处理 |
|------|------|
| `bsk` 不存在 | 提示用户安装 browser-skill（GitHub: anthropics/browser-skill） |
| `no browsers connected` | 提示用户打开 Chrome，确认扩展已连接 |
| VPN 登录弹窗 | 告知用户手动登录，脚本会自动轮询 URL 直到成功 |
| CARSI 登录超时 | 检查是否在其它标签页完成登录；可配置 `auth.carsi.probe` 自动验证权限 |
| `py` 报错 exit 49 | 使用 `py` 而不是 `python` |
| 403 / 400 | 出版商限流：暂停 30-60 分钟，或调大 `download.delay` |
| 下载到空包/错误页 | 脚本自动重试并校验；检查 VPN/CARSI 权限 |

## 修改与测试

- `config.yaml` 路径可用 `BPD_CONFIG` 环境变量覆盖（多学校配置切换、测试、CI）
- `pdf_server.py` 必须设置 `PDF_SERVER_TOKEN` 才会启动（主脚本自动处理）
- 运行测试：`py test_all.py`（纯单元/集成测试，无需浏览器和 bsk）

完整中文教程见 [README.md](README.md)。
