# 听潮 · 渠道代理体系设计（v1 草案）

> 规则口径（已确认）：渠道商**只能按官方价**销售，不可改价打折；返佣**首年 30%、首年之后一律 15%**（无论客户按月付还是一次付一年，首年窗口内的钱都算 30%）；**按月结算**；客户由**渠道商代客下单 + 专属推广链接**进入；后台**复用现有 FastAPI + Jinja 管理台，新增渠道商角色**并做数据隔离。

---

## 一、商务规则（先把口径钉死，避免后期扯皮）

### 1.1 价格与返佣

| 项目 | 规则 |
|---|---|
| 售价 | 官方统一价（专业版 ¥268/账号/月，限时价；原价 ¥468）。系统锁价，渠道端**没有改价入口** |
| 首年返佣 | 客户授权**首次开通日起 365 天内**的全部实付金额 × **30%** |
| 续费返佣 | 365 天之后的全部实付金额 × **15%** |
| 返佣基数 | 实付金额（不含退款、不含我方赠送时长对应的折算） |
| 企业版 | **第一版不开放给渠道**（价格一事一议、需售前介入），只让渠道卖专业版 |

「首年」按**客户主体**算而不是按订单算：同一个 `user_id`（授权账号）从第一笔已收款订单时间起算 365 天，窗口内所有付款都算首年。这样"客户先买了 1 个月，又补了 11 个月"和"一次买 12 个月"结果一致，渠道商不会吃亏也不会占便宜。

### 1.2 客户归属

- 通过渠道专属链接下单、或由渠道商代客下单的客户，**永久归属该渠道商**，其后续续费持续产生 15% 返佣。
- **报备保护期 30 天**：渠道商先报备客户（公司名 + 联系人手机号），30 天内该客户成交归他；过期未成交则释放。
- **撞单规则**：以「先报备且在有效期内」为准；都未报备时以「先收款」为准。争议由我方在后台仲裁，仲裁结果写入订单备注。
- **回收**：客户连续 **12 个月**无任何已收款订单 → 自动掉入公海，后续成交不再给原渠道返佣（防止渠道"占坑不干活"）。

### 1.3 结算

- 每月 1 日系统自动汇总上月返佣流水生成**渠道账单**（`bill_month = YYYY-MM`）。
- 每月 5 日前渠道商在后台**确认账单**并开票；我方财务对公支付，**月结后 15 个工作日内**打款。
- **起结门槛**：单月应结 < ¥200 时自动滚存到下月，减少小额打款成本。
- **退款红冲**：订单退款时生成负数返佣记录，从下期账单中抵扣；已打款的走线下追回。
- 发票：渠道商向我方开具"服务费/佣金"类发票；无开票能力的个人渠道需签代扣协议（这一条要财务确认，见 §7）。

---

## 二、数据模型（SQLite，新增 5 张表 + 改 1 张表）

```sql
-- 渠道商
CREATE TABLE IF NOT EXISTS partners (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,            -- 推广码，用于专属链接 ?code=
  name TEXT NOT NULL,                   -- 公司/个人名称
  contact TEXT, phone TEXT,             -- 联系人
  login_name TEXT UNIQUE NOT NULL,      -- 渠道后台登录名
  password_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',-- active / disabled / pending_review
  rate_first REAL NOT NULL DEFAULT 0.30,
  rate_renew REAL NOT NULL DEFAULT 0.15,
  bank_name TEXT, bank_account TEXT, tax_no TEXT,   -- 收款与开票信息
  note TEXT,
  created_at TEXT NOT NULL
);

-- 客户报备（归属仲裁的依据）
CREATE TABLE IF NOT EXISTS partner_leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partner_id INTEGER NOT NULL,
  customer_name TEXT NOT NULL,
  contact_phone TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending/converted/expired/rejected
  user_id INTEGER,                          -- 转化后关联 users.id
  reported_at TEXT NOT NULL,
  expire_at TEXT NOT NULL,                  -- reported_at + 30 天
  UNIQUE(customer_name, contact_phone)
);

-- 订单
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT UNIQUE NOT NULL,
  partner_id INTEGER NOT NULL,
  user_id INTEGER,                          -- 关联授权账号（开通后回填）
  plan TEXT NOT NULL DEFAULT 'pro',
  months INTEGER NOT NULL,
  amount REAL NOT NULL,                     -- 官方价 × 月数，渠道不可改
  source TEXT NOT NULL,                     -- partner_manual(代客) / partner_link(客户自点)
  status TEXT NOT NULL DEFAULT 'pending',   -- pending/paid/refunded/cancelled
  is_first_year INTEGER,                    -- 1=首年窗口内，0=续费
  paid_at TEXT, confirmed_by TEXT,          -- 核销人（人工确认收款）
  note TEXT,
  created_at TEXT NOT NULL
);

-- 返佣流水（一笔已收款订单一行）
CREATE TABLE IF NOT EXISTS commissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL UNIQUE,
  partner_id INTEGER NOT NULL,
  base_amount REAL NOT NULL,
  rate REAL NOT NULL,
  amount REAL NOT NULL,                     -- 退款订单为负数
  bill_month TEXT NOT NULL,                 -- YYYY-MM
  status TEXT NOT NULL DEFAULT 'pending',   -- pending/settled/rejected
  created_at TEXT NOT NULL
);

-- 渠道月度账单
CREATE TABLE IF NOT EXISTS partner_bills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partner_id INTEGER NOT NULL,
  bill_month TEXT NOT NULL,
  total_base REAL NOT NULL,
  total_amount REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft',     -- draft/confirmed/paid/disputed
  detail_csv TEXT,                          -- 导出文件路径
  settled_at TEXT,
  UNIQUE(partner_id, bill_month)
);

-- 现有授权表加两列（迁移脚本，幂等）
ALTER TABLE users ADD COLUMN source_partner_id INTEGER;   -- 归属渠道，NULL=官方直销
ALTER TABLE users ADD COLUMN first_paid_at TEXT;          -- 首年窗口的起算点
```

`users.first_paid_at` 是"首年窗口"的唯一事实来源：一旦写入就不再改，之后每笔订单都拿 `paid_at` 与它比较，判断落在 365 天内还是外。

---

## 三、下单与开通链路

第一版**不接在线支付**（微信/支付宝商户号 + 备案 + 对账是独立工程），走"渠道商先向我方付款 → 我方后台核销 → 自动开通授权"，全程在系统里留痕，等量起来再升级成在线支付。

```
渠道商后台「代客下单」                客户点专属链接
   │  填客户名/手机号/选时长             │  /buy?code=xxxx  →  落地页选时长下单
   └────────────┬───────────────────────┘
                ▼
        生成订单 status=pending（金额按官方价锁定，前端不可传价）
                ▼
   渠道商银行转账给我方 → 上传付款凭证 → 我方后台「确认收款」
                ▼
   事务内原子完成：order=paid → 创建/关联 users（生成账号+随机初始密码，
   写 source_partner_id、first_paid_at）→ 授权到期=今天+months
   → 计算 is_first_year → 写 commissions 流水 → 通知渠道商
```

关键约束：**金额一律服务端按价目表计算**，前端只提交 `months`，杜绝渠道改价与篡改金额。

---

## 四、后台与权限

| 入口 | 域名 | 能看到什么 |
|---|---|---|
| 我方管理台 | `admin.tingchao.cengfengkeji.cn`（现有） | 全量：用户、订单、渠道商管理、账单审核、核销、价格表、撞单仲裁 |
| 渠道商后台 | `partner.tingchao.cengfengkeji.cn`（需新增一条 A 记录解析到 49.235.168.97） | **仅自己**：客户、订单、返佣账单、推广链接、结算账户 |

隔离实现（三选一里最稳的做法）：

1. 会话里带 `role` + `partner_id`；渠道端所有查询**强制** `WHERE partner_id = :session_partner_id`，不接受任何来自请求参数的 partner_id；
2. 单资源访问（如 `/orders/123`）加 owner 校验，越权返回 404（不用 403，避免泄露资源是否存在）；
3. 上线前用一组越权用例回归：渠道 A 拿自己的 cookie 去请求渠道 B 的 order/lead/bill，必须全部 404。

**合规红线（必须写进渠道协议并在系统里落实）**：渠道商后台只出现商务数据（客户名称、账号、开通时长、金额、返佣）。客户在听潮里抓取的**评论、昵称、主页链接、线索内容属于客户资产且涉个人信息**，绝不出现在渠道端任何页面、接口和导出文件里。

### 渠道商后台功能（MVP）

概览（本月成交/金额/应返佣、累计、待结算）· 我的客户（列表 + 新增报备，显示保护期倒计时）· 开单（代客下单、生成专属链接与二维码）· 订单（待收款/已开通/已退款）· 返佣（月度账单 + 明细 + 导出 CSV）· 结算资料（收款账户、开票信息，改动需我方审核）· 消息（出账、审核结果通知）。

### 我方后台新增

渠道商管理（开通/停用/审核资料，可单独调返佣比例）· 订单核销（确认收款即自动开通）· 账单（生成/确认/标记已付/导出）· 报备与撞单仲裁 · 官方价目表维护。

---

## 五、返佣计算逻辑（唯一入口，避免多处实现不一致）

```python
def calc_rate(user, paid_at) -> float:
    if not user.first_paid_at:
        return user.rate_first          # 该客户的第一笔钱，必然算首年
    first = parse(user.first_paid_at)
    return user.rate_first if (paid_at - first).days < 365 else user.rate_renew
```

- 只在「订单确认收款」这一个时机计算并写 `commissions`，不做定时重算；
- 退款生成负数流水并标 `bill_month = 退款发生月`，自然从下期账单抵扣；
- 账单生成后若发现漏算，用补录流水进当月账单，不追溯改历史账单。

---

## 六、分期落地

**P0（约 1～2 周，可先签 3 家种子渠道跑通）**：5 张表 + users 加列迁移；渠道角色与登录隔离；代客下单 + 我方核销自动开通；返佣流水手工生成 + 月账单导出 CSV。

**P1**：专属链接落地页与二维码；报备保护期与撞单判定自动化；账单确认流与状态机；出账通知；渠道端数据看板。

**P2**：在线支付自动开通（需商户号）；渠道分级（LV1/LV2 不同返佣、阶梯奖励）；防刷与实名/执照审核；发票与税务流程线上化。

---

## 七、需要你和财务确认的 5 件事

1. **收款方式**：第一版走"渠道先付我方、后台核销开通"能否接受？还是必须先接微信/支付宝在线支付（会多 1～2 周且需商户号）？
2. **渠道准入门槛**：是否要求营业执照 + 预付款/保证金？不要求的话，个人渠道刷单、乱承诺售后的风险要写进协议。
3. **税与发票**：佣金支付给个人时涉及代扣代缴，走个体户/公司开票最省事——财务口径需要你确认。
4. **起结门槛 ¥200、12 个月无成交回收公海、报备保护期 30 天**：这三个数字我按行业常见值先填了，你拍板。
5. **企业版**是否给渠道卖（我建议第一版不给，价格一事一议容易被渠道乱报价）。
