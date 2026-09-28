# CIF 晶体数据库（`cif_database`）技术与实现说明

> 面向对象：运维 / 后端 / 前端开发者
> 覆盖范围：数据来源、下载、离线建库、化学式归一化、检索、筛选、CIF 生成与缓存、PBC 镜像、XRD 现场模拟、3D 可视化与下载、重建维护与故障排查。
> 相关页面：`/home/144/cif_database`（作业类型 `cif_database`）。

---

## 1. 一句话概括

`cif_database` 页面最初的实现是**实时代理 `api.materialsproject.org`**。2026-09 服务器出口 IP/ASN 被 Materials Project（下称 MP）封禁（表现为 `403 blocked`，发生在鉴权之前，与 API Key 无关）后，改为：

> 从 **MP 的 AWS Open Data 公开桶**下载全量构建数据（免密钥、免鉴权、无限流）→ 落地为一份本地 SQLite 索引 `data/mp_open_data/mp.db`（约 **627 MiB / 21 万条材料**）→ 页面**优先查本地库**，只有本地库缺失时才回退到线上 API。

这样页面不再依赖 `api.materialsproject.org`，也就不再被那边的 IP/ASN 封禁波及。

---

## 2. 数据来源

### 2.1 上游：Materials Project AWS Open Data

- S3 桶：`materialsproject-build.s3.amazonaws.com`
- 数据集目录：`collections/2025-09-25/`
- 特点：免密钥、免鉴权、无调用限流，适合一次性全量镜像。

该目录下按 **元素数 × 空间群** 切成三个集合（均为 `*.jsonl.gz` 分片）：

| 集合 | 目录 | 提供的字段 | 数据量（本机） |
|---|---|---|---|
| `materials` | `materials/nelements=N/symmetry_number=*.jsonl.gz` | `material_id` / `formula_pretty` / `formula_anonymous` / `elements` / `chemsys` / `symmetry` / `density` / `volume` / `nsites` / `structure` | ~771 MB |
| `thermo` | `thermo/thermo_type=GGA_GGA+U/nelements=N/*.jsonl.gz` | `energy_above_hull` / `formation_energy_per_atom` / `is_stable` | ~237 MB |
| `electronic-structure` | `electronic-structure/nelements=N/symmetry_number=*.jsonl.gz` | `band_gap` / `is_metal` / `is_gap_direct` | ~52 MB |

合计 **3,297 个分片文件，约 1,102.7 MB（1.1 GB）**。

### 2.2 关键坑：编号体系必须一致

MP 同时提供两套编号：

- **经典 MP-ID**：`mp-149`、`mp-1094120`（本页展示/检索/下载用的就是这套）；
- **下一代新式 ID**：`mp-aaahfmke` 形式，出现在 `collections/materials/version=<日期>/` 的 delta 表中（约 28 万条）。

这两套编号**零交集**：新式 ID 全表 0 个 `mp-<数字>`，既跟本页对不上，也 join 不到一起。因此 `build_mp_cifdb_index.py` 明确**只用 `collections/2025-09-25/` 这套按日期切分的 jsonl.gz**，绝不用 `version=` delta 表。三个集合（materials / thermo / electronic-structure）内部编号一致，可以按 `material_id` 合并。

### 2.3 授权

结构数据来自 Materials Project，遵循 **CC BY 4.0** 许可。前端详情页底部有明确署名。

---

## 3. 下载

下载脚本与数据同目录：`data/mp_open_data/`。提供两个等价工具，任选其一。

### 3.1 清单文件

| 文件 | 内容 |
|---|---|
| `urls.txt` | 每行一个 URL（`fetch.sh` 用） |
| `urls_with_size.txt` | 每行 `字节数 \t URL`（`fetch.py` 用，可校验完整性） |

清单由一次性脚本通过 S3 `ListObjectsV2` 生成后落盘。URL 统一前缀：

```
https://materialsproject-build.s3.amazonaws.com/collections/2025-09-25/
```

### 3.2 `fetch.sh`（bash + curl，简单版）

- `xargs -P 8` 并发；
- 目标路径 = URL 去掉前缀后的相对路径，自动 `mkdir -p`；
- 已存在**非空**文件直接跳过（重跑安全）；
- `curl -sS --retry 5 --retry-delay 3 --max-time 600 -C -`：断点续传；
- 失败写 `fetch_errors.log`，成功后写 `fetch_done.flag`。

> 注意：`fetch.sh` 只判断「文件非空」，不做字节级校验。

### 3.3 `fetch.py`（Python 标准库，推荐）

纯标准库、无第三方依赖，特性更完善：

1. **按清单大小校验**：`dst.stat().st_size == size` 才跳过，否则重下 —— 中途中断后重跑安全；
2. **先写 `.part` 再原子改名**（`os.replace`）：不会留下被误当成完整的半截文件；
3. **失败重试 5 次**，指数退避；
4. **URL 解码**：S3 key 里的 `+`（`thermo_type=GGA_GGA+U`）在 URL 中写成 `%2B`，落盘前用 `urllib.parse.unquote` 还原，否则会建出名字带 `%2B` 的目录，ETL 就找不到热力学数据；
5. **并发 + 进度上报**：`JOBS` 环境变量控制并发（默认 24），每 10 秒打印进度/速率/预计剩余时间。

```bash
cd data/mp_open_data
JOBS=24 python3 fetch.py          # 或 ./fetch.sh
```

### 3.4 本机实测下载日志（`fetch.log`）

```
清单 3297 个文件，共 1102.7 MB，并发 24
...
完成：3297/3297 文件，1102.7 MB，用时 4.9 分钟
```

平均速率约 **5–6 MB/s**，全量约 5 分钟。

### 3.5 下载后目录结构

```
data/mp_open_data/
├── materials/nelements=N/symmetry_number=*.jsonl.gz
├── thermo/thermo_type=GGA_GGA+U/nelements=N/*.jsonl.gz
├── electronic-structure/nelements=N/symmetry_number=*.jsonl.gz
├── urls.txt / urls_with_size.txt / fetch.sh / fetch.py / fetch.log
└── mp.db                     # 建库产物
```

---

## 4. 离线建库（`backend/build_mp_cifdb_index.py`）

把三套 jsonl.gz 流式合并成一份 SQLite 索引 `data/mp_open_data/mp.db`。

### 4.1 运行方式

```bash
cd backend
../.venv/bin/python build_mp_cifdb_index.py \
    --src ../data/mp_open_data \
    --out ../data/mp_open_data/mp.db
```

- 可**重复执行**：每次 `DROP TABLE` 后重建，不做增量；
- 机器内存很小（约 2 GB），全程**流式读取、分批提交**，不把任何集合整体读进内存。

### 4.2 数据库 Schema

```sql
-- 材料主表：把所有检索/展示用字段摊平成列
CREATE TABLE materials (
    material_id              TEXT PRIMARY KEY,
    formula_pretty           TEXT NOT NULL,
    formula_anonymous        TEXT,
    chemsys                  TEXT,
    nelements                INTEGER,
    nsites                   INTEGER,
    volume                   REAL,
    density                  REAL,
    density_atomic           REAL,
    crystal_system           TEXT,
    sg_symbol                TEXT,
    sg_number                INTEGER,
    lat_a, lat_b, lat_c       REAL,   -- 晶格常数直接摊平：搜索卡片每次都要显示
    alpha, beta, gamma        REAL,
    band_gap                 REAL,
    is_metal                 INTEGER,
    is_gap_direct            INTEGER,
    is_stable                INTEGER,
    energy_above_hull        REAL,
    formation_energy_per_atom REAL,
    deprecated               INTEGER NOT NULL DEFAULT 0
);

-- 结构表：zlib 压缩后的 emmet structure dict（JSON 字符串）
CREATE TABLE structures (
    material_id TEXT PRIMARY KEY,
    z           BLOB NOT NULL
);

-- 元素关联表：一条材料 × 一个元素一行，供「元素组合/包含/不包含」检索
CREATE TABLE material_elements (
    material_id TEXT NOT NULL,
    element     TEXT NOT NULL
);

-- 化学式组成归一化键（NiOOH ≡ NiHO2），DDL 与填充逻辑见 services/mp_formula.py
CREATE TABLE formula_keys (
    material_id TEXT PRIMARY KEY,
    formula_key TEXT NOT NULL
);
```

索引：

```sql
CREATE INDEX idx_mat_formula  ON materials(formula_pretty);
CREATE INDEX idx_mat_chemsys  ON materials(chemsys);
CREATE INDEX idx_mat_order    ON materials(deprecated, is_stable DESC, energy_above_hull, nsites);
CREATE INDEX idx_el_element   ON material_elements(element, material_id);
CREATE INDEX idx_el_mat       ON material_elements(material_id);
CREATE INDEX idx_fkey_formula ON formula_keys(formula_key);
```

### 4.3 构建参数（一次性写库优化）

```python
PRAGMA journal_mode = OFF     # 一次性构建，不需要崩溃恢复
PRAGMA synchronous = OFF      # 牺牲持久性换速度
PRAGMA cache_size  = -64000   # 64 MB 缓存（机器内存很小）
PRAGMA temp_store  = FILE     # 临时表落盘，避免吃内存
```

### 4.4 四阶段 ETL

| 阶段 | 动作 | 要点 |
|---|---|---|
| 1/4 `load_materials` | 读 `materials/`，`INSERT OR REPLACE` 进 `materials`；结构 dict 用 `zlib.compress(..., 6)` 存进 `structures`；每个元素写 `material_elements` | 晶格常数从 `structure.lattice` 摊平成列；每 5000 条 `commit` |
| 2/4 `load_thermo` | 读 `thermo/thermo_type=GGA_GGA+U`（兼容 `%2B` 目录名），`UPDATE materials SET energy_above_hull / formation_energy_per_atom / is_stable` | 按分片逐文件事务提交；缺分片则热力学字段留空，不报错 |
| 3/4 `load_electronic` | 读 `electronic-structure/`，`UPDATE materials SET band_gap / is_metal / is_gap_direct` | 同上 |
| 4/4 `populate_formula_keys` | 重算并写入归一化化学式键 | 见下节 |

每行解析失败只跳过该行（`iter_rows` 捕获异常），不中断整轮加载。最终 `executescript(INDEXES)` 建索引 + `ANALYZE` 更新统计信息。

### 4.5 阶段 4：化学式组成归一化（`app/services/mp_formula.py`）

**动机**：MP 的 `formula_pretty` 只按它自己的规则写一遍。例如 mp-1067482（羟基氧化镍）写作 `NiHO2`，用户按教科书写法搜 `NiOOH` 走精确匹配会 0 命中。

**做法**：用 pymatgen 先约简配比（`Ti2O4 → TiO2`），再按 Hill 系统排序、去掉空格：

```
NiOOH / NiHO2 / HNiO2  →  HNiO2
```

- 建库与查询**共用同一个函数** `formula_key()`，两边键必然一致，不存在「库里写 A、查询算成 B」；
- 只对**组成完全相同**的化学式相等，不引入假命中（实测全库 155,204 个不同 `formula_pretty` 归一化后仍是 155,204 个不同键，零碰撞）；
- 非化学式输入（含未知元素、括号解析失败、空串）返回空串，调用方退回精确匹配；
- pymatgen 会把 `Zz9Qq` 收成 `DummySpecies`，因此额外校验「全部是真实 `Element`」才算化学式。

`add_mp_formula_keys.py` 是给**已建好的老库**补这张表用的独立脚本（纯新增，不 `ALTER` 21 万行的 `materials`；老库缺表时代码可安全退回精确匹配）：

```bash
cd backend
../.venv/bin/python add_mp_formula_keys.py --db ../data/mp_open_data/mp.db
```

全新重建库时**不需要**跑它 —— `build_mp_cifdb_index.py` 最后阶段会自己写。

### 4.6 建库完成后的覆盖统计（本机实测）

| 指标 | 数值 |
|---|---|
| 材料总数 | **210,579** |
| 结构 blob 数 | 210,579 |
| 元素关联行 | 744,531 |
| 含热力学数据（`energy_above_hull` 非空） | 153,167 |
| 含带隙数据（`band_gap` 非空） | 200,487 |
| 稳定材料（`is_stable=1`） | 34,775 |
| 已弃用（`deprecated=1`） | 10,092 |
| 不同 `formula_pretty` | 155,204 |
| 不同化学体系 | 79,293 |
| 不同空间群 | 228 |
| 不同元素 | 91 |
| `formula_keys` 行数（仅未弃用） | 200,487 |
| 最大原子数 `nsites` | 1,072 |
| 超过 240 原子的条目 | 418（约 0.2%） |
| 数据库文件 | 657,186,816 B（约 **627 MiB**），page 4096 × 160,446 页 |

---

## 5. 检索（后端）

代码位置：`backend/app/routers/users.py`，`MP_LOCAL_DB` 常量 + `_mpdb_*` 系列。

### 5.1 本地优先 + 线上兜底

```python
MP_LOCAL_DB = Path(__file__).resolve().parents[3] / "data" / "mp_open_data" / "mp.db"

def _mpdb_conn():
    """只读打开本地 MP 索引。库不存在或打不开时返回 None，调用方回退到线上 API。"""
```

- 只读 URI 打开：`file:{MP_LOCAL_DB}?mode=ro`；
- 库不存在 → 返回 `None` → 回退 `_cifdb_search_remote()`（仍走 `api.materialsproject.org`）；
- **先发代码后建库也是安全的**：没库时页面退化为线上模式，只是可能撞上 MP 封禁。

### 5.2 搜索端点

```
GET /api/users/me/jobs/{job_id}/cifdb/search
    ?q=<关键字>&page=<页码>&size=<每页数>
    &sg=&lat_min=&lat_max=&el_inc=&el_exc=      # 可选筛选
```

- `size` 夹在 `[1, 50]`，`page >= 1`；
- 校验作业属于当前用户、且 `job_type == "cif_database"`；
- 关键字解析 `_mpdb_where()` 的分支顺序与线上 API 保持一致：

| 输入形态 | 判定 | SQL |
|---|---|---|
| `mp-149` | MP-ID | `m.material_id = ?` |
| `Ti O` / `Ti,O` / `Ti，O` | 元素组合（≥2 个 1–2 字母符号） | `material_id IN (... GROUP BY material_id HAVING COUNT(DISTINCT element)=N)`，语义是「**含全部这些元素**」（可再含其它），与 MP `elements` 参数同义 |
| `Ti-O` / `Li-Fe-P` | 化学体系 | `m.chemsys = ?`（元素按字母序归一化，与 MP 一致） |
| `TiO2` / `NiOOH` | 化学式 | 先 `formula_pretty = ?` **或** `material_id IN (formula_keys WHERE formula_key=?)`；精确匹配 0 命中时才退化为 `formula_pretty LIKE '%...%'` |

### 5.3 结果筛选 `_mpdb_parse_filters()` + `_mpdb_filter_sql()`

筛选条件与检索条件 **`AND`** 在一起，作用于**全库口径**（因此 `total_count` 与翻页都是筛选后的真实总数，而不是只筛当前页）：

- **空间群 `sg`**：符号（`Fm-3m` / `P2_1/c`）或编号（`225`），多个之间是「或」；
- **晶格区间 `lat_min` / `lat_max`**：**同时**作用于 `lat_a`、`lat_b`、`lat_c`，即三个边长都要落在区间内（NULL 晶格数据的条目被排除，与 SQL 对 NULL 的行为一致）；
- **包含元素 `el_inc`**：必须含全部这些元素；
- **不包含 `el_exc`**：一个都不能有。

入参校验：元素符号必须在 118 个标准符号白名单内（静态表 `_MP_ELEMENT_SYMBOLS`）；晶格上下限必须 > 0 且 min ≤ max。

### 5.4 排序与分页

```sql
ORDER BY (m.energy_above_hull IS NULL),   -- 没有热力学数据的沉到最后
         m.is_stable DESC,                 -- 稳定相优先
         m.energy_above_hull ASC,          -- 越贴近凸包越前
         m.nsites ASC,                     -- 原子少优先
         m.material_id ASC                 -- 稳定去重键
LIMIT ? OFFSET ?
```

所有检索强制 `m.deprecated = 0`（弃用条目不出现在结果里）。

**返回字段**（本地与线上逐字段一致）：`material_id`、`formula`、`formula_anonymous`、`elements`、`spacegroup`、`spacegroup_number`、`band_gap`、`is_stable`、`energy_above_hull`、`formation_energy_per_atom`、`density`、`volume`、`nsites`，另加本地库独有的 `lattice`（a/b/c）与 `angles`。

**两个总数的区别**（前端别混用）：

- `total` = 本页条数；
- `total_count` = 命中总数（带筛选算的），用于「共 N 页」；线上兜底路径为 `null`（未取总数，前端只显示第几页）。

`source` 字段标明 `"local"` / `"remote"`。

### 5.5 状态机与计费

- 点击「搜索」就是这个作业的**全部工作**（不向节点提交任何计算），所以搜索成功后把作业从 `draft/submitted/running` **条件更新**为 `done`，并只在第一次写入 `finished_at`；
- 条件 `UPDATE` 保证并发/重复搜索只置一次，且**不把 `failed` / `cancelled` 的作业复活**；
- `price > 0` 时搜索成功即扣费（失败/余额不足不扣费）；
- 这些不变量由 `backend/test_cifdb_status.py` 钉住（本地索引与线上兜底均打桩）。

---

## 6. CIF 生成与缓存

结构以 emmet `structure dict` 存在 `structures.z`（zlib）。下载/预览时现场转成 CIF 文本。

### 6.1 转换流程 `_cifdb_cif_text()`

```
zlib.decompress(structures.z) → JSON
  → Lattice(struct_dict["lattice"]["matrix"])
  → sites：按占据率降序取 species[0]["element"]，坐标取 abc 或 xyz
  → pymatgen Structure
  → struct.to_conventional()          # 惯用晶胞（失败则保持原样）
  → struct.make_supercell([sc,sc,sc]) # sc ∈ 1..4
  → CifWriter(struct, symprec=None)   # 不做对称化，写出全原子
```

- 含无序占据的结构直接报错（`结构含无序占据`）；
- **缓存**：`<作业目录同级>/mp_cif_cache/{mp_id}_s{sc}.cif`。3D 预览与 XRD 共用同一缓存文件，所以「点过 3D 再看 XRD」不会重算结构；
- 仅当本地库没有该 ID 时，才回退线上 `materials/core/` 拉结构；
- MP-ID 白名单校验 `_cifdb_mp_id()`：仅允许 `mp-<数字>`，防止路径穿越注入。

### 6.2 端点

```
GET /api/users/me/jobs/{job_id}/cifdb/{mp_id}/cif?supercell=1..4&pbc=0|1
```

返回：

```json
{
  "ok": true,
  "material_id": "mp-149",
  "cif": "<干净的原胞（或超胞）CIF 文本>",
  "cif_pbc": "<补了周期性镜像的显示用 CIF 或 null>",
  "pbc_added": 12,
  "info": {
    "formula": "Si2", "atoms": 2,
    "elements": [{"element": "Si", "count": 2}],
    "lattice": [3.84, 3.84, 3.84], "angles": [90, 90, 90],
    "poscar": "..."
  }
}
```

> **关键不变式**：`cif` 字段始终是干净的原胞/超胞 CIF，下载走的就是它，**不会把镜像原子写进下载文件**；`cif_pbc` 只供 3D 渲染。

### 6.3 周期性边界镜像 `_pbc_pad()`

CIF 按惯用胞书写，边界截断很常见（跨界的分子/配位被切断）。`pbc=1` 时在原胞四周补一圈镜像原子：

| 常量 | 值 | 含义 |
|---|---|---|
| `PBC_CUTOFF` | 3.0 Å | 只补「离胞内某原子 ≤ 3 Å」的镜像，避免 27 倍膨胀 |
| `PBC_MAX_SITES` | 120 | 原胞超过此规模就不补 |
| `PBC_MAX_PADDED` | 240 | 补完镜像总原子数超过此值就整体不补（顶爆 3D 预览上限） |

- 对 26 个平移向量做「分块计算最近距离」（每 256 个镜像一批），避免一次性开 `(26N × N)` 大矩阵；
- 镜像分数坐标落在 `[0,1)` 之外，3Dmol 不会折回胞内（实测），晶格保持不变，晶胞框仍是原来的胞；
- 补镜像失败绝不影响正常预览（`cif_pbc` 留空即可）。

---

## 7. XRD 现场模拟

```
GET /api/users/me/jobs/{job_id}/cifdb/{mp_id}/xrd
```

- 结构取该 ID 的**惯用胞 CIF**（与 3D 预览同一缓存文件）；
- **不补周期性镜像** —— 镜像原子只是显示用副本，算进结构因子会把强度算错；
- pymatgen `XRDCalculator(wavelength="CuKa")` → `get_pattern()`；
- **每次点都重算、不落盘、不缓存**。

### 7.1 规模上限

| 常量 | 值 | 含义 |
|---|---|---|
| `XRD_MAX_SITES` | 240 | 超过则返回 `ok=false` + 说明（全库仅 0.2% 条目超限）。XRD 反射数随原子数急剧增长，400 原子要 5 s 以上、出几千个峰，表格没法看 |
| `XRD_TABLE_MAX` | 80 | 峰表最多列最强的 80 个（再按 2θ 升序展示） |
| `XRD_LABEL_TOP` | 5 | 图上标注的最强 5 个峰（与 `build_surface` 页一致），表中以 ★ 标出 |

### 7.2 返回内容

- `xrd_png`：base64 data URL（`services/hetero.py` 的 `_xrd_png()`，matplotlib 画**离散竖线、不展宽**，横轴 0–90°，红字标注最强 5 峰 hkl）；
- `peaks`：峰特征表 —— `two_theta`、`d`、`intensity`、`hkl`、`hkl_count`、`multiplicity`、`labeled`；
  - 六方/三方晶系的 Miller-Bravais `(hkil)` 统一去掉 i 指数成三指数 `(hkl)`；
- 元信息：`formula`、`sites`、`wavelength_angstrom`、`two_theta_range`、`total_peaks`、`table_peaks`、`labeled_peaks`；
- 结构过大时返回 `{ok:false, message}`，前端在弹窗里原地提示。

> matplotlib pyplot 非线程安全，`_xrd_png` 经由 `services/plot_lock.py` 全局锁串行化，避免与其它出图任务串图。

---

## 8. 前端交互与可视化

核心组件：`frontend/src/components/CifDatabaseDetail.vue`（约 676 行）。
挂载点：`views/JobDetailPage.vue`（`job.job_type === 'cif_database'` 时渲染）。

### 8.1 页面结构

1. **搜索卡片**：输入框（回车或点按钮搜索），按钮上显示单次价格；示例标签 `TiO2 / Co3S4 / Si O / mp-149 / Li-Fe-P` 一键填充并搜索。
2. **结果头**：显示「共 N 条 · 本页 M 条 [· 本页 K 条符合筛选]」。搜索失败时不显示（否则会误显示「共 0 条」）。
3. **筛选条**：空间群（多选，候选项取自本页结果，也允许直接输入）、晶格 a/b/c 区间、包含元素、不包含元素、重置。
   - 客户端筛选函数 `matchesFilters()` 与后端 `_mpdb_filter_sql()` **语义一一对应**；
   - 筛选即时作用于**本页卡片**；点搜索时 `filterParams()` 把同样条件带到服务端，作用于**全部结果**。
4. **结果卡片网格**：化学式、稳定/亚稳标签、MP-ID、元素 chips、空间群、晶格参数、角度、体系能量；两个动作入口 —— 点卡片看 **3D 结构**，点底部「XRD」行看**图谱**（`@click.stop` 防止冒泡）。
5. **分页**：每页 `PAGE_SIZE = 12`，上一页/下一页；只有拿到 `total_count` 才显示「共 M 页」。
6. **3D 抽屉**（`el-drawer`，62% 宽）：
   - 超胞切换 `1×1×1` ~ `4×4×4`（`el-segmented`）；
   - 「周期性边界」开关；
   - `StructureViewer3D` 渲染；
   - 结构信息卡：化学式 / 原子数 / 晶格 / 角度 / 元素组成；
   - 「下载结构」按钮 → `StructureDownloadDialog`。
7. **XRD 弹窗**（`el-dialog`，`min(1460px, 95vw)`）：左图谱 + 参数说明，右峰特征表；窄屏（<1150px）自动堆叠。
8. **下载弹窗**：重命名 + 格式下拉，支持 `cif / vasp / xsd / car / traj / pdb / xyz / cell`，POST 到通用结构下载端点。

### 8.2 竞态处理

搜索 / 详情 / XRD 各自维护递增的请求序号（`searchReqId` / `detailReqId` / `xrdReqId`），响应回来时若序号已过期就丢弃 —— 避免慢的旧请求覆盖新结果（翻页、切换卡片、切换超胞时常见）。

### 8.3 3D 渲染（`StructureViewer3D.vue`）

- 通过 `/3Dmol-min.js` 加载 `$3Dmol`（`loadScriptOnce` 单例，`window.__3dmolLoaded` 标记）；
- 元素配色来自 `utils/elements.js`（Jmol 标准配色，全周期表），带图例；
- 固定六视角：俯视/仰视/前视/后视/左视/右视（对齐晶轴 a/b/c，直接替换 3Dmol 旋转四元数）；
- 支持全屏、下载当前视角 PNG（`viewer.pngURI()`）、原子索引标签、点选/框选（供其它页面复用）；
- 渲染内容 = `detail.cif_pbc || detail.cif`（开了 PBC 且后端确实补了镜像才用 `cif_pbc`；**下载始终用 `detail.cif`**）。
- 有 WebGL 才可交互；服务端无头浏览器可能禁用 WebGL，只能用于页面文字/布局检查。

---

## 9. 目录与文件速查

| 作用 | 路径 |
|---|---|
| 数据目录 | `data/mp_open_data/` |
| 下载清单 | `data/mp_open_data/urls.txt` / `urls_with_size.txt` |
| 下载脚本 | `data/mp_open_data/fetch.sh` / `fetch.py` |
| 原始分片 | `data/mp_open_data/{materials,thermo,electronic-structure}/` |
| **本地索引库** | `data/mp_open_data/mp.db` |
| 建库脚本 | `backend/build_mp_cifdb_index.py` |
| 补化学式键脚本 | `backend/add_mp_formula_keys.py` |
| 化学式归一化 | `backend/app/services/mp_formula.py` |
| 后端路由与查询 | `backend/app/routers/users.py`（`MP_LOCAL_DB` / `_mpdb_*` / `cifdb_*`） |
| XRD 绘图 | `backend/app/services/hetero.py`（`_xrd_png` / `_top_xrd_peaks`） |
| 状态机测试 | `backend/test_cifdb_status.py` |
| CIF 展开工具 | `backend/app/services/cif_tools.py` |
| 前端页面组件 | `frontend/src/components/CifDatabaseDetail.vue` |
| 3D 查看器 | `frontend/src/components/StructureViewer3D.vue` |
| 下载弹窗 | `frontend/src/components/StructureDownloadDialog.vue` |
| CIF 缓存 | `workspace/<邮箱>/mp_cif_cache/{mp_id}_s{sc}.cif`（即作业目录的上级） |
| 线上请求缓存 | `backend/mp_cache/`（`_mp_request` 30 分钟 TTL，降低触发 MP 限流） |

---

## 10. 重建与维护手册

### 10.1 全量重建（首次部署 / 上游数据更新）

```bash
# 1) 下载原始分片（免密钥）
cd data/mp_open_data
JOBS=24 python3 fetch.py            # 约 5 分钟 / 1.1 GB

# 2) 建库（约 21 万条）
cd ../../backend
../.venv/bin/python build_mp_cifdb_index.py \
    --src ../data/mp_open_data --out ../data/mp_open_data/mp.db

# 3) 重启后端服务（只读连接按需打开，无长驻句柄）
```

`build_mp_cifdb_index.py` 最后阶段已自动写 `formula_keys`，无需再跑 `add_mp_formula_keys.py`。

### 10.2 只补化学式键（老库缺 `formula_keys` 表）

```bash
cd backend
../.venv/bin/python add_mp_formula_keys.py --db ../data/mp_open_data/mp.db
```

### 10.3 更新上游数据版本

上游目录名是日期（当前 `2025-09-25`）。更新时：

1. 用 S3 `ListObjectsV2` 为新日期目录重新生成 `urls.txt` / `urls_with_size.txt`；
2. 同步修改 `fetch.py` 的 `BASE`、`fetch.sh` 前缀剥离逻辑、`build_mp_cifdb_index.py` 的日期相关注释；
3. 务必确认新目录仍是**经典 MP-ID** 体系，与 `thermo` / `electronic-structure` 能按 `material_id` 合并。

### 10.4 空间占用

| 内容 | 大小 |
|---|---|
| 原始分片（三套 jsonl.gz） | ~1.1 GB |
| `mp.db` | ~627 MiB |
| 合计 | ~1.7 GB |

> 仓库不包含这两部分（`.gitignore` 排除），部署时需自行下载/生成（见 README「大型数据」）。

---

## 11. 关键设计权衡

1. **本地索引优先**：彻底摆脱 MP 线上封禁；代价是需占 ~1.7 GB 磁盘，且数据不是实时最新。
2. **结构存 zlib blob 而非 CIF 文本**：结构 dict 更紧凑、字段无损，CIF 由 pymatgen 现场生成，可自由转超胞/惯用胞。
3. **晶格参数摊平成列**：搜索卡片每张都要显示，不值得每次解压结构 blob。
4. **`formula_keys` 单独建表**：`ALTER` 21 万行的 `materials` 要整表重写；新表可纯新增，老库缺表时安全退回精确匹配。
5. **只在精确匹配 0 命中时才 LIKE**：比线上宽松，但不影响有精确命中的检索（避免慢查询与噪声）。
6. **筛选下推到服务端**：保证 `total_count` 与翻页是全库口径，前端筛选只作为即时预览。
7. **PBC 镜像与下载分离**：`cif` 永远干净，`cif_pbc` 只给渲染；避免用户下载到带镜像原子的错误文件。
8. **XRD 不缓存**：结构因子随参数变化，且点开才需要；上限保护避免大结构卡死。
9. **线上兜底保留**：先发代码后建库、或库损坏时页面仍可用（只是可能撞 MP 封禁）。

---

## 12. 故障排查

| 现象 | 排查方向 |
|---|---|
| 搜索报「未配置 Materials Project API Key」 | 只在**线上兜底**路径才会要 Key。检查 `data/mp_open_data/mp.db` 是否存在/可读；本地库正常时不应出现此提示 |
| 搜索报「MP 已暂时封禁服务器 IP/ASN」 | 线上兜底路径触发。确认本地库可用即可绕开；该错误与 Key 无关，是 MP 临时封禁出口 IP |
| 搜索 0 命中但已知该物质在 MP | 检查 `formula_keys` 表是否存在；确认查询走的是归一化键（如 `NiOOH`） |
| 3D 预览原子残缺 | 检查 CIF 是否只含不对称单元（`cif_tools.expand_to_p1` 用于展开）；或开启「周期性边界」补跨界原子 |
| 结构含无序占据报错 | MP 该条目含部分占据，本实现不支持生成 CIF |
| XRD 返回 `ok=false` | 结构原子数 > 240，属预期保护 |
| 搜索结果与筛选数量对不上 | 未点搜索时筛选只作用于**本页**；点搜索后才是全库口径 |
| 建库中途失败 | 直接重跑（DROP 后重建，幂等）；确认内存/磁盘充足、`--src` 指向正确 |
| 下载 403/404 | 本地库无此 MP-ID 时回退线上；若 MP 封禁则 403；404 表示库与线上均无 |

---

## 13. 维护记录要点

- **2026-09**：服务器 IP/ASN 被 MP 封禁（`403 blocked`，鉴权前），线上直连方案不可用；
- 改用 MP AWS Open Data 全量镜像 + 本地 SQLite 索引，页面本地优先；
- 新增化学式组成归一化（`NiOOH ≡ NiHO2`）、结果筛选下推、超胞（1–4）、周期性边界镜像、XRD 现场模拟、结构格式多下载等能力。

---

*本文档依据仓库当前实现（`backend/build_mp_cifdb_index.py`、`backend/app/routers/users.py`、`backend/app/services/mp_formula.py`、`frontend/src/components/CifDatabaseDetail.vue` 等）整理，数值为本机实测（2025-09-25 数据集快照）。*
