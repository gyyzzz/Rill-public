# 更新日志

所有重要的变更都会记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [0.2.1] - 2026-09-23

### 新增

#### Metrics工具增强
- 新增 `ImsCallAlertRate`（IMS域智能呼叫接通率）指标查询
  - 查询 `r_pd_scp_volteasv_vonr_15min_ne` 表
  - 支持直接比率字段查询（平均、最小、最大值）
  - 指标类型：`is_direct_rate`（直接比率）
  
#### 提示词优化
- 对「音视频锚定」指标设置特殊处理规则
  - 波动容忍度从 5% 提升到 10%
  - 对比权重降低 50%
- 优化指标波动判断标准说明
  - 比率类指标：变化超过 5% 为显著变化
  - 成功率类指标：变化超过 5% 为显著变化
  - 特殊指标：变化超过 10% 才视为异常

#### 数据准备脚本
- `scripts/insert_24h_data_batch.py`: 批量插入最近 24 小时测试数据
- `scripts/create_metrics_tables.py`: 创建 metrics 数据库表结构
- `scripts/test_metrics_tool.py`: 测试 metrics 工具功能

### 变更

#### Metrics工具优化
- 简化趋势判断逻辑，改为由 AI 在提示词中分析
- 移除代码中的 `trend` 字段计算
- 增强 `NaN` 值处理，确保返回有效数值

#### 配置优化
- 支持更多指标类型：绝对值、成功率、直接比率
- 优化提示词结构，提供更清晰的分析指导

### 当前支持的指标

#### NCP 业务模块
- **metrics.r_NCP_User_1min_ne**
  - 新通话应答话务量（erl）：绝对值
  - 新通话DC应答话务量（erl）：绝对值

- **metrics.r_NCP_Media_1min_ne**
  - 音视频锚定：成功率（特殊指标，波动容忍度 10%）
  - 小程序列表下载：成功率

- **metrics.r_pd_scp_volteasv_vonr_15min_ne**
  - IMS域智能呼叫触发(VONR)：成功率
  - IMS域智能呼叫接通率(VONR)：直接比率 ⭐ 新增

#### SNE 业务模块
- **metrics.r_YWPT_HTTP_1min_ne**
  - 呼叫时间控制：成功率

#### SCPAS 业务模块
- **metrics.r_pd_scp_volteas_1min_ne**
  - IMS域智能呼叫触发：成功率

---

## [0.2.0] - 2026-09-21

### 新增

#### Metrics查询工具
- 新增 `MetricsQueryTool` 用于查询ClickHouse指标数据
- 支持NCP、SNE、SCPAS三大业务模块的指标查询
- 支持时间范围查询（默认最近30分钟）
- 支持与前一日同一时间段对比分析
- 支持URL编码的密码解析（`%23` → `#` 等特殊字符）
- 自动计算成功率并生成趋势分析
- 固定格式的报告输出

#### 配置项
- `ORIONXCORE_ENABLE_METRICS`: 启用metrics工具
- `ORIONXCORE_METRICS_DATABASE_URL`: ClickHouse数据库连接URL
- `ORIONXCORE_METRICS_DATABASE_TIMEOUT_SECONDS`: 查询超时时间
- `ORIONXCORE_METRICS_TIMEZONE`: 时区设置
- `ORIONXCORE_METRICS_DEFAULT_WINDOW_MINUTES`: 默认查询窗口

### 变更

#### 依赖更新
- 将 `clickhouse-connect` 从可选依赖移至主依赖
- 确保metrics工具开箱即用，无需额外安装

#### URL解析增强
- 支持多种ClickHouse URL格式
- 正确处理URL编码的用户名和密码
- 支持特殊字符：`#`, `@`, `/`, `:`, `?`, `&` 等

### 修复

#### URL解析问题
- 修复手写正则表达式解析URL不够健壮的问题
- 使用标准库 `urllib.parse.urlparse` 和 `unquote`
- 解决密码包含特殊字符时认证失败的问题

### 文档

#### 新增文档
- `docs/INSTALL.md`: 详细安装指南（在线/离线）
- `docs/dependencies.md`: 依赖说明文档
- `docs/QUICKSTART_zh.md`: 中文快速开始指南

#### 更新文档
- README.md: 添加安装文档链接
- README_zh.md: 添加安装文档链接
- .env.example: 添加metrics工具配置示例

### 测试

#### 新增测试
- `tests/test_metrics_tool.py`: 完整的metrics工具测试套件
  - 测试工具属性和配置
  - 测试时间范围生成
  - 测试URL解析（包括编码密码）
  - 测试指标查询和对比分析
  - 测试错误处理

#### 测试覆盖率
- 13个测试用例全部通过
- 覆盖主要功能和边界情况

---

## [0.1.0] - 初始版本

### 新增

#### 核心功能
- FastAPI服务框架
- OpenAI兼容的模型集成
- 服务端Agent循环
- REST和SSE接口

#### 工具
- Terminal工具：命令执行、风险控制
- Database工具：ClickHouse集成、Text-to-SQL
- Filesystem工具：文件读写、路径沙盒
- Chart工具：数据可视化
- Alert工具：告警查询（PostgreSQL/OpenGauss）

#### 配置
- 环境变量驱动配置
- .env文件支持
- 灵活的工具启用/禁用

#### CLI
- `orionx ask`: 单次提问
- `orionx chat`: 交互式对话
- 会话管理

---

## 版本规划

### [0.3.0] - 计划中

#### 新增功能
- Web UI界面
- 用户认证和授权
- API速率限制
- 审计日志

#### 性能优化
- 流式响应优化
- 数据库连接池
- 缓存机制

#### 工具增强
- 更多数据库支持（MySQL、PostgreSQL）
- 更多工具类型（HTTP请求、代码执行）

---

## 升级指南

### 从 0.1.x 升级到 0.2.0

#### 1. 更新依赖

```bash
# 激活虚拟环境
source .venv/bin/activate

# 升级依赖
pip install --upgrade -e .
```

#### 2. 更新配置

```bash
# 查看新增配置项
grep "METRICS" .env.example

# 添加metrics配置到 .env
vim .env
```

#### 3. 验证升级

```bash
# 检查新工具
python -c "from orionxcore.tools import MetricsQueryTool; print('OK')"

# 运行测试
pytest tests/test_metrics_tool.py -v
```

### 兼容性说明

- **配置兼容**: 所有旧配置继续有效，无需修改
- **API兼容**: 所有现有API保持不变
- **工具兼容**: 所有现有工具功能不变

---

## 贡献

欢迎贡献代码、报告问题或提出建议！

### 提交Issue

请提供以下信息：
- 操作系统和Python版本
- 错误信息和堆栈跟踪
- 复现步骤
- 预期行为和实际行为

### 提交PR

请确保：
- 代码通过所有测试
- 添加必要的测试用例
- 更新相关文档
- 遵循代码风格

---

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

---

## 致谢

感谢所有贡献者和用户的支持！