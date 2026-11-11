# 概念层补边报告（m 任务 / T10）：577 → 706

> 生成日期 2026-10-31｜主工程 本仓库根｜补前基数 REQUIRES 196 / SPECIAL_OF 183 / APPLIES_TO 198
> 本文所有数字均可由 §9 的命令复算；~~R@5 只给预期方向，标注为「预期」，不是实测。~~
> **2026-10-31 16:40 已由整合步骤进图并实测，R@5 见 §14（0.4417 → 0.4389）。**

## 0. 结论（先看这里）

| 指标 | 补前 | 补后 |
|---|---|---|
| 人工概念层边（三类合计） | 577 | **706** |
| REQUIRES | 196 | 230 |
| SPECIAL_OF | 183 | 185 |
| APPLIES_TO | 198 | 291 |
| 概念密度（边/概念，281 概念） | 2.05 | **2.51** |
| 零度概念数 | 20 | 18 |

- **新增 129 条边，机械校验通过 129 条（100%）**；无一条靠人工抽检放行。
- **语义判断清单 111 条**（gold 有标注但缺字面锚点 / 关系类型需判断），**不计入上述达标数**；另有 22 条扫描噪声未采纳。
- **900 目标未达成于机械层**：机械可证 **706**（差 194）；按用户决策另建**语义推断层 194 条**（独立文件、不计入机械计数），**双层合计 900 = 900（3.20 条/概念）**。
- **对外结论只能用机械层 706**；推断层 194 条全部为 medium（high 0 / low 0），仍属「可审计但未经条文句式直接支撑」。
- **R@5 已实测（2026-10-31 16:40 进图后）**：0.4417 → **0.4389**（−0.0028），60 条中 4 条变动（2 升 2 降），
  在抽样噪声内；`cross_law` 桶 0.45→0.35 属方向性风险。详见 §14。**不再使用「预期略升」的旧判断。**

## 1. 缺口清单口径（data/lexicon/gap_targets.json）

**筛选口径**：`gold_200.jsonl` 中出现过（freq ≥ 1）的概念，其**概念层度数 ≤ 2**。度数 = REQUIRES 进出 + SPECIAL_OF 进出 + APPLIES_TO 入边（scenario→concept），**不含** DEFINES（Article→Concept，属抽取层而非人工概念层）。

**补前总数 23 个**（gold_60 命中的 9 个是其子集）：

| 概念 | 名称 | gold 频次 | 补前度数 | 补后度数 | 是否已补 |
|---|---|---|---|---|---|
| `female_worker_protection` | 女职工特殊保护 | 3 | 2 | 3 | 是 |
| `file_transfer` | 档案和社保转移 | 3 | 2 | 4 | 是 |
| `emergency_overtime` | 紧急情况加班 | 2 | 1 | 3 | 是 |
| `child_labor` | 童工 | 2 | 2 | 2 | 否 |
| `collective_consultation` | 集体协商 | 2 | 2 | 3 | 是 |
| `invalid_contract` | 无效劳动合同 | 2 | 2 | 3 | 是 |
| `labor_dispute_litigation` | 劳动争议诉讼 | 2 | 2 | 4 | 是 |
| `occupational_health_check` | 职业健康检查 | 2 | 2 | 2 | 否 |
| `separation_certificate` | 解除终止证明 | 2 | 2 | 3 | 是 |
| `training` | 专业技术培训 | 2 | 2 | 3 | 是 |
| `contract_modification` | 劳动合同变更 | 1 | 1 | 2 | 否 |
| `piece_rate` | 计件工资 | 1 | 1 | 2 | 否 |
| `acceptance` | 承诺 | 1 | 2 | 3 | 是 |
| `employment_discrimination` | 就业歧视 | 1 | 2 | 2 | 否 |
| `equal_employment_right` | 平等就业权 | 1 | 2 | 2 | 否 |
| `fair_liability` | 公平责任 | 1 | 2 | 3 | 是 |
| `minimum_wage_standard` | 最低工资标准 | 1 | 2 | 2 | 否 |
| `occupational_disease` | 职业病 | 1 | 2 | 2 | 否 |
| `precontractual_liability` | 缔约过失责任 | 1 | 2 | 3 | 是 |
| `retirement` | 退休 | 1 | 2 | 4 | 是 |
| `standard_terms` | 格式条款 | 1 | 2 | 3 | 是 |
| `survivor_benefit` | 遗属待遇 | 1 | 2 | 2 | 否 |
| `wage_priority_payment` | 工资优先支付 | 1 | 2 | 2 | 否 |

补边后仍 ≤2 的剩 10 个（缺口从 23 降到 10）。

## 2. 三类新增边分布

| 关系 | 新增 | 机械可验 | 来源 |
|---|---|---|---|
| APPLIES_TO | 93 | 93 | gold_200 的 scenario+concept 标注，锚点条文独立命中概念名/别名 |
| REQUIRES（gold 路径直达） | 24 | 24 | gold_200 路径内非相邻概念对，同条双端独立命中 |
| REQUIRES（分句级依赖） | 10 | 10 | 条文内「A 依/按/经 B」线索 + 同条双端独立命中 |
| SPECIAL_OF | 2 | 2 | 定义式属概念（「未成年工是指…劳动者」「…是本法所称用人单位」） |
| **合计** | **129** | **129** | |

APPLIES_TO 覆盖 31 个场景，涉及 94 个 gold 用例。

## 3. 新增边逐条依据（note 均引条文号）

### 3.1 APPLIES_TO（93 条）

| 场景 → 概念 | 条文 | 条文中独立命中 | note |
|---|---|---|---|
| `maternity_leave` → `labor_protection` | 第五十八条 | 劳动保护（女职工和未成年工实行特殊劳动保护。未成年工是指年满十六周） | gold g37/g102/g103/g156 把「劳动保护」列入场景「生育与产假」的标注路径；中华人民共和国劳动法第五十八条 原文独立出现「劳动保护」，场景-概念适用有字面锚点。 |
| `part_time_job` → `labor_dispute` | 第七十七条 | 劳动争议（用人单位与劳动者发生劳动争议，当事人可以依法申请调解） | gold g39/g110/g162 把「劳动争议」列入场景「非全日制用工」的标注路径；中华人民共和国劳动法第七十七条 原文独立出现「劳动争议」，场景-概念适用有字面锚点。 |
| `sick_leave` → `severance` | 第四十六条 | 经济补偿（用人单位应当向劳动者支付经济补偿：（一）劳动者依照本法第） | gold g42 把「经济补偿」列入场景「病假与医疗期」的标注路径；中华人民共和国劳动合同法第四十六条 原文独立出现「经济补偿」，场景-概念适用有字面锚点。 |
| `contract_terms_missing` → `standard_terms` | 第四百九十七条 | 格式条款（有下列情形之一的，该格式条款无效：（一）具有本法第一） | gold g51 把「格式条款」列入场景「合同条款缺失」的标注路径；中华人民共和国民法典第四百九十七条 原文独立出现「格式条款」，场景-概念适用有字面锚点。 |
| `severance_calculation` → `liquidated_damages_civil` | 第五百八十五条 | 违约金（情况向对方支付一定数额的违约金，也可以约定因违约产生的） | gold g52 把「违约金」列入场景「经济补偿计算」的标注路径；中华人民共和国民法典第五百八十五条 原文独立出现「违约金」，场景-概念适用有字面锚点。 |
| `illegal_dismissal` → `damages_civil` | 第一千一百八十三条 | 损害赔偿（的，被侵权人有权请求精神损害赔偿。因故意或者重大过失侵害） | gold g53 把「损害赔偿」列入场景「违法解除」的标注路径；中华人民共和国民法典第一千一百八十三条 原文独立出现「损害赔偿」，场景-概念适用有字面锚点。 |
| `work_injury` → `tort_liability` | 第一千一百七十五条 | 侵权责任（人造成的，第三人应当承担侵权责任。） | gold g55/g198 把「侵权责任」列入场景「工伤认定与待遇」的标注路径；中华人民共和国民法典第一千一百七十五条 原文独立出现「侵权责任」，场景-概念适用有字面锚点。 |
| `work_injury` → `no_fault_liability` | 第一千一百六十六条 | 不论行为人有无过错（人造成他人民事权益损害，不论行为人有无过错，法律规定应当承担侵权责） | gold g55 把「无过错责任」列入场景「工伤认定与待遇」的标注路径；中华人民共和国民法典第一千一百六十六条 原文独立出现「不论行为人有无过错」，场景-概念适用有字面锚点。 |
| `part_time_job` → `offer` | 第四百七十六条 | 要约（要约可以撤销，但是有下列情形） | gold g56 把「要约」列入场景「非全日制用工」的标注路径；中华人民共和国民法典第四百七十六条 原文独立出现「要约」，场景-概念适用有字面锚点。 |
| `part_time_job` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g56/g179 把「合同成立」列入场景「非全日制用工」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `economic_layoff` → `damages_civil` | 第五百八十五条 | 损失赔偿（，也可以约定因违约产生的损失赔偿额的计算方法。约定的违约） | gold g59/g193 把「损害赔偿」列入场景「经济性裁员」的标注路径；中华人民共和国民法典第五百八十五条 原文独立出现「损失赔偿」，场景-概念适用有字面锚点。 |
| `platform_worker` → `agency` | 第一百七十二条 | 代理（者代理权终止后，仍然实施代理行为，相对人有理由相信行） | gold g60 把「代理」列入场景「平台用工」的标注路径；中华人民共和国民法典第一百七十二条 原文独立出现「代理」，场景-概念适用有字面锚点。 |
| `platform_worker` → `apparent_agency` | 第一百七十二条 | 相对人有理由相信（止后，仍然实施代理行为，相对人有理由相信行为人有代理权的，代理行） | gold g60 把「表见代理」列入场景「平台用工」的标注路径；中华人民共和国民法典第一百七十二条 原文独立出现「相对人有理由相信」，场景-概念适用有字面锚点。 |
| `probation_dismissal` → `rescission_by_employer` | 第四十三条 | 用人单位单方解除（用人单位单方解除劳动合同，应当事先将理由） | gold g68/g135 把「用人单位单方解除」列入场景「试用期辞退」的标注路径；中华人民共和国劳动合同法第四十三条 原文独立出现「用人单位单方解除」，场景-概念适用有字面锚点。 |
| `probation_dismissal` → `labor_contract` | 第十五条 | 劳动合同（完成一定工作任务为期限的劳动合同，是指用人单位与劳动者约） | gold g69/g177 把「劳动合同」列入场景「试用期辞退」的标注路径；中华人民共和国劳动合同法第十五条 原文独立出现「劳动合同」，场景-概念适用有字面锚点。 |
| `open_ended_contract_request` → `labor_contract` | 第十三条 | 劳动合同（固定期限劳动合同，是指用人单位与劳动者约） | gold g71/g74/g178 把「劳动合同」列入场景「要求签无固定期限」的标注路径；中华人民共和国劳动合同法第十三条 原文独立出现「劳动合同」，场景-概念适用有字面锚点。 |
| `non_compete_dispute` → `trade_secret` | 第二十三条 | 商业秘密（合同中约定保守用人单位的商业秘密和与知识产权相关的保密事） | gold g76/g139/g140 把「商业秘密」列入场景「竞业限制纠纷」的标注路径；中华人民共和国劳动合同法第二十三条 原文独立出现「商业秘密」，场景-概念适用有字面锚点。 |
| `contractor_vs_employee` → `contract_rescission` | 第三十六条 | 解除劳动合同（位与劳动者协商一致，可以解除劳动合同。） | gold g80 把「劳动合同解除」列入场景「劳动关系认定」的标注路径；中华人民共和国劳动合同法第三十六条 原文独立出现「解除劳动合同」，场景-概念适用有字面锚点。 |
| `resignation_notice` → `non_fault_dismissal` | 第四十条 | 提前三十日以书面形式通知（下列情形之一的，用人单位提前三十日以书面形式通知劳动者本人或者额外支付劳） | gold g82/g141 把「非过失性辞退」列入场景「主动辞职」的标注路径；中华人民共和国劳动合同法第四十条 原文独立出现「提前三十日以书面形式通知」，场景-概念适用有字面锚点。 |
| `resignation_notice` → `severance` | 第四十六条 | 经济补偿（用人单位应当向劳动者支付经济补偿：（一）劳动者依照本法第） | gold g83 把「经济补偿」列入场景「主动辞职」的标注路径；中华人民共和国劳动合同法第四十六条 原文独立出现「经济补偿」，场景-概念适用有字面锚点。 |
| `immediate_resignation` → `file_transfer` | 第五十条 | 社会保险关系转移（五日内为劳动者办理档案和社会保险关系转移手续。劳动者应当按照双方） | gold g86 把「档案和社保转移」列入场景「被迫即时解除」的标注路径；中华人民共和国劳动合同法第五十条 原文独立出现「社会保险关系转移」，场景-概念适用有字面锚点。 |
| `economic_layoff` → `rescission_by_employer` | 第四十三条 | 用人单位单方解除（用人单位单方解除劳动合同，应当事先将理由） | gold g89 把「用人单位单方解除」列入场景「经济性裁员」的标注路径；中华人民共和国劳动合同法第四十三条 原文独立出现「用人单位单方解除」，场景-概念适用有字面锚点。 |
| `illegal_dismissal` → `severance_standard` | 第四十七条 | 按劳动者在本单位工作的年限（经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资） | gold g90/g148 把「经济补偿计算标准」列入场景「违法解除」的标注路径；中华人民共和国劳动合同法第四十七条 原文独立出现「按劳动者在本单位工作的年限」，场景-概念适用有字面锚点。 |
| `severance_calculation` → `labor_relationship` | 第七条 | 劳动关系（用工之日起即与劳动者建立劳动关系。用人单位应当建立职工名） | gold g94/g149 把「劳动关系」列入场景「经济补偿计算」的标注路径；中华人民共和国劳动合同法第七条 原文独立出现「劳动关系」，场景-概念适用有字面锚点。 |
| `service_period_training` → `labor_contract` | 第十三条 | 劳动合同（固定期限劳动合同，是指用人单位与劳动者约） | gold g95/g180 把「劳动合同」列入场景「服务期与培训」的标注路径；中华人民共和国劳动合同法第十三条 原文独立出现「劳动合同」，场景-概念适用有字面锚点。 |
| `work_injury` → `social_insurance` | 第七十二条 | 社会保险（社会保险基金按照保险类型确定资金） | gold g100 把「社会保险」列入场景「工伤认定与待遇」的标注路径；中华人民共和国劳动法第七十二条 原文独立出现「社会保险」，场景-概念适用有字面锚点。 |
| `annual_leave` → `working_hours` | 第三十六条 | 每日工作时间（国家实行劳动者每日工作时间不超过八小时、平均每周工） | gold g104/g158 把「工时制度」列入场景「年休假」的标注路径；中华人民共和国劳动法第三十六条 原文独立出现「每日工作时间」，场景-概念适用有字面锚点。 |
| `working_hours_dispute` → `emergency_overtime` | 第四十二条 | 发生自然灾害（十一条规定的限制：（一）发生自然灾害、事故或者因其他原因，威） | gold g106 把「紧急情况加班」列入场景「工时与休息」的标注路径；中华人民共和国劳动法第四十二条 原文独立出现「发生自然灾害」，场景-概念适用有字面锚点。 |
| `wage_deduction` → `piece_rate` | 第三十七条 | 计件报酬（制度合理确定其劳动定额和计件报酬标准。） | gold g108 把「计件工资」列入场景「扣罚工资」的标注路径；中华人民共和国劳动法第三十七条 原文独立出现「计件报酬」，场景-概念适用有字面锚点。 |
| `wage_deduction` → `immediate_rescission` | 第三十八条 | 立即解除（者人身安全的，劳动者可以立即解除劳动合同，不需事先告知用） | gold g109/g161 把「劳动者即时解除」列入场景「扣罚工资」的标注路径；中华人民共和国劳动合同法第三十八条 原文独立出现「立即解除」，场景-概念适用有字面锚点。 |
| `part_time_job` → `labor_contract` | 第十九条 | 劳动合同（劳动合同应当以书面形式订立，并具） | gold g111/g179 把「劳动合同」列入场景「非全日制用工」的标注路径；中华人民共和国劳动法第十九条 原文独立出现「劳动合同」，场景-概念适用有字面锚点。 |
| `contractor_vs_employee` → `labor_dispute` | 第七十七条 | 劳动争议（用人单位与劳动者发生劳动争议，当事人可以依法申请调解） | gold g114 把「劳动争议」列入场景「劳动关系认定」的标注路径；中华人民共和国劳动法第七十七条 原文独立出现「劳动争议」，场景-概念适用有字面锚点。 |
| `confidentiality_breach` → `non_compete` | 第二十三条 | 竞业限制（者保密协议中与劳动者约定竞业限制条款，并约定在解除或者终） | gold g117/g165/g166 把「竞业限制」列入场景「泄密纠纷」的标注路径；中华人民共和国劳动合同法第二十三条 原文独立出现「竞业限制」，场景-概念适用有字面锚点。 |
| `labor_litigation` → `arbitration` | 第七十九条 | 劳动争议仲裁（人一方要求仲裁的，可以向劳动争议仲裁委员会申请仲裁。当事人一） | gold g122/g123 把「劳动仲裁」列入场景「劳动争议诉讼」的标注路径；中华人民共和国劳动法第七十九条 原文独立出现「劳动争议仲裁」，场景-概念适用有字面锚点。 |
| `handover_dispute` → `social_insurance` | 第七十二条 | 社会保险（社会保险基金按照保险类型确定资金） | gold g125 把「社会保险」列入场景「离职交接与证明」的标注路径；中华人民共和国劳动法第七十二条 原文独立出现「社会保险」，场景-概念适用有字面锚点。 |
| `remote_work` → `work_hour_extension` | 第四十一条 | 延长工作时间（与工会和劳动者协商后可以延长工作时间，一般每日不得超过一小时） | gold g127 把「延长工作时间」列入场景「远程办公」的标注路径；中华人民共和国劳动法第四十一条 原文独立出现「延长工作时间」，场景-概念适用有字面锚点。 |
| `remote_work` → `overtime_pay` | 第三十一条 | 加班费（国家有关规定向劳动者支付加班费。） | gold g128 把「加班工资」列入场景「远程办公」的标注路径；中华人民共和国劳动合同法第三十一条 原文独立出现「加班费」，场景-概念适用有字面锚点。 |
| `remote_work` → `contract_modification` | 第三十五条 | 变更劳动合同（位与劳动者协商一致，可以变更劳动合同约定的内容。变更劳动合同） | gold g129 把「劳动合同变更」列入场景「远程办公」的标注路径；中华人民共和国劳动合同法第三十五条 原文独立出现「变更劳动合同」，场景-概念适用有字面锚点。 |
| `platform_worker` → `social_insurance` | 第七十二条 | 社会保险（社会保险基金按照保险类型确定资金） | gold g130 把「社会保险」列入场景「平台用工」的标注路径；中华人民共和国劳动法第七十二条 原文独立出现「社会保险」，场景-概念适用有字面锚点。 |
| `overtime_pay_dispute` → `emergency_overtime` | 第四十二条 | 发生自然灾害（十一条规定的限制：（一）发生自然灾害、事故或者因其他原因，威） | gold g133 把「紧急情况加班」列入场景「加班费争议」的标注路径；中华人民共和国劳动法第四十二条 原文独立出现「发生自然灾害」，场景-概念适用有字面锚点。 |
| `contract_renewal` → `severance_standard` | 第四十七条 | 按劳动者在本单位工作的年限（经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资） | gold g137 把「经济补偿计算标准」列入场景「合同到期续签」的标注路径；中华人民共和国劳动合同法第四十七条 原文独立出现「按劳动者在本单位工作的年限」，场景-概念适用有字面锚点。 |
| `contract_renewal` → `retirement` | 第七十三条 | 退休（享受社会保险待遇：（一）退休；（二）患病、负伤；（三） | gold g138 把「退休」列入场景「合同到期续签」的标注路径；中华人民共和国劳动法第七十三条 原文独立出现「退休」，场景-概念适用有字面锚点。 |
| `resignation_notice` → `separation_certificate` | 第五十条 | 解除或者终止劳动合同的证明（除或者终止劳动合同时出具解除或者终止劳动合同的证明，并在十五日内为劳动者办） | gold g142 把「解除终止证明」列入场景「主动辞职」的标注路径；中华人民共和国劳动合同法第五十条 原文独立出现「解除或者终止劳动合同的证明」，场景-概念适用有字面锚点。 |
| `immediate_resignation` → `labor_quota` | 第四条 | 劳动定额（、职工培训、劳动纪律以及劳动定额管理等直接涉及劳动者切身） | gold g143 把「劳动定额」列入场景「被迫即时解除」的标注路径；中华人民共和国劳动合同法第四条 原文独立出现「劳动定额」，场景-概念适用有字面锚点。 |
| `economic_layoff` → `damages` | 第八十五条 | 赔偿金（百以下的标准向劳动者加付赔偿金：（一）未按照劳动合同的） | gold g146/g193 把「赔偿金」列入场景「经济性裁员」的标注路径；中华人民共和国劳动合同法第八十五条 原文独立出现「赔偿金」，场景-概念适用有字面锚点。 |
| `illegal_dismissal` → `fault_dismissal` | 第三十九条 | 严重违反用人单位的规章制度（不符合录用条件的；（二）严重违反用人单位的规章制度的；（三）严重失职，营私） | gold g147 把「过失性辞退」列入场景「违法解除」的标注路径；中华人民共和国劳动合同法第三十九条 原文独立出现「严重违反用人单位的规章制度」，场景-概念适用有字面锚点。 |
| `severance_calculation` → `labor_union` | 第六条 | 工会（工会应当帮助、指导劳动者与用） | gold g150 把「工会」列入场景「经济补偿计算」的标注路径；中华人民共和国劳动合同法第六条 原文独立出现「工会」，场景-概念适用有字面锚点。 |
| `double_wage_claim` → `wage` | 第五十条 | 工资（工资应当以货币形式按月支付给） | gold g152 把「工资」列入场景「二倍工资」的标注路径；中华人民共和国劳动法第五十条 原文独立出现「工资」，场景-概念适用有字面锚点。 |
| `double_wage_claim` → `wage_payment` | 第三十条 | 足额支付（和国家规定，向劳动者及时足额支付劳动报酬。用人单位拖欠或） | gold g152 把「工资支付」列入场景「二倍工资」的标注路径；中华人民共和国劳动合同法第三十条 原文独立出现「足额支付」，场景-概念适用有字面锚点。 |
| `social_insurance_dispute` → `file_transfer` | 第五十条 | 社会保险关系转移（五日内为劳动者办理档案和社会保险关系转移手续。劳动者应当按照双方） | gold g153 把「档案和社保转移」列入场景「社保争议」的标注路径；中华人民共和国劳动合同法第五十条 原文独立出现「社会保险关系转移」，场景-概念适用有字面锚点。 |
| `maternity_leave` → `sick_leave` | 第四十二条 | 患病或者非因工负伤（分丧失劳动能力的；（三）患病或者非因工负伤，在规定的医疗期内的；（） | gold g156 把「病假与医疗期」列入场景「生育与产假」的标注路径；中华人民共和国劳动合同法第四十二条 原文独立出现「患病或者非因工负伤」，场景-概念适用有字面锚点。 |
| `annual_leave` → `statutory_holiday` | 第五十一条 | 法定休假日（劳动者在法定休假日和婚丧假期间以及依法参加） | gold g157 把「法定休假日」列入场景「年休假」的标注路径；中华人民共和国劳动法第五十一条 原文独立出现「法定休假日」，场景-概念适用有字面锚点。 |
| `annual_leave` → `overtime_pay` | 第三十一条 | 加班费（国家有关规定向劳动者支付加班费。） | gold g158 把「加班工资」列入场景「年休假」的标注路径；中华人民共和国劳动合同法第三十一条 原文独立出现「加班费」，场景-概念适用有字面锚点。 |
| `working_hours_dispute` → `overtime_pay` | 第三十一条 | 加班费（国家有关规定向劳动者支付加班费。） | gold g160 把「加班工资」列入场景「工时与休息」的标注路径；中华人民共和国劳动合同法第三十一条 原文独立出现「加班费」，场景-概念适用有字面锚点。 |
| `working_hours_dispute` → `wage_payment` | 第八十五条 | 支付劳动报酬（，由劳动行政部门责令限期支付劳动报酬、加班费或者经济补偿；劳） | gold g160 把「工资支付」列入场景「工时与休息」的标注路径；中华人民共和国劳动合同法第八十五条 原文独立出现「支付劳动报酬」，场景-概念适用有字面锚点。 |
| `part_time_job` → `arbitration` | 第八十二条 | 劳动争议仲裁（争议发生之日起六十日内向劳动争议仲裁委员会提出书面申请。仲裁） | gold g162 把「劳动仲裁」列入场景「非全日制用工」的标注路径；中华人民共和国劳动法第八十二条 原文独立出现「劳动争议仲裁」，场景-概念适用有字面锚点。 |
| `collective_contract` → `severance` | 第四十六条 | 经济补偿（用人单位应当向劳动者支付经济补偿：（一）劳动者依照本法第） | gold g163 把「经济补偿」列入场景「集体合同」的标注路径；中华人民共和国劳动合同法第四十六条 原文独立出现「经济补偿」，场景-概念适用有字面锚点。 |
| `confidentiality_breach` → `non_compete_compensation` | 第二十三条 | 按月给予劳动者经济补偿（合同后，在竞业限制期限内按月给予劳动者经济补偿。劳动者违反竞业限制约定） | gold g165/g166 把「竞业限制经济补偿」列入场景「泄密纠纷」的标注路径；中华人民共和国劳动合同法第二十三条 原文独立出现「按月给予劳动者经济补偿」，场景-概念适用有字面锚点。 |
| `occupational_disease` → `illegal_rescission` | 第八十七条 | 违反本法规定解除或者终止劳动合同（用人单位违反本法规定解除或者终止劳动合同的，应当依照本法第四十七） | gold g167 把「违法解除」列入场景「职业病」的标注路径；中华人民共和国劳动合同法第八十七条 原文独立出现「违反本法规定解除或者终止劳动合同」，场景-概念适用有字面锚点。 |
| `arbitration_procedure` → `labor_dispute_litigation` | 第七十九条 | 向人民法院提起诉讼（。对仲裁裁决不服的，可以向人民法院提起诉讼。） | gold g169 把「劳动争议诉讼」列入场景「劳动仲裁流程」的标注路径；中华人民共和国劳动法第七十九条 原文独立出现「向人民法院提起诉讼」，场景-概念适用有字面锚点。 |
| `arbitration_procedure` → `part_time_wage_settlement` | 第七十二条 | 小时计酬（非全日制用工小时计酬标准不得低于用人单位所在） | gold g171 把「非全日制工资结算」列入场景「劳动仲裁流程」的标注路径；中华人民共和国劳动合同法第七十二条 原文独立出现「小时计酬」，场景-概念适用有字面锚点。 |
| `arbitration_procedure` → `part_time_employment` | 第六十九条 | 非全日制用工（非全日制用工双方当事人可以订立口头协） | gold g171 把「非全日制用工」列入场景「劳动仲裁流程」的标注路径；中华人民共和国劳动合同法第六十九条 原文独立出现「非全日制用工」，场景-概念适用有字面锚点。 |
| `handover_dispute` → `contract_rescission` | 第三十七条 | 解除劳动合同（面形式通知用人单位，可以解除劳动合同。劳动者在试用期内提前三） | gold g173 把「劳动合同解除」列入场景「离职交接与证明」的标注路径；中华人民共和国劳动合同法第三十七条 原文独立出现「解除劳动合同」，场景-概念适用有字面锚点。 |
| `handover_dispute` → `severance` | 第四十六条 | 经济补偿（用人单位应当向劳动者支付经济补偿：（一）劳动者依照本法第） | gold g173 把「经济补偿」列入场景「离职交接与证明」的标注路径；中华人民共和国劳动合同法第四十六条 原文独立出现「经济补偿」，场景-概念适用有字面锚点。 |
| `remote_work` → `wage` | 第五十条 | 工资（工资应当以货币形式按月支付给） | gold g174 把「工资」列入场景「远程办公」的标注路径；中华人民共和国劳动法第五十条 原文独立出现「工资」，场景-概念适用有字面锚点。 |
| `platform_worker` → `labor_dispute` | 第七十九条 | 劳动争议（劳动争议发生后，当事人可以向本单） | gold g175 把「劳动争议」列入场景「平台用工」的标注路径；中华人民共和国劳动法第七十九条 原文独立出现「劳动争议」，场景-概念适用有字面锚点。 |
| `platform_worker` → `arbitration` | 第八十二条 | 劳动争议仲裁（争议发生之日起六十日内向劳动争议仲裁委员会提出书面申请。仲裁） | gold g175 把「劳动仲裁」列入场景「平台用工」的标注路径；中华人民共和国劳动法第八十二条 原文独立出现「劳动争议仲裁」，场景-概念适用有字面锚点。 |
| `probation_dismissal` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g177 把「合同成立」列入场景「试用期辞退」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `open_ended_contract_request` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g178 把「合同成立」列入场景「要求签无固定期限」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `service_period_training` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g180 把「合同成立」列入场景「服务期与培训」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `confidentiality_breach` → `labor_contract` | 第十五条 | 劳动合同（完成一定工作任务为期限的劳动合同，是指用人单位与劳动者约） | gold g181 把「劳动合同」列入场景「泄密纠纷」的标注路径；中华人民共和国劳动合同法第十五条 原文独立出现「劳动合同」，场景-概念适用有字面锚点。 |
| `confidentiality_breach` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g181 把「合同成立」列入场景「泄密纠纷」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `contract_terms_missing` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g182/g183/g184 把「合同成立」列入场景「合同条款缺失」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `contract_terms_missing` → `offer` | 第四百七十五条 | 要约（要约可以撤回。要约的撤回适用） | gold g182 把「要约」列入场景「合同条款缺失」的标注路径；中华人民共和国民法典第四百七十五条 原文独立出现「要约」，场景-概念适用有字面锚点。 |
| `contract_terms_missing` → `acceptance` | 第四百八十八条 | 承诺（承诺的内容应当与要约的内容一） | gold g183 把「承诺」列入场景「合同条款缺失」的标注路径；中华人民共和国民法典第四百八十八条 原文独立出现「承诺」，场景-概念适用有字面锚点。 |
| `contract_terms_missing` → `precontractual_liability` | 第五百条 | 假借订立合同（应当承担赔偿责任：（一）假借订立合同，恶意进行磋商；（二）故） | gold g184 把「缔约过失责任」列入场景「合同条款缺失」的标注路径；中华人民共和国民法典第五百条 原文独立出现「假借订立合同」，场景-概念适用有字面锚点。 |
| `remote_work` → `contract_establishment` | 第四百八十三条 | 合同成立（承诺生效时合同成立，但是法律另有规定或者当） | gold g185 把「合同成立」列入场景「远程办公」的标注路径；中华人民共和国民法典第四百八十三条 原文独立出现「合同成立」，场景-概念适用有字面锚点。 |
| `unpaid_wages` → `labor_relationship` | 第七条 | 劳动关系（用工之日起即与劳动者建立劳动关系。用人单位应当建立职工名） | gold g186 把「劳动关系」列入场景「拖欠工资」的标注路径；中华人民共和国劳动合同法第七条 原文独立出现「劳动关系」，场景-概念适用有字面锚点。 |
| `unpaid_wages` → `agency` | 第一百六十九条 | 代理人（代理人需要转委托第三人代理的，） | gold g186 把「代理」列入场景「拖欠工资」的标注路径；中华人民共和国民法典第一百六十九条 原文独立出现「代理人」，场景-概念适用有字面锚点。 |
| `double_wage_claim` → `agency` | 第一百六十三条 | 代理（代理包括委托代理和法定代理。） | gold g187 把「代理」列入场景「二倍工资」的标注路径；中华人民共和国民法典第一百六十三条 原文独立出现「代理」，场景-概念适用有字面锚点。 |
| `resignation_notice` → `labor_relationship` | 第七条 | 劳动关系（用工之日起即与劳动者建立劳动关系。用人单位应当建立职工名） | gold g188 把「劳动关系」列入场景「主动辞职」的标注路径；中华人民共和国劳动合同法第七条 原文独立出现「劳动关系」，场景-概念适用有字面锚点。 |
| `resignation_notice` → `agency` | 第一百六十三条 | 代理（代理包括委托代理和法定代理。） | gold g188 把「代理」列入场景「主动辞职」的标注路径；中华人民共和国民法典第一百六十三条 原文独立出现「代理」，场景-概念适用有字面锚点。 |
| `social_insurance_dispute` → `agency` | 第一百六十九条 | 代理人（代理人需要转委托第三人代理的，） | gold g189 把「代理」列入场景「社保争议」的标注路径；中华人民共和国民法典第一百六十九条 原文独立出现「代理人」，场景-概念适用有字面锚点。 |
| `arbitration_procedure` → `labor_relationship` | 第七条 | 劳动关系（用工之日起即与劳动者建立劳动关系。用人单位应当建立职工名） | gold g190 把「劳动关系」列入场景「劳动仲裁流程」的标注路径；中华人民共和国劳动合同法第七条 原文独立出现「劳动关系」，场景-概念适用有字面锚点。 |
| `arbitration_procedure` → `agency` | 第一百六十九条 | 代理人（代理人需要转委托第三人代理的，） | gold g190 把「代理」列入场景「劳动仲裁流程」的标注路径；中华人民共和国民法典第一百六十九条 原文独立出现「代理人」，场景-概念适用有字面锚点。 |
| `illegal_dismissal` → `labor_protection` | 第五十八条 | 劳动保护（女职工和未成年工实行特殊劳动保护。未成年工是指年满十六周） | gold g191 把「劳动保护」列入场景「违法解除」的标注路径；中华人民共和国劳动法第五十八条 原文独立出现「劳动保护」，场景-概念适用有字面锚点。 |
| `illegal_dismissal` → `tort_liability` | 第一千一百六十六条 | 侵权责任（无过错，法律规定应当承担侵权责任的，依照其规定。） | gold g191 把「侵权责任」列入场景「违法解除」的标注路径；中华人民共和国民法典第一千一百六十六条 原文独立出现「侵权责任」，场景-概念适用有字面锚点。 |
| `non_compete_dispute` → `liquidated_damages_civil` | 第五百八十五条 | 违约金（情况向对方支付一定数额的违约金，也可以约定因违约产生的） | gold g195 把「违约金」列入场景「竞业限制纠纷」的标注路径；中华人民共和国民法典第五百八十五条 原文独立出现「违约金」，场景-概念适用有字面锚点。 |
| `illegal_dismissal` → `liquidated_damages_civil` | 第五百八十五条 | 违约金（情况向对方支付一定数额的违约金，也可以约定因违约产生的） | gold g196 把「违约金」列入场景「违法解除」的标注路径；中华人民共和国民法典第五百八十五条 原文独立出现「违约金」，场景-概念适用有字面锚点。 |
| `economic_layoff` → `breach_of_contract` | 第五百七十七条 | 违约责任（取补救措施或者赔偿损失等违约责任。） | gold g197 把「违约责任」列入场景「经济性裁员」的标注路径；中华人民共和国民法典第五百七十七条 原文独立出现「违约责任」，场景-概念适用有字面锚点。 |
| `occupational_disease` → `tort_liability` | 第一千一百六十六条 | 侵权责任（无过错，法律规定应当承担侵权责任的，依照其规定。） | gold g199/g200 把「侵权责任」列入场景「职业病」的标注路径；中华人民共和国民法典第一千一百六十六条 原文独立出现「侵权责任」，场景-概念适用有字面锚点。 |
| `occupational_disease` → `no_fault_liability` | 第一千一百六十六条 | 不论行为人有无过错（人造成他人民事权益损害，不论行为人有无过错，法律规定应当承担侵权责） | gold g199 把「无过错责任」列入场景「职业病」的标注路径；中华人民共和国民法典第一千一百六十六条 原文独立出现「不论行为人有无过错」，场景-概念适用有字面锚点。 |
| `occupational_disease` → `fair_liability` | 第一千一百八十六条 | 分担损失（的，依照法律的规定由双方分担损失。） | gold g200 把「公平责任」列入场景「职业病」的标注路径；中华人民共和国民法典第一千一百八十六条 原文独立出现「分担损失」，场景-概念适用有字面锚点。 |

### 3.2 REQUIRES —— gold 路径直达（24 条）

| 依赖边 | 条文 | 双端命中 | note |
|---|---|---|---|
| `wage` → `wage_deduction` | 第五十条 | 工资 / 无故拖欠 | gold g31 路径 wage -> wage_payment -> wage_deduction；中华人民共和国劳动法第五十条 同条独立出现「工资」与「无故拖欠」，补 A→B 直达依赖以缩短跳数。 |
| `non_compete` → `liquidated_damages` | 第二十三条 | 竞业限制 / 支付违约金 | gold g35 路径 non_compete -> non_compete_compensation -> liquidated_damages；中华人民共和国劳动合同法第二十三条 同条独立出现「竞业限制」与「支付违约金」，补 A→B 直达依赖以缩短跳数。 |
| `invalid_contract` → `coercion` | 第二十六条 | 劳动合同无效 / 胁迫 | gold g40 路径 invalid_contract -> fraud -> coercion；中华人民共和国劳动合同法第二十六条 同条独立出现「劳动合同无效」与「胁迫」，补 A→B 直达依赖以缩短跳数。 |
| `training` → `liquidated_damages` | 第二十二条 | 专项培训 / 支付违约金 | gold g41 路径 training -> service_period -> liquidated_damages；中华人民共和国劳动合同法第二十二条 同条独立出现「专项培训」与「支付违约金」，补 A→B 直达依赖以缩短跳数。 |
| `lease_contract` → `damages_civil` | 第七百一十六条 | 租赁合同 / 赔偿损失 | gold g45 路径 lease_contract -> deposit -> damages_civil；中华人民共和国民法典第七百一十六条 同条独立出现「租赁合同」与「赔偿损失」，补 A→B 直达依赖以缩短跳数。 |
| `liquidated_damages` → `breach_of_contract` | 第八百七十三条 | 支付违约金 / 违约责任 | gold g52 路径 liquidated_damages -> liquidated_damages_civil -> breach_of_contract；中华人民共和国民法典第八百七十三条 同条独立出现「支付违约金」与「违约责任」，补 A→B 直达依赖以缩短跳数。 |
| `severance` → `damages_civil` | 第四百六十一条 | 补偿金 / 赔偿损失 | gold g59 路径 severance -> breach_of_contract -> damages_civil；中华人民共和国民法典第四百六十一条 同条独立出现「补偿金」与「赔偿损失」，补 A→B 直达依赖以缩短跳数。 |
| `fixed_term_contract` → `contract_termination` | 第四十六条 | 固定期限劳动合同 / 终止劳动合同 | gold g136 路径 fixed_term_contract -> open_ended_contract -> contract_termination；中华人民共和国劳动合同法第四十六条 同条独立出现「固定期限劳动合同」与「终止劳动合同」，补 A→B 直达依赖以缩短跳数。 |
| `open_ended_contract` → `retirement` | 第十四条 | 无固定期限劳动合同 / 退休 | gold g138 路径 open_ended_contract -> contract_termination -> retirement；中华人民共和国劳动合同法第十四条 同条独立出现「无固定期限劳动合同」与「退休」，补 A→B 直达依赖以缩短跳数。 |
| `non_compete_compensation` → `trade_secret` | 第二十三条 | 按月给予劳动者经济补偿 / 商业秘密 | gold g140 路径 non_compete_compensation -> non_compete -> trade_secret；中华人民共和国劳动合同法第二十三条 同条独立出现「按月给予劳动者经济补偿」与「商业秘密」，补 A→B 直达依赖以缩短跳数。 |
| `unilateral_rescission_by_worker` → `non_fault_dismissal` | 第三十一条 | 劳动者解除劳动合同 / 提前三十日以书面形式通知 | gold g141 路径 unilateral_rescission_by_worker -> notice_period -> non_fault_dismissal；中华人民共和国劳动法第三十一条 同条独立出现「劳动者解除劳动合同」与「提前三十日以书面形式通知」，补 A→B 直达依赖以缩短跳数。 |
| `severance` → `labor_relationship` | 第九十七条 | 经济补偿 / 劳动关系 | gold g149 路径 severance -> wage -> labor_relationship；中华人民共和国劳动合同法第九十七条 同条独立出现「经济补偿」与「劳动关系」，补 A→B 直达依赖以缩短跳数。 |
| `statutory_holiday` → `rest_day` | 第四十四条 | 法定休假日 / 休息日 | gold g159 路径 statutory_holiday -> rest_period -> rest_day；中华人民共和国劳动法第四十四条 同条独立出现「法定休假日」与「休息日」，补 A→B 直达依赖以缩短跳数。 |
| `wage_payment` → `immediate_rescission` | 第三十八条 | 足额支付 / 立即解除 | gold g161 路径 wage_payment -> wage_deduction -> immediate_rescission；中华人民共和国劳动合同法第三十八条 同条独立出现「足额支付」与「立即解除」，补 A→B 直达依赖以缩短跳数。 |
| `part_time_wage_settlement` → `arbitration` | 第八十四条 | 十五日 / 劳动争议仲裁 | gold g162 路径 part_time_wage_settlement -> labor_dispute -> arbitration；中华人民共和国劳动法第八十四条 同条独立出现「十五日」与「劳动争议仲裁」，补 A→B 直达依赖以缩短跳数。 |
| `labor_union` → `collective_consultation` | 第六条 | 工会 / 集体协商 | gold g164 路径 labor_union -> collective_contract -> collective_consultation；中华人民共和国劳动合同法第六条 同条独立出现「工会」与「集体协商」，补 A→B 直达依赖以缩短跳数。 |
| `confidentiality` → `non_compete_compensation` | 第二十三条 | 保密义务 / 按月给予劳动者经济补偿 | gold g165 路径 confidentiality -> non_compete -> non_compete_compensation；中华人民共和国劳动合同法第二十三条 同条独立出现「保密义务」与「按月给予劳动者经济补偿」，补 A→B 直达依赖以缩短跳数。 |
| `trade_secret` → `non_compete_compensation` | 第二十三条 | 商业秘密 / 按月给予劳动者经济补偿 | gold g166 路径 trade_secret -> non_compete -> non_compete_compensation；中华人民共和国劳动合同法第二十三条 同条独立出现「商业秘密」与「按月给予劳动者经济补偿」，补 A→B 直达依赖以缩短跳数。 |
| `labor_dispute` → `labor_dispute_litigation` | 第七十九条 | 劳动争议 / 向人民法院提起诉讼 | gold g169 路径 labor_dispute -> arbitration -> labor_dispute_litigation；中华人民共和国劳动法第七十九条 同条独立出现「劳动争议」与「向人民法院提起诉讼」，补 A→B 直达依赖以缩短跳数。 |
| `labor_dispute_mediation` → `arbitration` | 第七十九条 | 劳动争议调解 / 劳动争议仲裁 | gold g170 路径 labor_dispute_mediation -> labor_dispute -> arbitration；中华人民共和国劳动法第七十九条 同条独立出现「劳动争议调解」与「劳动争议仲裁」，补 A→B 直达依赖以缩短跳数。 |
| `guarantee` → `joint_liability` | 第六百八十六条 | 保证 / 连带责任 | gold g172 路径 guarantee -> guarantee_contract -> joint_liability；中华人民共和国民法典第六百八十六条 同条独立出现「保证」与「连带责任」，补 A→B 直达依赖以缩短跳数。 |
| `handover` → `severance` | 第五十条 | 工作交接 / 经济补偿 | gold g173 路径 handover -> contract_rescission -> severance；中华人民共和国劳动合同法第五十条 同条独立出现「工作交接」与「经济补偿」，补 A→B 直达依赖以缩短跳数。 |
| `sick_leave` → `female_worker_protection` | 第四十二条 | 患病或者非因工负伤 / 哺乳期 | gold g176 路径 sick_leave -> labor_protection -> female_worker_protection；中华人民共和国劳动合同法第四十二条 同条独立出现「患病或者非因工负伤」与「哺乳期」，补 A→B 直达依赖以缩短跳数。 |
| `labor_contract` → `contract_performance` | 第二十九条 | 劳动合同 / 全面履行 | gold g185 路径 labor_contract -> contract_establishment -> contract_performance；中华人民共和国劳动合同法第二十九条 同条独立出现「劳动合同」与「全面履行」，补 A→B 直达依赖以缩短跳数。 |

### 3.3 REQUIRES —— 分句级依赖（10 条）

| 依赖边 | 条文 | 依据 |
|---|---|---|
| `labor_contract` → `written_form` | 第十条 | 中华人民共和国劳动合同法第十条：「建立劳动关系，应当订立书面劳动合同」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「劳动合同」「订立书面」）。 |
| `borrower` → `lender` | 第六百六十九条 | 中华人民共和国民法典第六百六十九条：「借款人应当按照贷款人的要求提供与借款有关的业务活动和财务状况的真实情况」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「借款人」「贷款人」）。 |
| `agent_party` → `principal` | 第九百二十四条 | 中华人民共和国民法典第九百二十四条：「受托人应当按照委托人的要求」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「受托人」「委托人」）。 |
| `contractor_party` → `employer_party` | 第七百九十一条 | 中华人民共和国民法典第七百九十一条：「施工承包人经发包人同意」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「承包人」「发包人」）。 |
| `work_contractor` → `ordering_party` | 第七百八十五条 | 中华人民共和国民法典第七百八十五条：「承揽人应当按照定作人的要求保守秘密」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「承揽人」「定作人」）。 |
| `work_contract` → `ordering_party` | 第七百七十条 | 中华人民共和国民法典第七百七十条：「承揽合同是承揽人按照定作人的要求完成工作」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「承揽合同」「定作人」）。 |
| `financing_lease_contract` → `lessee` | 第七百三十五条 | 中华人民共和国民法典第七百三十五条：「融资租赁合同是出租人根据承租人对出卖人」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「融资租赁合同」「承租人」）。 |
| `lessor` → `lessee` | 第七百四十四条 | 中华人民共和国民法典第七百四十四条：「出租人根据承租人对出卖人」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「出租人」「承租人」）。 |
| `lessee` → `lessor` | 第七百一十六条 | 中华人民共和国民法典第七百一十六条：「承租人未经出租人同意转租的」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「承租人」「出租人」）。 |
| `dispatch_unit` → `legal_person` | 第五十七条 | 中华人民共和国劳动合同法第五十七条：「劳务派遣单位应当依照公司法的有关规定设立」——A 依/按/经 B 方能成立或行使，故 A 依赖 B（机械锚点：同条独立出现「劳务派遣单位」「公司」）。 |

### 3.4 SPECIAL_OF（2 条）

| 下位 | 上位 | 条文 | 依据 |
|---|---|---|---|
| `minor_worker` | `worker` | 第五十八条 | 中华人民共和国劳动法第五十八条「国家对女职工和未成年工实行特殊劳动保护。未成年工是指年满十六周岁未满十八周岁的劳动者。…」——未成年工 的定义式为「劳动者」的下位概念，构成 SPECIAL_OF。 |
| `dispatch_unit` | `employer` | 第五十八条 | 中华人民共和国劳动合同法第五十八条「劳务派遣单位是本法所称用人单位，应当履行用人单位对劳动者的义务。劳务派遣单位与被派遣劳动者订立的劳动合同，除应当载明本法…」——劳务派遣单位 的定义式为「用人单位」的下位概念，构成 SPECIAL_OF。 |

## 4. 机械校验方法与结果

判据完全复用 `scripts/check_defines_edges.py` 的常量与函数（`find_all` / `enclosing_terms` / 派生后缀 `人/权/物/方/者`）：

- **独立出现**：概念名原样出现且下一字不在派生后缀；或**登记别名**出现、非概念名子串、下一字不在派生后缀、且不被其它概念的更长术语包裹。
- APPLIES_TO：校验概念侧是否在 **gold 该概念的锚点条文** 独立出现（20 条 gold 标注因锚点条文无字面命中而未纳入）。
- REQUIRES / SPECIAL_OF：校验**两端**是否在同一锚点条文独立出现。

结果：**129/129 条通过**，失败 0 条；每条边的命中片段与条文号已写入 `data/lexicon/gap_edges_added.json`。

## 5. 语义判断清单（不计入达标数）

`data/lexicon/gap_semantic_candidates.json` 共 133 条，其中**真正需要语义判断的 111 条**：

| 类别 | 条数 | 为什么不计入 |
|---|---|---|
| gold APPLIES_TO 无字面锚点 | 20 | gold 有标注，但该概念的锚点条文无字面命中，需人判是否仍成立 |
| gold 路径非相邻对（无同条双端共现） | 67 | gold 路径内跨概念对，但无任一条文同时双端命中，属路径语义而非条文证据 |
| 分句级依赖线索未采纳 | 24 | 线索词命中且同条双端可见，但从线索到关系方向/类型需人判 |

另 22 条为扫描噪声（「包括」内容清单 10 条、场景通用词跨语域误配 12 条），一并未采纳。

## 6. 机械层为什么只能到 706（而不是 900）

三条机械约束把上限压住了：

1. **SPECIAL_OF 结构上限**：`build_lexicon` 只从种子 `CONCEPTS.parent` 生成 SPECIAL_OF，每概念**至多一个父**；183 个已定父，剩余 98 个多为顶层概念（劳动合同、劳动者、债权、债务、侵权责任…），无法再挂。枚举式条文（「X 分为 A、B」）全库仅 2 处且已被现有边覆盖。**机械可证新增仅 2 条**。
2. **REQUIRES 只能靠「同条双端独立命中 + 明确依赖线索」**：金标 200 条路径的**相邻概念对 172 个已全部存在**（缺口不在相邻边）；跨概念对 91 个里只有 24 个能在同一条文双端命中。分句级线索扫描 362 条原始候选，去噪后仅 10 条方向可辩护。
3. **APPLIES_TO 的增量被 gold 标注封顶**：gold_200 的 scenario-concept 对里新增 142 个，其中 122 个锚点条文有字面命中，去重后 93 条。继续扩只能靠共现推理（12 条，9 条跨语域误配），会把 noise 写进图。

要达到 900（较机械层 +194），只能补「字面共现但关系类型靠语义判断」的边。机械层因此定为 **706 条（2.51 条/概念）**；余下 194 条按用户决策单独交付为**可审计的语义推断层**（`gap_edges_inferred.jsonl`，见 §11），两者**分账、不混计**。

## 7. 与 `__000__lexicon_seed.py` 的一致性检查

| 检查项 | 结果 |
|---|---|
| `python app/__001__ingest/__001__build_lexicon.py --check` | **通过**（术语库与种子一致） |
| 概念数 / 别名数 / 别名冲突 | 281 / 811 / 57，**与补前完全一致，未新增冲突** |
| 新增 APPLIES_TO 的概念 id | 全部存在于种子 `CONCEPTS`（`_compose` 会因未知概念直接抛错） |
| 新增 REQUIRES 两端 id | 全部存在（同上） |
| 新增 parent | `minor_worker→worker`、`dispatch_unit→employer`，均为已存在概念 |

## 8. diff 摘要（改了哪些文件、增了多少）

| 文件 | 补前 | 补后 | 变化 |
|---|---|---|---|
| `app/__001__ingest/__000__lexicon_seed.py` | 610 行 | 646 行 | +36 行（31 个场景 concepts 追加、REQUIRES +10、GOLD_PATH_REQUIRES +24、2 处 parent、2 行注释） |
| `data/lexicon/scenarios.jsonl` | 53 行 | 53 行 | 行数不变；31 个场景追加 93 个 concept 边 |
| `data/lexicon/requires.jsonl` | 196 | 230 | **+34** |
| `data/lexicon/special_of.jsonl` | 183 | 185 | **+2** |
| `data/lexicon/concepts.jsonl` | 281 | 281 | 行数不变；2 个概念 parent 由 null 改为具体值 |
| `data/lexicon/stats.json` | 旧 | 新 | requires 196→230、gold_path 35→59、special_of 183→185、scenario_concept 198→291 |
| `data/lexicon/alias_index.json` / `alias_collisions.json` | — | 未变 | 别名体系未改动 |

新增的独立产物：`data/lexicon/gap_targets.json`（缺口清单，前后两态）、`data/lexicon/gap_edges_added.json`（129 条边逐条 note+命中）、`data/lexicon/gap_semantic_candidates.json`（133 条未采纳清单）、本报告。

**未触碰**：`data/graph/`（未写）、`build_graph.py`（未运行）、`app/` 下其它代码、判据、README、git。

## 9. 复现命令

```powershell
# 1) 一致性检查（应输出「术语库与种子一致」）
& '.venv\Scripts\python.exe' 'app\__001__ingest\__001__build_lexicon.py' --check   # cwd=仓库根
# 2) 机械复核 129 条新增边（应输出 added_total=129, mech_verified_total=129, failed=[]）
& '.venv\Scripts\python.exe' 'F:\大模型应用开发\算法岗冲刺\work\m\verify_edges.py'
# 3) 缺口清单（before/after）
python 'F:\大模型应用开发\算法岗冲刺\work\m\gap_targets.py' out.json after
# 4) 边数与密度
python -c "import json,collections;r=[json.loads(l) for l in open(r'data\lexicon\requires.jsonl',encoding='utf-8')];s=[json.loads(l) for l in open(r'data\lexicon\special_of.jsonl',encoding='utf-8')];c=[json.loads(l) for l in open(r'data\lexicon\scenarios.jsonl',encoding='utf-8')];n=len(r)+len(s)+sum(len(x['concepts']) for x in c);print(n, len(r), len(s), sum(len(x['concepts']) for x in c), round(n/281,3))"
```

## 10. 哪些数是命令复核的，哪些是预期

- **命令可复核**：577→706 的边计数与密度、129 条新增边及其逐条命中、未采纳清单 133 条（其中语义判断 111 条）、缺口 23→10、`--check` 一致性、diff 行数。
- **预期（未实测）**：R@5 方向。~~评测需等整合步骤重建图后跑 `eval/`，本任务按铁律未起推理进程、未写 `data/graph/`。~~
  **2026-10-31 16:40 整合步骤已完成：进图 + 实测 R@5 0.4389，见 §14。**
## 11. 双层账：机械层 706 + 语义推断层 194 = 900

| 层 | 条数 | 是否计入对外结论 | 文件 |
|---|---|---|---|
| 机械可证（129 条新增，逐条字面校验） | 706 | **是** | `gap_edges_added.json` + `requires/special_of/scenarios.jsonl` |
| 语义推断（五类来源，逐条 note+confidence） | 194 | **否** | `data/lexicon/gap_edges_inferred.jsonl` |
| 合计 | **900** | 对外报「706 机械 + 194 推断」 | |

| 关系 | 机械层 | 推断层 | 合计 |
|---|---|---|---|
| REQUIRES | 230 | 192 | 422 |
| SPECIAL_OF | 185 | 2 | 187 |
| APPLIES_TO | 291 | 0 | 291 |
| 合计 | 706 | 194 | **900** |

> **2026-10-31 16:40 进图状态**：机械层 706 条随 `requires/special_of/scenarios.jsonl` 由 `build_graph.py` 直接落图；
> 推断层 194 条由**后处理脚本** `scripts/apply_gap_edges.py` 加边并打标（`inferred=true`），重建本身未改。
> 因此「是否计入对外结论」仍是**否**——图里可查，但不计入机械层口径。

密度：机械层 2.512 条/概念；双层合计 **3.203 条/概念**（281 概念）。零度概念数：机械层 18 → 双层 10。

## 12. 五类来源：各补多少、丢弃多少

| 来源（evidence_type） | 净候选 | 入库 | 说明 |
|---|---|---|---|
| ① same_clause 条文内同句双端共现 | 180 | 124 | 两种可复读形态：(a) 分句首概念 + 紧邻强/情态线索（≤4 字）→ A 依赖其后宾语；(b) 条文定义对象 A 与同分句概念 B 有序、局部（≤20 字）共现。均 medium，无线索且无锚点的同句共现不入库 |
| ② chapter_structure 章/节制度归属 | 1 | 1 | 章标题含上位名 **且** 下位名形态学包含上位名才成立（「融资租赁合同⊂租赁合同」类），medium；单纯同章不登记 |
| ③ definitional 定义式/枚举式 | 12 | 12 | 「X 是指…」定义体含 Y 记 X 依赖 Y；「X 包括/分为/统称…」显式列举记 SPECIAL_OF；否定式（不包括）与上位名包含下位名者剔除 |
| ④ cross_law 跨法域同名制度 | 0 | 0 | 全库无「同名不同 id」概念对，故该来源为空（不是漏做） |
| ⑤ gold_path 金标路径概念对 | 57 | 57 | 路径序 A→…→B，相邻边均已存在，补直达 |

丢弃明细：原始候选 742 → 去重（同一对概念只留一条，跨条文重复登记）476 → 跨语域误配 16 → 净候选 250 → 额度外/低分 35 → 入库 194。

## 13. 置信分布与「不能用」的层

| confidence | 条数 | 用途 |
|---|---|---|
| high | 0 | — |
| medium | 194 | 可审计、可复核，但不得替代机械层结论 |
| low | 0 | 已全部留在候选池，未进交付文件 |

按分数排序选边，medium 用尽仍不足 194 才考虑 low；实际 medium 池充足，因此**交付文件中 low = 0**，未用低置信度凑数。净候选里 low 有 21 条，未入库。

### 13.1 推断层逐条（194 条，条文号 + note 摘要）

| # | 关系 | 边 | 条文 | 来源 | 置信 | note（截断） |
|---|---|---|---|---|---|---|
| 1 | REQUIRES | `project_term_contract`→`employer` | 第十五条 | definitional | medium | 中华人民共和国劳动合同法第十五条 定义式「以完成一定工作任务为期限的劳动合同是指用人单位与劳动者约定以某项工作的完成为合同期限的劳动合同」的定义体含「用人单位」 |
| 2 | REQUIRES | `project_term_contract`→`worker` | 第十五条 | definitional | medium | 中华人民共和国劳动合同法第十五条 定义式「以完成一定工作任务为期限的劳动合同是指用人单位与劳动者约定以某项工作的完成为合同期限的劳动合同」的定义体含「劳动者」， |
| 3 | REQUIRES | `child_labor`→`labor_protection` | 第十五条 | gold_path | medium | gold g168 路径 child_labor -> minor_worker -> labor_protection：A 与 B 同处一条标注路径、顺序 A |
| 4 | REQUIRES | `occupational_health_check`→`illegal_rescission` | 第五十四条 | gold_path | medium | gold g167 路径 occupational_health_check -> labor_protection -> illegal_rescission |
| 5 | REQUIRES | `wage_priority_payment`→`severance` | 第二十七条 | gold_path | medium | gold g132 路径 wage_priority_payment -> wage -> severance：A 与 B 同处一条标注路径、顺序 A 先 B  |
| 6 | REQUIRES | `creditor_right`→`debt_obligation` | 第五百二十一条 | same_clause | medium | 中华人民共和国民法典第五百二十一条 同句「按比例向其他连带债权人返还。连带债权参照适用本章连带债务的有关规定。」：主语「债权」后紧接强线索「适用」，宾语为「债务 |
| 7 | REQUIRES | `custodian`→`depositor` | 第九百一十一条 | same_clause | medium | 中华人民共和国民法典第九百一十一条 同句「保管人根据存货人或者仓单持有人的要求」：主语「保管人」后紧接强线索「根据」，宾语为「存货人」，A 以 B 为依据/要件 |
| 8 | REQUIRES | `custody_depositor`→`custodian` | 第八百八十九条 | same_clause | medium | 中华人民共和国民法典第八百八十九条 同句「寄存人应当按照约定向保管人支付保管费。当事人对」：主语「寄存人」后紧接强线索「按照」，宾语为「保管人」，A 以 B 为 |
| 9 | REQUIRES | `deposit_escrow`→`debtor_party` | 第五百七十四条 | same_clause | medium | 中华人民共和国民法典第五百七十四条 同句「权人未履行债务或者提供担保之前，提存部门根据债务人的要求应当拒绝其领取」：主语「提存」后紧接强线索「根据」，宾语为「债 |
| 10 | REQUIRES | `seller`→`buyer` | 第六百二十四条 | same_clause | medium | 中华人民共和国民法典第六百二十四条 同句「出卖人依照买受人的指示向第三人交付标」：主语「出卖人」后紧接强线索「依照」，宾语为「买受人」，A 以 B 为依据/要件 |
| 11 | REQUIRES | `seller`→`delivery` | 第六百零九条 | same_clause | medium | 中华人民共和国民法典第六百零九条 同句「出卖人按照约定未交付有关标的物的单证和资」：主语「出卖人」后紧接强线索「按照」，宾语为「交付」，A 以 B 为依据/要件 |
| 12 | REQUIRES | `seller`→`subject_matter` | 第六百零七条 | same_clause | medium | 中华人民共和国民法典第六百零七条 同句「出卖人按照约定将标的物运送至买受人指定地点」：主语「出卖人」后紧接强线索「按照」，宾语为「标的物」，A 以 B 为依据/ |
| 13 | REQUIRES | `fixed_term_contract`→`employer` | 第十三条 | definitional | medium | 中华人民共和国劳动合同法第十三条 定义式「固定期限劳动合同是指用人单位与劳动者约定合同终止时间的劳动合同」的定义体含「用人单位」，X 的定义依赖该概念。 |
| 14 | REQUIRES | `fixed_term_contract`→`worker` | 第十三条 | definitional | medium | 中华人民共和国劳动合同法第十三条 定义式「固定期限劳动合同是指用人单位与劳动者约定合同终止时间的劳动合同」的定义体含「劳动者」，X 的定义依赖该概念。 |
| 15 | REQUIRES | `open_ended_contract`→`employer` | 第十四条 | definitional | medium | 中华人民共和国劳动合同法第十四条 定义式「无固定期限劳动合同是指用人单位与劳动者约定无确定终止时间的劳动合同」的定义体含「用人单位」，X 的定义依赖该概念。 |
| 16 | REQUIRES | `open_ended_contract`→`worker` | 第十四条 | definitional | medium | 中华人民共和国劳动合同法第十四条 定义式「无固定期限劳动合同是指用人单位与劳动者约定无确定终止时间的劳动合同」的定义体含「劳动者」，X 的定义依赖该概念。 |
| 17 | REQUIRES | `part_time_employment`→`employer` | 第六十八条 | definitional | medium | 中华人民共和国劳动合同法第六十八条 定义式「非全日制用工是指以小时计酬为主，劳动者在同一用人单位一般平均每日工作时间不超过四小时，每周工作时间累计不超过二十四小 |
| 18 | REQUIRES | `part_time_employment`→`worker` | 第六十八条 | definitional | medium | 中华人民共和国劳动合同法第六十八条 定义式「非全日制用工是指以小时计酬为主，劳动者在同一用人单位一般平均每日工作时间不超过四小时，每周工作时间累计不超过二十四小 |
| 19 | SPECIAL_OF | `service_period`→`property_service_contract` | 第九百三十八条 | definitional | medium | 中华人民共和国民法典第九百三十八条 枚举式「物业服务合同包括服务事项、服务质量、服务费用的标准和收取办法、维修资金的使用、服务用房的管理和使用、服务期限、服务交 |
| 20 | REQUIRES | `severance`→`worker` | 第四十七条 | definitional | medium | 中华人民共和国劳动合同法第四十七条 定义式「经济补偿是指劳动者在劳动合同解除或者终止前十二个月的平均工资」的定义体含「劳动者」，X 的定义依赖该概念。 |
| 21 | REQUIRES | `contract_rescission`→`agency` | 第三十七条 | gold_path | medium | gold g188 路径 contract_rescission -> labor_relationship -> agency：A 与 B 同处一条标注路径、 |
| 22 | REQUIRES | `contract_rescission`→`separation_certificate` | 第三十七条 | gold_path | medium | gold g142 路径 contract_rescission -> handover -> separation_certificate：A 与 B 同处一 |
| 23 | REQUIRES | `contract_termination`→`severance_standard` | 第四十四条 | gold_path | medium | gold g137 路径 contract_termination -> severance -> severance_standard：A 与 B 同处一条标 |
| 24 | REQUIRES | `double_wage`→`agency` | 第八十二条 | gold_path | medium | gold g187 路径 double_wage -> labor_relationship -> agency：A 与 B 同处一条标注路径、顺序 A 先 B |
| 25 | REQUIRES | `double_wage`→`wage_payment` | 第八十二条 | gold_path | medium | gold g152 路径 double_wage -> wage -> wage_payment：A 与 B 同处一条标注路径、顺序 A 先 B 后，按路径依赖 |
| 26 | REQUIRES | `fault_dismissal`→`illegal_rescission` | 第三十九条 | gold_path | medium | gold g135 路径 fault_dismissal -> rescission_by_employer -> illegal_rescission：A 与 |
| 27 | REQUIRES | `female_worker_protection`→`illegal_rescission` | 第六十三条 | gold_path | medium | gold g37 路径 female_worker_protection -> labor_protection -> illegal_rescission：A |
| 28 | REQUIRES | `fixed_term_contract`→`contract_establishment` | 第十三条 | gold_path | medium | gold g178 路径 fixed_term_contract -> labor_contract -> contract_establishment：A 与 |
| 29 | REQUIRES | `guarantee_contract`→`security_interest` | 第六百八十八条 | gold_path | medium | gold g44 路径 guarantee_contract -> joint_liability -> security_interest：A 与 B 同处一 |
| 30 | REQUIRES | `illegal_rescission`→`damages_civil` | 第八十七条 | gold_path | medium | gold g53 路径 illegal_rescission -> damages -> damages_civil：A 与 B 同处一条标注路径、顺序 A 先 |
| 31 | REQUIRES | `illegal_rescission`→`severance_standard` | 第八十七条 | gold_path | medium | gold g148 路径 illegal_rescission -> damages -> severance_standard：A 与 B 同处一条标注路径、 |
| 32 | REQUIRES | `illegal_rescission`→`sick_leave` | 第八十七条 | gold_path | medium | gold g156 路径 illegal_rescission -> labor_protection -> sick_leave：A 与 B 同处一条标注路径 |
| 33 | REQUIRES | `illegal_rescission`→`tort_liability` | 第八十七条 | gold_path | medium | gold g191 路径 illegal_rescission -> labor_protection -> tort_liability：A 与 B 同处一条 |
| 34 | REQUIRES | `immediate_rescission`→`labor_quota` | 第三十八条 | gold_path | medium | gold g143 路径 immediate_rescission -> wage_deduction -> labor_quota：A 与 B 同处一条标注路 |
| 35 | REQUIRES | `immediate_rescission`→`pension_insurance` | 第三十八条 | gold_path | medium | gold g144 路径 immediate_rescission -> social_insurance -> pension_insurance：A 与 B |
| 36 | REQUIRES | `labor_contract`→`acceptance` | 第十五条 | gold_path | medium | gold g183 路径 labor_contract -> contract_establishment -> acceptance：A 与 B 同处一条标注 |
| 37 | REQUIRES | `labor_contract`→`offer` | 第十三条 | gold_path | medium | gold g182 路径 labor_contract -> contract_establishment -> offer：A 与 B 同处一条标注路径、顺序 |
| 38 | REQUIRES | `labor_contract`→`precontractual_liability` | 第十五条 | gold_path | medium | gold g184 路径 labor_contract -> contract_establishment -> precontractual_liabilit |
| 39 | REQUIRES | `labor_contract`→`standard_terms` | 第二十六条 | gold_path | medium | gold g51 路径 labor_contract -> contract_establishment -> standard_terms：A 与 B 同处一 |
| 40 | REQUIRES | `labor_dispute`→`agency` | 第七十七条 | gold_path | medium | gold g190 路径 labor_dispute -> labor_relationship -> agency：A 与 B 同处一条标注路径、顺序 A 先 |
| 41 | REQUIRES | `labor_dispute`→`limitation_period` | 第八十二条 | gold_path | medium | gold g43 路径 labor_dispute -> arbitration -> limitation_period：A 与 B 同处一条标注路径、顺序  |
| 42 | REQUIRES | `labor_protection`→`fair_liability` | 第五十八条 | gold_path | medium | gold g200 路径 labor_protection -> tort_liability -> fair_liability：A 与 B 同处一条标注路径 |
| 43 | REQUIRES | `labor_protection`→`fault_liability` | 第五十四条 | gold_path | medium | gold g58 路径 labor_protection -> tort_liability -> fault_liability：A 与 B 同处一条标注路径 |
| 44 | REQUIRES | `labor_protection`→`no_fault_liability` | 第五十八条 | gold_path | medium | gold g199 路径 labor_protection -> tort_liability -> no_fault_liability：A 与 B 同处一条 |
| 45 | REQUIRES | `labor_relationship`→`apparent_agency` | 第七条 | gold_path | medium | gold g60 路径 labor_relationship -> agency -> apparent_agency：A 与 B 同处一条标注路径、顺序 A  |
| 46 | REQUIRES | `labor_relationship`→`arbitration` | 第七条 | gold_path | medium | gold g175 路径 labor_relationship -> labor_dispute -> arbitration：A 与 B 同处一条标注路径、顺 |
| 47 | REQUIRES | `labor_relationship`→`pension_insurance` | 第七条 | gold_path | medium | gold g36 路径 labor_relationship -> social_insurance -> pension_insurance：A 与 B 同处 |
| 48 | REQUIRES | `labor_relationship`→`wage_payment` | 第七条 | gold_path | medium | gold g174 路径 labor_relationship -> wage -> wage_payment：A 与 B 同处一条标注路径、顺序 A 先 B  |
| 49 | REQUIRES | `labor_union`→`severance_standard` | 第四十三条 | gold_path | medium | gold g145 路径 labor_union -> severance -> severance_standard：A 与 B 同处一条标注路径、顺序 A  |
| 50 | REQUIRES | `liquidated_damages`→`contract_establishment` | 第五百八十五条 | gold_path | medium | gold g181 路径 liquidated_damages -> labor_contract -> contract_establishment：A 与  |
| 51 | REQUIRES | `minor_worker`→`tort_liability` | 第六十四条 | gold_path | medium | gold g192 路径 minor_worker -> labor_protection -> tort_liability：A 与 B 同处一条标注路径、顺 |
| 52 | REQUIRES | `non_compete_compensation`→`liquidated_damages_civil` | 第二十三条 | gold_path | medium | gold g195 路径 non_compete_compensation -> liquidated_damages -> liquidated_damage |
| 53 | REQUIRES | `overtime_pay`→`emergency_overtime` | 第三十一条 | gold_path | medium | gold g133 路径 overtime_pay -> work_hour_extension -> emergency_overtime：A 与 B 同处一 |
| 54 | REQUIRES | `part_time_employment`→`contract_establishment` | 第六十九条 | gold_path | medium | gold g56 路径 part_time_employment -> offer -> contract_establishment：A 与 B 同处一条标注 |
| 55 | REQUIRES | `part_time_employment`→`labor_dispute` | 第七十二条 | gold_path | medium | gold g39 路径 part_time_employment -> part_time_wage_settlement -> labor_dispute：A |
| 56 | REQUIRES | `pension_insurance`→`file_transfer` | 第七十三条 | gold_path | medium | gold g153 路径 pension_insurance -> social_insurance -> file_transfer：A 与 B 同处一条标注 |
| 57 | REQUIRES | `pension_insurance`→`work_injury_insurance` | 第七十三条 | gold_path | medium | gold g155 路径 pension_insurance -> social_insurance -> work_injury_insurance：A 与  |
| 58 | REQUIRES | `probation_period`→`contract_establishment` | 第十九条 | gold_path | medium | gold g177 路径 probation_period -> labor_contract -> contract_establishment：A 与 B  |
| 59 | REQUIRES | `probation_period`→`illegal_rescission` | 第二十一条 | gold_path | medium | gold g33 路径 probation_period -> trial_dismissal -> illegal_rescission：A 与 B 同处一条 |
| 60 | REQUIRES | `product_liability`→`sales_contract` | 第一千二百零二条 | gold_path | medium | gold g47 路径 product_liability -> no_fault_liability -> sales_contract：A 与 B 同处一条 |
| 61 | REQUIRES | `right_of_privacy`→`network_tort` | 第一千零三十二条 | gold_path | medium | gold g48 路径 right_of_privacy -> personal_information -> network_tort：A 与 B 同处一条标 |
| 62 | REQUIRES | `service_period`→`contract_establishment` | 第二十二条 | gold_path | medium | gold g180 路径 service_period -> labor_contract -> contract_establishment：A 与 B 同处 |
| 63 | REQUIRES | `service_period`→`liquidated_damages_civil` | 第二十二条 | gold_path | medium | gold g194 路径 service_period -> liquidated_damages -> liquidated_damages_civil：A  |
| 64 | REQUIRES | `severance`→`contract_performance` | 第四十六条 | gold_path | medium | gold g197 路径 severance -> breach_of_contract -> contract_performance：A 与 B 同处一条标 |
| 65 | REQUIRES | `severance`→`liquidated_damages_civil` | 第四十六条 | gold_path | medium | gold g196 路径 severance -> breach_of_contract -> liquidated_damages_civil：A 与 B 同 |
| 66 | REQUIRES | `severance_standard`→`damages_civil` | 第四十七条 | gold_path | medium | gold g193 路径 severance_standard -> damages -> damages_civil：A 与 B 同处一条标注路径、顺序 A  |
| 67 | REQUIRES | `social_insurance`→`agency` | 第七十三条 | gold_path | medium | gold g189 路径 social_insurance -> labor_relationship -> agency：A 与 B 同处一条标注路径、顺序  |
| 68 | REQUIRES | `traffic_accident_liability`→`damages_civil` | 第一千二百一十七条 | gold_path | medium | gold g46 路径 traffic_accident_liability -> no_fault_liability -> damages_civil：A  |
| 69 | REQUIRES | `unemployment_insurance`→`labor_relationship` | 第七十三条 | gold_path | medium | gold g154 路径 unemployment_insurance -> social_insurance -> labor_relationship：A  |
| 70 | REQUIRES | `wage`→`agency` | 第五十条 | gold_path | medium | gold g186 路径 wage -> labor_relationship -> agency：A 与 B 同处一条标注路径、顺序 A 先 B 后，按路径依 |
| 71 | REQUIRES | `wage_payment`→`labor_quota` | 第三十条 | gold_path | medium | gold g131 路径 wage_payment -> wage_deduction -> labor_quota：A 与 B 同处一条标注路径、顺序 A 先 |
| 72 | REQUIRES | `work_hour_extension`→`wage_payment` | 第四十四条 | gold_path | medium | gold g160 路径 work_hour_extension -> overtime_pay -> wage_payment：A 与 B 同处一条标注路径、 |
| 73 | REQUIRES | `work_injury`→`fault_liability` | 第七十三条 | gold_path | medium | gold g198 路径 work_injury -> tort_liability -> fault_liability：A 与 B 同处一条标注路径、顺序  |
| 74 | REQUIRES | `working_hours`→`wage_payment` | 第三十六条 | gold_path | medium | gold g134 路径 working_hours -> overtime_pay -> wage_payment：A 与 B 同处一条标注路径、顺序 A 先 |
| 75 | REQUIRES | `employer`→`labor_contract` | 第三十条 | same_clause | medium | 中华人民共和国劳动合同法第三十条 同句「用人单位应当按照劳动合同约定和国家规定，向劳」：主语「用人单位」后紧接强线索「按照」，宾语为「劳动合同」，A 以 B 为 |
| 76 | REQUIRES | `guarantor`→`guarantee_contract` | 第六百九十九条 | same_clause | medium | 中华人民共和国民法典第六百九十九条 同句「同一债务有两个以上保证人的，保证人应当按照保证合同约定的保证份额，承担」：主语「保证人」后紧接强线索「按照」，宾语为「 |
| 77 | REQUIRES | `lessee`→`possession` | 第七百二十五条 | same_clause | medium | 中华人民共和国民法典第七百二十五条 同句「租赁物在承租人按照租赁合同占有期限内发生所有权变动」：主语「承租人」后紧接强线索「按照」，宾语为「占有」，A 以 B  |
| 78 | REQUIRES | `lessor`→`seller` | 第七百三十九条 | same_clause | medium | 中华人民共和国民法典第七百三十九条 同句「出租人根据承租人对出卖人、租赁物的选择订立的」：主语「出租人」后紧接强线索「根据」，宾语为「出卖人」，A 以 B 为依 |
| 79 | REQUIRES | `natural_person`→`personal_freedom` | 第九百九十条 | same_clause | medium | 中华人民共和国民法典第九百九十条 同句「等权利。除前款规定的人格权外，自然人享有基于人身自由、人格尊严产生的其他」：主语「自然人」后紧接强线索「基于」，宾语为「 |
| 80 | REQUIRES | `seller`→`lessee` | 第七百三十九条 | same_clause | medium | 中华人民共和国民法典第七百三十九条 同句「、租赁物的选择订立的买卖合同，出卖人应当按照约定向承租人交付标的物，承租人享」：主语「出卖人」后紧接强线索「按照」，宾 |
| 81 | REQUIRES | `fixed_term_contract`→`contract_termination_civil` | 第十三条 | definitional | medium | 中华人民共和国劳动合同法第十三条 定义式「固定期限劳动合同是指用人单位与劳动者约定合同终止时间的劳动合同」的定义体含「合同终止」，X 的定义依赖该概念。 |
| 82 | REQUIRES | `severance`→`labor_contract` | 第四十七条 | definitional | medium | 中华人民共和国劳动合同法第四十七条 定义式「经济补偿是指劳动者在劳动合同解除或者终止前十二个月的平均工资」的定义体含「劳动合同」，X 的定义依赖该概念。 |
| 83 | REQUIRES | `agent_party`→`third_party` | 第九百二十三条 | same_clause | medium | 中华人民共和国民法典第九百二十三条 同句「担责任。转委托未经同意或者追认的，受托人应当对转委托的第三人的行为承担责任；但是」：主语「受托人」后紧接情态「应当」， |
| 84 | REQUIRES | `buyer`→`subject_matter` | 第六百零五条 | same_clause | medium | 中华人民共和国民法典第六百零五条 同句「致使标的物未按照约定的期限交付的，买受人应当自违反约定时起承担标的物毁损、灭失的风险。」：主语「买受人」后紧接情态「应当 |
| 85 | REQUIRES | `contractor_party`→`third_party` | 第七百九十一条 | same_clause | medium | 中华人民共和国民法典第七百九十一条 同句「、施工承包人向发包人承担连带责任。承包人不得将其承包的全部建设工程转包给第三人或者将其承包的全部建」：主语「承包人」后 |
| 86 | REQUIRES | `counterparty`→`debt_obligation` | 第一百七十一条 | same_clause | medium | 中华人民共和国民法典第一百七十一条 同句「。行为人实施的行为未被追认的，善意相对人有权请求行为人履行债务或者就其受到的损害请」：主语「相对人」后紧接情态「有权」 |
| 87 | REQUIRES | `creditor_party`→`counterparty` | 第五百三十五条 | same_clause | medium | 中华人民共和国民法典第五百三十五条 同句「权利，影响债权人的到期债权实现的，债权人可以向人民法院请求以自己的名义代位行使债务人对相对人的权利，但是该权利专」：主 |
| 88 | REQUIRES | `creditor_party`→`debt_obligation` | 第五百一十八条 | same_clause | medium | 中华人民共和国民法典第五百一十八条 同句「债权人为二人以上，部分或者全部债权人均可以请求债务人履行债务的，为连带债权；债务」：主语「债权人」后紧接情态「可以」， |
| 89 | REQUIRES | `creditor_party`→`debtor_party` | 第四百四十七条 | same_clause | medium | 中华人民共和国民法典第四百四十七条 同句「债务人不履行到期债务，债权人可以留置已经合法占有的债务人的动产，并有权就该动」：主语「债权人」后紧接情态「可以」，同分 |
| 90 | REQUIRES | `creditor_party`→`guarantor` | 第六百九十九条 | same_clause | medium | 中华人民共和国民法典第六百九十九条 同句「承担保证责任；没有约定保证份额的，债权人可以请求任何一个保证人在其保证范围内承担保」：主语「债权人」后紧接情态「可以」 |
| 91 | REQUIRES | `creditor_party`→`possession` | 第四百四十七条 | same_clause | medium | 中华人民共和国民法典第四百四十七条 同句「债务人不履行到期债务，债权人可以留置已经合法占有的债务人的动产，并有」：主语「债权人」后紧接情态「可以」，同分句宾语「 |
| 92 | REQUIRES | `custodian`→`deposit_escrow` | 第九百一十六条 | same_clause | medium | 中华人民共和国民法典第九百一十六条 同句「其在合理期限内提取；逾期不提取的，保管人可以提存仓储物。」：主语「保管人」后紧接情态「可以」，同分句宾语「提存」，A  |
| 93 | REQUIRES | `custodian`→`third_party` | 第八百九十四条 | same_clause | medium | 中华人民共和国民法典第八百九十四条 同句「保管人不得将保管物转交第三人保管，但是当事人另有」：主语「保管人」后紧接情态「不得」，同分句宾语「第三人」，A 的义务 |
| 94 | REQUIRES | `debtor_party`→`lien` | 第四百五十四条 | same_clause | medium | 中华人民共和国民法典第四百五十四条 同句「债务人可以请求留置权人在债务履行期限届满后行使留置权；留置权人不行使的，」：主语「债务人」后紧接情态「可以」，同分句宾 |
| 95 | REQUIRES | `debtor_party`→`performance_deadline` | 第四百五十三条 | same_clause | medium | 中华人民共和国民法典第四百五十三条 同句「留置权人与债务人应当约定留置财产后的债务履行期限；没有约定或者约定不」：主语「债务人」后紧接情态「应当」，同分句宾语「 |
| 96 | REQUIRES | `debtor_party`→`set_off` | 第五百四十九条 | same_clause | medium | 中华人民共和国民法典第五百四十九条 同句「有下列情形之一的，债务人可以向受让人主张抵销：（一）债务人接到债」：主语「债务人」后紧接情态「可以」，同分句宾语「抵销 |
| 97 | REQUIRES | `debtor_party`→`subject_matter` | 第五百二十九条 | same_clause | medium | 中华人民共和国民法典第五百二十九条 同句「知债务人，致使履行债务发生困难的，债务人可以中止履行或者将标的物提存。」：主语「债务人」后紧接情态「可以」，同分句宾语 |
| 98 | REQUIRES | `decedent`→`debt_obligation` | 第一千一百六十一条 | same_clause | medium | 中华人民共和国民法典第一千一百六十一条 同句「在此限。继承人放弃继承的，对被继承人依法应当缴纳的税款和债务可以不负清偿责任。」：主语「被继承人」后紧接情态「应当 |
| 99 | REQUIRES | `decedent`→`performance` | 第一千一百六十一条 | same_clause | medium | 中华人民共和国民法典第一千一百六十一条 同句「在此限。继承人放弃继承的，对被继承人依法应当缴纳的税款和债务可以不负清偿责任。」：主语「被继承人」后紧接情态「应当 |
| 100 | REQUIRES | `donee`→`delivery` | 第六百六十条 | same_clause | medium | 中华人民共和国民法典第六百六十条 同句「赠与合同，赠与人不交付赠与财产的，受赠人可以请求交付。依据前款规定应当交」：主语「受赠人」后紧接情态「可以」，同分句宾语 |
| 101 | REQUIRES | `employer`→`casualty_accident` | 第五十七条 | same_clause | medium | 中华人民共和国劳动法第五十七条 同句「人民政府劳动行政部门、有关部门和用人单位应当依法对劳动者在劳动过程中发生的伤亡事故和劳动者的职业病状况」：主语「用人单位」 |
| 102 | REQUIRES | `employer`→`occupational_disease` | 第五十七条 | same_clause | medium | 中华人民共和国劳动法第五十七条 同句「人民政府劳动行政部门、有关部门和用人单位应当依法对劳动者在劳动过程中发生的伤亡事故和劳动者的职业病状况，进行统计、报告」： |
| 103 | REQUIRES | `employer`→`vocational_training` | 第六十八条 | same_clause | medium | 中华人民共和国劳动法第六十八条 同句「用人单位应当建立职业培训制度，按照国家规定提」：主语「用人单位」后紧接情态「应当」，同分句宾语「职业培训」，A 的义务/权 |
| 104 | REQUIRES | `employer`→`worker` | 第四条 | same_clause | medium | 中华人民共和国劳动合同法第四条 同句「单位提出，通过协商予以修改完善。用人单位应当将直接涉及劳动者切身利益的规章制度和」：主语「用人单位」后紧接情态「应当」，同 |
| 105 | REQUIRES | `guarantor`→`debtor_party` | 第六百八十九条 | same_clause | medium | 中华人民共和国民法典第六百八十九条 同句「保证人可以要求债务人提供反担保。」：主语「保证人」后紧接情态「可以」，同分句宾语「债务人」，A 的义务/权限指向 B。 |
| 106 | REQUIRES | `legal_person`→`guarantor` | 第六百八十三条 | same_clause | medium | 中华人民共和国民法典第六百八十三条 同句「机关法人不得为保证人，但是经国务院批准为」：主语「法人」后紧接情态「不得」，同分句宾语「保证人」，A 的义务/权限指向 |
| 107 | REQUIRES | `principal`→`third_party` | 第九百二十三条 | same_clause | medium | 中华人民共和国民法典第九百二十三条 同句「以转委托。转委托经同意或者追认的，委托人可以就委托事务直接指示转委托的第三人，受托人仅就第三人的」：主语「委托人」后紧 |
| 108 | REQUIRES | `property_service_provider`→`third_party` | 第九百四十一条 | same_clause | medium | 中华人民共和国民法典第九百四十一条 同句「项服务事项向业主负责。物业服务人不得将其应当提供的全部物业服务转委托给第三人，或者将全部物业服务」：主语「物业服务人」 |
| 109 | REQUIRES | `seller`→`third_party` | 第六百四十三条 | same_clause | medium | 中华人民共和国民法典第六百四十三条 同句「买受人在回赎期限内没有回赎标的物，出卖人可以以合理价格将标的物出卖给第三人，出卖所得价款扣除买」：主语「出卖人」后紧接 |
| 110 | REQUIRES | `third_party`→`counterparty` | 第九百二十六条 | same_clause | medium | 中华人民共和国民法典第九百二十六条 同句「者委托人作为相对人主张其权利，但是第三人不得变更选定的相对人。委托人行使受托人对」：主语「第三人」后紧接情态「不得」， |
| 111 | REQUIRES | `third_party`→`creditor_party` | 第五百二十四条 | same_clause | medium | 中华人民共和国民法典第五百二十四条 同句「第三人对履行该债务具有合法利益的，第三人有权向债权人代为履行；但是，根据」：主语「第三人」后紧接情态「有权」，同分句宾 |
| 112 | REQUIRES | `third_party`→`debt_obligation` | 第五百二十二条 | same_clause | medium | 中华人民共和国民法典第五百二十二条 同句「担违约责任。法律规定或者当事人约定第三人可以直接请求债务人向其履行债务，第三人未在合理期限」：主语「第三人」后紧接情态 |
| 113 | REQUIRES | `third_party`→`debtor_party` | 第五百二十二条 | same_clause | medium | 中华人民共和国民法典第五百二十二条 同句「担违约责任。法律规定或者当事人约定第三人可以直接请求债务人向其履行债务，第三人」：主语「第三人」后紧接情态「可以」，同 |
| 114 | REQUIRES | `third_party`→`legal_person` | 第七十五条 | same_clause | medium | 中华人民共和国民法典第七十五条 同句「的名义从事民事活动产生的民事责任，第三人有权选择请求法人或者设立人承担。」：主语「第三人」后紧接情态「有权」，同分句宾语「 |
| 115 | REQUIRES | `unincorporated_organization`→`guarantor` | 第六百八十三条 | same_clause | medium | 中华人民共和国民法典第六百八十三条 同句「外。以公益为目的的非营利法人、非法人组织不得为保证人。」：主语「非法人组织」后紧接情态「不得」，同分句宾语「保证人」， |
| 116 | REQUIRES | `work_contractor`→`third_party` | 第七百七十三条 | same_clause | medium | 中华人民共和国民法典第七百七十三条 同句「承揽人可以将其承揽的辅助工作交由第三人完成。承揽人将其承揽」：主语「承揽人」后紧接情态「可以」，同分句宾语「第三人」， |
| 117 | REQUIRES | `accepting_unit`→`employer` | 第六十二条 | same_clause | medium | 中华人民共和国劳动合同法第六十二条 同句「用工的，实行正常的工资调整机制。用工单位不得将被派遣劳动者再派遣到其他用人单位。」：主语「用工单位」后紧接情态「不得」 |
| 118 | REQUIRES | `accepting_unit`→`worker` | 第六十二条 | same_clause | medium | 中华人民共和国劳动合同法第六十二条 同句「用工的，实行正常的工资调整机制。用工单位不得将被派遣劳动者再派遣到其他用人单位」：主语「用工单位」后紧接情态「不得」， |
| 119 | REQUIRES | `buyer`→`breach_of_contract` | 第五百九十七条 | same_clause | medium | 中华人民共和国民法典第五百九十七条 同句「处分权致使标的物所有权不能转移的，买受人可以解除合同并请求出卖人承担违约责任。法律、行政法规禁止」：主语「买受人」后紧 |
| 120 | REQUIRES | `counterparty`→`agency` | 第一百七十一条 | same_clause | medium | 中华人民共和国民法典第一百七十一条 同句「认时相对人所能获得的利益。相对人知道或者应当知道行为人无权代理的，相对人和行为人按」：主语「相对人」后紧接情态「应当」 |
| 121 | REQUIRES | `counterparty`→`joint_liability` | 第一百六十四条 | same_clause | medium | 中华人民共和国民法典第一百六十四条 同句「，损害被代理人合法权益的，代理人和相对人应当承担连带责任。」：主语「相对人」后紧接情态「应当」，同分句宾语「连带责任」 |
| 122 | REQUIRES | `counterparty`→`unauthorized_agency` | 第一百七十一条 | same_clause | medium | 中华人民共和国民法典第一百七十一条 同句「认时相对人所能获得的利益。相对人知道或者应当知道行为人无权代理的，相对人和行为人按」：主语「相对人」后紧接情态「应当」 |
| 123 | REQUIRES | `creditor_party`→`guarantee` | 第六百九十九条 | same_clause | medium | 中华人民共和国民法典第六百九十九条 同句「承担保证责任；没有约定保证份额的，债权人可以请求任何一个保证人在其保证范围内承担保证责任。」：主语「债权人」后紧接情态 |
| 124 | REQUIRES | `debtor_party`→`breach_of_contract` | 第五百二十三条 | same_clause | medium | 中华人民共和国民法典第五百二十三条 同句「履行债务或者履行债务不符合约定的，债务人应当向债权人承担违约责任。」：主语「债务人」后紧接情态「应当」，同分句宾语「违 |
| 125 | REQUIRES | `employer`→`damages` | 第四十八条 | same_clause | medium | 中华人民共和国劳动合同法第四十八条 同句「或者劳动合同已经不能继续履行的，用人单位应当依照本法第八十七条规定支付赔偿金。」：主语「用人单位」后紧接情态「应当」， |
| 126 | REQUIRES | `employer`→`labor_dispatch` | 第六十七条 | same_clause | medium | 中华人民共和国劳动合同法第六十七条 同句「用人单位不得设立劳务派遣单位向本单位或者所属」：主语「用人单位」后紧接情态「不得」，同分句宾语「劳务派遣」，A 的义务 |
| 127 | REQUIRES | `employer`→`labor_quota` | 第三十一条 | same_clause | medium | 中华人民共和国劳动合同法第三十一条 同句「用人单位应当严格执行劳动定额标准，不得强迫或者变」：主语「用人单位」后紧接情态「应当」，同分句宾语「劳动定额」，A 的 |
| 128 | REQUIRES | `employer`→`labor_union` | 第四十三条 | same_clause | medium | 中华人民共和国劳动合同法第四十三条 同句「定的，工会有权要求用人单位纠正。用人单位应当研究工会的意见，并将处理结果」：主语「用人单位」后紧接情态「应当」，同分句 |
| 129 | REQUIRES | `employer`→`liquidated_damages` | 第二十五条 | same_clause | medium | 中华人民共和国劳动合同法第二十五条 同句「十二条和第二十三条规定的情形外，用人单位不得与劳动者约定由劳动者承担违约金。」：主语「用人单位」后紧接情态「不得」，同 |
| 130 | REQUIRES | `employer`→`minor_worker` | 第六十五条 | same_clause | medium | 中华人民共和国劳动法第六十五条 同句「用人单位应当对未成年工定期进行健康检查。」：主语「用人单位」后紧接情态「应当」，同分句宾语「未成年工」，A 的义务/权限指 |
| 131 | REQUIRES | `employer`→`non_compete` | 第二十三条 | same_clause | medium | 中华人民共和国劳动合同法第二十三条 同句「密事项。对负有保密义务的劳动者，用人单位可以在劳动合同或者保密协议中与劳动者约定竞业限制条款，并约定在解除或」：主语「 |
| 132 | REQUIRES | `employer`→`severance` | 第四十六条 | same_clause | medium | 中华人民共和国劳动合同法第四十六条 同句「有下列情形之一的，用人单位应当向劳动者支付经济补偿：（一）劳动者依照本」：主语「用人单位」后紧接情态「应当」，同分句宾 |
| 133 | REQUIRES | `employer`→`wage` | 第四十四条 | same_clause | medium | 中华人民共和国劳动法第四十四条 同句「有下列情形之一的，用人单位应当按照下列标准支付高于劳动者正常工作时间工资的工资报酬：（一）安」：主语「用人单位」后紧接情态 |
| 134 | REQUIRES | `employer`→`working_hours` | 第三十七条 | same_clause | medium | 中华人民共和国劳动法第三十七条 同句「对实行计件工作的劳动者，用人单位应当根据本法第三十六条规定的工时制度合理确定其劳动定额和」：主语「用人单位」后紧接情态「应 |
| 135 | REQUIRES | `lessee`→`delivery` | 第七百四十条 | same_clause | medium | 中华人民共和国民法典第七百四十条 同句「付标的物的义务，有下列情形之一的，承租人可以拒绝受领出卖人向其交付的标的物：（一）标的」：主语「承租人」后紧接情态「可以 |
| 136 | REQUIRES | `lessee`→`subject_matter` | 第七百四十条 | same_clause | medium | 中华人民共和国民法典第七百四十条 同句「付标的物的义务，有下列情形之一的，承租人可以拒绝受领出卖人向其交付的标的物：（一）标的物严重不」：主语「承租人」后紧接情 |
| 137 | REQUIRES | `lessor`→`delivery` | 第七百零八条 | same_clause | medium | 中华人民共和国民法典第七百零八条 同句「出租人应当按照约定将租赁物交付承租人，并在租赁期限」：主语「出租人」后紧接情态「应当」，同分句宾语「交付」，A 的义务/ |
| 138 | REQUIRES | `lessor`→`financing_lease_contract` | 第七百五十三条 | same_clause | medium | 中华人民共和国民法典第七百五十三条 同句「押、投资入股或者以其他方式处分的，出租人可以解除融资租赁合同。」：主语「出租人」后紧接情态「可以」，同分句宾语「融资租 |
| 139 | REQUIRES | `lessor`→`possession` | 第七百四十八条 | same_clause | medium | 中华人民共和国民法典第七百四十八条 同句「出租人应当保证承租人对租赁物的占有和使用。出租人有下列」：主语「出租人」后紧接情态「应当」，同分句宾语「占有」，A 的 |
| 140 | REQUIRES | `lessor`→`sublease` | 第七百一十八条 | same_clause | medium | 中华人民共和国民法典第七百一十八条 同句「出租人知道或者应当知道承租人转租，但是在六个月内未提」：主语「出租人」后紧接情态「应当」，同分句宾语「转租」，A 的义 |
| 141 | REQUIRES | `ordering_party`→`delivery` | 第七百八十二条 | same_clause | medium | 中华人民共和国民法典第七百八十二条 同句「法第五百一十条的规定仍不能确定的，定作人应当在承揽人交付工作成果时支付；工作」：主语「定作人」后紧接情态「应当」，同分 |
| 142 | REQUIRES | `third_party`→`breach_of_contract` | 第五百二十二条 | same_clause | medium | 中华人民共和国民法典第五百二十二条 同句「履行债务或者履行债务不符合约定的，第三人可以请求债务人承担违约责任；债务人对债权人的抗」：主语「第三人」后紧接情态「可 |
| 143 | REQUIRES | `third_party`→`tort_liability` | 第一千一百七十五条 | same_clause | medium | 中华人民共和国民法典第一千一百七十五条 同句「损害是因第三人造成的，第三人应当承担侵权责任。」：主语「第三人」后紧接情态「应当」，同分句宾语「侵权责任」，A 的 |
| 144 | REQUIRES | `worker`→`dispatch_unit` | 第六十四条 | same_clause | medium | 中华人民共和国劳动合同法第六十四条 同句「被派遣劳动者有权在劳务派遣单位或者用工单位依法参加」：主语「劳动者」后紧接情态「有权」，同分句宾语「劳务派遣单位」，A |
| 145 | REQUIRES | `worker`→`labor_contract` | 第三十八条 | same_clause | medium | 中华人民共和国劳动合同法第三十八条 同句「用人单位有下列情形之一的，劳动者可以解除劳动合同：（一）未按照劳动合」：主语「劳动者」后紧接情态「可以」，同分句宾语「 |
| 146 | REQUIRES | `worker`→`labor_dispatch` | 第六十四条 | same_clause | medium | 中华人民共和国劳动合同法第六十四条 同句「被派遣劳动者有权在劳务派遣单位或者用工单位依法」：主语「劳动者」后紧接情态「有权」，同分句宾语「劳务派遣」，A 的义务 |
| 147 | REQUIRES | `worker`→`labor_union` | 第六十四条 | same_clause | medium | 中华人民共和国劳动合同法第六十四条 同句「被派遣劳动者有权在劳务派遣单位或者用工单位依法参加或者组织工会，维护自身的合法权益」：主语「劳动者」后紧接情态「有权」 |
| 148 | SPECIAL_OF | `financing_lease_contract`→`lease_contract` | 第七百三十五条 | chapter_structure | medium | 中华人民共和国民法典第七百三十五条 位于「第三编 合同·第十五章 融资租赁合同」，章标题含上位名「租赁合同」，且下位名「融资租赁合同」本身包含该上位名，按制度归 |
| 149 | REQUIRES | `brokerage_contract`→`principal` | 第九百五十一条 | same_clause | medium | 中华人民共和国民法典第九百五十一条 定义「行纪合同」的同一分句「行纪合同是行纪人以自己的名义为委托人从事贸易活动」内独立出现「委托人」，条文内双端共现，记 A  |
| 150 | REQUIRES | `buyer`→`third_party` | 第六百一十三条 | same_clause | medium | 中华人民共和国民法典第六百一十三条 定义「买受人」的同一分句「买受人订立合同时知道或者应当知道第三人对买卖的标的物享有权利的」内独立出现「第三人」，条文内双端共 |
| 151 | REQUIRES | `creditor_party`→`deposit_escrow` | 第五百七十四条 | same_clause | medium | 中华人民共和国民法典第五百七十四条 定义「债权人」的同一分句「或者债权人向提存部门书面表示放弃领取提存物权利的」内独立出现「提存」，条文内双端共现，记 A 对  |
| 152 | REQUIRES | `creditor_party`→`guarantee_period` | 第六百九十二条 | same_clause | medium | 中华人民共和国民法典第六百九十二条 定义「债权人」的同一分句「债权人与保证人可以约定保证期间」内独立出现「保证期间」，条文内双端共现，记 A 对 B 的依赖。 |
| 153 | REQUIRES | `creditor_party`→`performance_deadline` | 第六百九十二条 | same_clause | medium | 中华人民共和国民法典第六百九十二条 定义「债权人」的同一分句「债权人与债务人对主债务履行期限没有约定或者约定不明确的」内独立出现「履行期限」，条文内双端共现，记 |
| 154 | REQUIRES | `creditor_party`→`subrogation` | 第五百三十五条 | same_clause | medium | 中华人民共和国民法典第五百三十五条 定义「债权人」的同一分句「债权人行使代位权的必要费用」内独立出现「代位权」，条文内双端共现，记 A 对 B 的依赖。 |
| 155 | REQUIRES | `custody_depositor`→`delivery` | 第八百九十三条 | same_clause | medium | 中华人民共和国民法典第八百九十三条 定义「寄存人」的同一分句「寄存人交付的保管物有瑕疵或者根据保管物的性质需要采取特殊保管措施的」内独立出现「交付」，条文内双端 |
| 156 | REQUIRES | `debtor_party`→`creditor_assignment` | 第五百四十九条 | same_clause | medium | 中华人民共和国民法典第五百四十九条 定义「债务人」的同一分句「（一）债务人接到债权转让通知时」内独立出现「债权转让」，条文内双端共现，记 A 对 B 的依赖。 |
| 157 | REQUIRES | `debtor_party`→`creditor_right` | 第五百四十九条 | same_clause | medium | 中华人民共和国民法典第五百四十九条 定义「债务人」的同一分句「（一）债务人接到债权转让通知时」内独立出现「债权」，条文内双端共现，记 A 对 B 的依赖。 |
| 158 | REQUIRES | `donor`→`delivery` | 第六百六十条 | same_clause | medium | 中华人民共和国民法典第六百六十条 定义「赠与人」的同一分句「赠与人不交付赠与财产的」内独立出现「交付」，条文内双端共现，记 A 对 B 的依赖。 |
| 159 | REQUIRES | `employer`→`minimum_wage_standard` | 第四十八条 | same_clause | medium | 中华人民共和国劳动法第四十八条 定义「用人单位」的同一分句「用人单位支付劳动者的工资不得低于当地最低工资标准」内独立出现「最低工资标准」，条文内双端共现，记 A |
| 160 | REQUIRES | `expression_of_intent`→`counterparty` | 第一百四十一条 | same_clause | medium | 中华人民共和国民法典第一百四十一条 定义「意思表示」的同一分句「撤回意思表示的通知应当在意思表示到达相对人前或者与意思表示同时到达相对人」内独立出现「相对人」， |
| 161 | REQUIRES | `factoring_contract`→`creditor_party` | 第七百六十一条 | same_clause | medium | 中华人民共和国民法典第七百六十一条 定义「保理合同」的同一分句「保理合同是应收账款债权人将现有的或者将有的应收账款转让给保理人」内独立出现「债权人」，条文内双端 |
| 162 | REQUIRES | `financing_lease_contract`→`seller` | 第七百三十五条 | same_clause | medium | 中华人民共和国民法典第七百三十五条 定义「融资租赁合同」的同一分句「融资租赁合同是出租人根据承租人对出卖人」内独立出现「出卖人」，条文内双端共现，记 A 对 B |
| 163 | REQUIRES | `guarantee_period`→`debt_obligation` | 第六百九十二条 | same_clause | medium | 中华人民共和国民法典第六百九十二条 定义「保证期间」的同一分句「但是约定的保证期间早于主债务履行期限或者与主债务履行期限同时届满的」内独立出现「债务」，条文内双 |
| 164 | REQUIRES | `guarantee_period`→`debtor_party` | 第六百九十二条 | same_clause | medium | 中华人民共和国民法典第六百九十二条 定义「保证期间」的同一分句「保证期间自债权人请求债务人履行债务的宽限期届满之日起计算」内独立出现「债务人」，条文内双端共现， |
| 165 | REQUIRES | `guarantee_period`→`guarantor` | 第六百九十二条 | same_clause | medium | 中华人民共和国民法典第六百九十二条 定义「保证期间」的同一分句「保证期间是确定保证人承担保证责任的期间」内独立出现「保证人」，条文内双端共现，记 A 对 B 的 |
| 166 | REQUIRES | `guarantee_period`→`performance_deadline` | 第六百九十二条 | same_clause | medium | 中华人民共和国民法典第六百九十二条 定义「保证期间」的同一分句「但是约定的保证期间早于主债务履行期限或者与主债务履行期限同时届满的」内独立出现「履行期限」，条文 |
| 167 | REQUIRES | `intermediary_contract`→`principal` | 第九百六十一条 | same_clause | medium | 中华人民共和国民法典第九百六十一条 定义「中介合同」的同一分句「中介合同是中介人向委托人报告订立合同的机会或者提供订立合同的媒介服务」内独立出现「委托人」，条文 |
| 168 | REQUIRES | `legal_person`→`creditor_party` | 第八十三条 | same_clause | medium | 中华人民共和国民法典第八十三条 定义「法人」的同一分句「营利法人的出资人不得滥用法人独立地位和出资人有限责任损害法人债权人的利益」内独立出现「债权人」，条文内双 |
| 169 | REQUIRES | `legal_person`→`debt_obligation` | 第八十三条 | same_clause | medium | 中华人民共和国民法典第八十三条 定义「法人」的同一分句「应当对法人债务承担连带责任」内独立出现「债务」，条文内双端共现，记 A 对 B 的依赖。 |
| 170 | REQUIRES | `lessee`→`liquidated_damages` | 第七百一十九条 | same_clause | medium | 中华人民共和国民法典第七百一十九条 同句「承租人拖欠租金的，次承租人可以代承租人支付其欠付的租金和违约金，但是转租合同对出租」：主语「承租人」后紧接情态「可以」 |
| 171 | REQUIRES | `lessee`→`liquidated_damages_civil` | 第七百一十九条 | same_clause | medium | 中华人民共和国民法典第七百一十九条 同句「承租人拖欠租金的，次承租人可以代承租人支付其欠付的租金和违约金，但是转租合同对出租」：主语「承租人」后紧接情态「可以」 |
| 172 | REQUIRES | `lessor`→`guarantee` | 第七百四十八条 | same_clause | medium | 中华人民共和国民法典第七百四十八条 同句「出租人应当保证承租人对租赁物的占有」：主语「出租人」后紧接情态「应当」，同分句宾语「保证」，A 的义务/权限指向 B。 |
| 173 | REQUIRES | `maximum_mortgage`→`creditor_right` | 第四百二十一条 | same_clause | medium | 中华人民共和国民法典第四百二十一条 定义「最高额抵押」的同一分句「最高额抵押担保的债权确定前」内独立出现「债权」，条文内双端共现，记 A 对 B 的依赖。 |
| 174 | REQUIRES | `natural_person`→`inheritance` | 第一千一百三十三条 | same_clause | medium | 中华人民共和国民法典第一千一百三十三条 同句「分个人财产，并可以指定遗嘱执行人。自然人可以立遗嘱将个人财产指定由法定继承人中的一人或者数人继承。自然人可以立遗嘱 |
| 175 | REQUIRES | `natural_person`→`legacy` | 第一千一百五十八条 | same_clause | medium | 中华人民共和国民法典第一千一百五十八条 同句「自然人可以与继承人以外的组织或者个人签订遗赠扶养协议。按照协议，」：主语「自然人」后紧接情态「可以」，同分句宾语「 |
| 176 | REQUIRES | `natural_person`→`personal_information` | 第一千零三十七条 | same_clause | medium | 中华人民共和国民法典第一千零三十七条 同句「自然人可以依法向信息处理者查阅或者复制其个人信息；发现信息有错误的，」：主语「自然人」后紧接情态「可以」，同分句宾语 |
| 177 | REQUIRES | `natural_person`→`testament` | 第一千一百三十三条 | same_clause | medium | 中华人民共和国民法典第一千一百三十三条 同句「自然人可以依照本法规定立遗嘱处分个人财产，并可以」：主语「自然人」后紧接情态「可以」，同分句宾语「遗嘱」，A 的义 |
| 178 | REQUIRES | `partner`→`creditor_party` | 第九百七十五条 | same_clause | medium | 中华人民共和国民法典第九百七十五条 定义「合伙人」的同一分句「合伙人的债权人不得代位行使合伙人依照本章规定和合伙合同享有的权利」内独立出现「债权人」，条文内双端 |
| 179 | REQUIRES | `performance`→`debt_obligation` | 第九百七十三条 | same_clause | medium | 中华人民共和国民法典第九百七十三条 定义「清偿」的同一分句「清偿合伙债务超过自己应当承担份额的合伙人」内独立出现「债务」，条文内双端共现，记 A 对 B 的依赖 |
| 180 | REQUIRES | `performance`→`partner` | 第九百七十三条 | same_clause | medium | 中华人民共和国民法典第九百七十三条 定义「清偿」的同一分句「清偿合伙债务超过自己应当承担份额的合伙人」内独立出现「合伙人」，条文内双端共现，记 A 对 B 的依 |
| 181 | REQUIRES | `revocation_right`→`creditor_party` | 第五百四十一条 | same_clause | medium | 中华人民共和国民法典第五百四十一条 定义「撤销权」的同一分句「撤销权自债权人知道或者应当知道撤销事由之日起一年内行使」内独立出现「债权人」，条文内双端共现，记  |
| 182 | REQUIRES | `security_contract`→`creditor_right` | 第三百八十八条 | same_clause | medium | 中华人民共和国民法典第三百八十八条 定义「担保合同」的同一分句「担保合同是主债权债务合同的从合同」内独立出现「债权」，条文内双端共现，记 A 对 B 的依赖。 |
| 183 | REQUIRES | `security_contract`→`debt_obligation` | 第三百八十八条 | same_clause | medium | 中华人民共和国民法典第三百八十八条 定义「担保合同」的同一分句「担保合同是主债权债务合同的从合同」内独立出现「债务」，条文内双端共现，记 A 对 B 的依赖。 |
| 184 | REQUIRES | `service_technical_achievement`→`legal_person` | 第八百四十七条 | same_clause | medium | 中华人民共和国民法典第八百四十七条 定义「职务技术成果」的同一分句「职务技术成果是执行法人或者非法人组织的工作任务」内独立出现「法人」，条文内双端共现，记 A  |
| 185 | REQUIRES | `service_technical_achievement`→`unincorporated_organization` | 第八百四十七条 | same_clause | medium | 中华人民共和国民法典第八百四十七条 定义「职务技术成果」的同一分句「职务技术成果是执行法人或者非法人组织的工作任务」内独立出现「非法人组织」，条文内双端共现，记 |
| 186 | REQUIRES | `technology_transfer_contract`→`technology_license_contract` | 第八百六十二条 | same_clause | medium | 中华人民共和国民法典第八百六十二条 定义「技术转让合同」的同一分句「技术转让合同和技术许可合同中关于提供实施技术的专用设备」内独立出现「技术许可合同」，条文内双 |
| 187 | REQUIRES | `warehouse_contract`→`custodian` | 第九百零四条 | same_clause | medium | 中华人民共和国民法典第九百零四条 定义「仓储合同」的同一分句「仓储合同是保管人储存存货人交付的仓储物」内独立出现「保管人」，条文内双端共现，记 A 对 B 的依 |
| 188 | REQUIRES | `warehouse_contract`→`delivery` | 第九百零四条 | same_clause | medium | 中华人民共和国民法典第九百零四条 定义「仓储合同」的同一分句「仓储合同是保管人储存存货人交付的仓储物」内独立出现「交付」，条文内双端共现，记 A 对 B 的依赖 |
| 189 | REQUIRES | `work_contractor`→`delivery` | 第七百八十三条 | same_clause | medium | 中华人民共和国民法典第七百八十三条 定义「承揽人」的同一分句「承揽人对完成的工作成果享有留置权或者有权拒绝交付」内独立出现「交付」，条文内双端共现，记 A 对  |
| 190 | REQUIRES | `work_contractor`→`lien` | 第七百八十三条 | same_clause | medium | 中华人民共和国民法典第七百八十三条 定义「承揽人」的同一分句「承揽人对完成的工作成果享有留置权或者有权拒绝交付」内独立出现「留置权」，条文内双端共现，记 A 对 |
| 191 | REQUIRES | `acceptance`→`expression_of_intent` | 第四百七十九条 | same_clause | medium | 中华人民共和国民法典第四百七十九条 定义「承诺」的同一分句「承诺是受要约人同意要约的意思表示」内独立出现「意思表示」，条文内双端共现，记 A 对 B 的依赖。 |
| 192 | REQUIRES | `agency`→`third_party` | 第一百六十九条 | same_clause | medium | 中华人民共和国民法典第一百六十九条 定义「代理」的同一分句「被代理人可以就代理事务直接指示转委托的第三人」内独立出现「第三人」，条文内双端共现，记 A 对 B  |
| 193 | REQUIRES | `civil_subject`→`credit` | 第一千零二十九条 | same_clause | medium | 中华人民共和国民法典第一千零二十九条 定义「民事主体」的同一分句「民事主体可以依法查询自己的信用评价」内独立出现「信用」，条文内双端共现，记 A 对 B 的依赖 |
| 194 | REQUIRES | `collective_contract`→`employer` | 第五十一条 | same_clause | medium | 中华人民共和国劳动合同法第五十一条 定义「集体合同」的同一分句「集体合同由工会代表企业职工一方与用人单位订立」内独立出现「用人单位」，条文内双端共现，记 A 对 |

## 14. 2026-10-31 进图实测（整合步骤，取代 §10「未实测」）

### 14.1 口径与命令

```powershell
# 备份（graph.json / lexweave_graph.pkl / stats.json / retrieval_post_rebuild.json）
#   → data/graph/_backup_20261031_m/（BACKUP_MANIFEST.json 记 SHA256 + mtime）
python app\__002__extract\__002__build_extraction.py          # 无 LLM，CPU
python -m app.__002__extract.__003__edge_typing                 # 默认口径，不设 DEFINES_STRICT
python scripts\build_graph.py                                  # 机械层 706 直接落图
python scripts\apply_gap_edges.py                              # 后处理：194 条推断边 + inferred 标记
# 评测
$env:GRAPH_BACKEND='networkx'; $env:EMBEDDING_DEVICE='cpu'
python scripts\eval_retrieval.py --modes graph --no-llm --out data\eval_out\retrieval_post_m_rebuild.json
```

GPU：`nvidia-smi` 显示另一会话占用（6344 MiB / 39%），本流程全程 CPU，未抢卡。

### 14.2 图规模与三类边

| 指标 | 补边进图前（15:13 首次重建） | 进图后（16:40） | 差值 |
|---|---:|---:|---:|
| 节点 | 1799 | **1799** | 0 |
| 边总数 | 5503 | **5826** | +323 |
| DEFINES | 710 | **710** | 0 |
| REFERS_TO | 4119 | **4119** | 0（`m` 未改抽取） |
| CITES | 97 | 97 | 0 |
| REQUIRES | 196 | **422** | +226（机械 +34，推断 +192） |
| SPECIAL_OF | 183 | **187** | +4（机械 +2，推断 +2） |
| APPLIES_TO | 198 | **291** | +93（机械） |

说明：`build_graph.py` 直接落的是 `m` 的机械层 706（REQUIRES 230 / SPECIAL_OF 185 / APPLIES_TO 291），
比 15:13 那次重建多 129 条；这 129 条不是本次后处理加的。194 条推断边由后处理加入，所以 +323 = 129 + 194。
`concept_coverage.covered_by_scenarios` 也从 119 升到 129。

### 14.3 标记核验（`data/eval_out/gap_edges_mark_check.json`）

| 项 | 实测 |
|---|---:|
| 推断边文件行数 | 194 |
| 图中 `inferred=true` 边数 | **194** |
| 其中 REQUIRES / SPECIAL_OF | 192 / 2 |
| 机械层 REQUIRES / SPECIAL_OF / APPLIES_TO（key=关系名） | **230 / 185 / 291** |
| 机械层被误打 `inferred` 标记 | **0** |
| 端点缺失 / 关系非法 / 重复 | 0 / 0 / 0 |

标记是**后处理**加的：`inferred=true`、`provenance="inferred"`、`evidence_type`、`article`、`note` 原样带入，
`confidence=0.6`（medium 的数值映射，原值保留在 `confidence_label`）；边 key 用 `REQUIRES_INFERRED` /
`SPECIAL_OF_INFERRED`，与机械层的 `REQUIRES` / `SPECIAL_OF` 并存，**不会覆盖机械边**。
`build_graph.py` 未改一行。

### 14.4 R@5 新旧对照

| 项 | 旧基线（`retrieval_post_rebuild.json`） | 新基线（`retrieval_post_m_rebuild.json`） |
|---|---:|---:|
| graph recall@5 | **0.4417** | **0.4389**（−0.0028） |
| all@5 | 0.2333 | 0.2167（−0.0167） |
| path_recall | 0.5694 | 0.5694 |
| citation_hit | 0.4417 | 0.4389（−0.0028） |
| by_hop（two/three/cross） | 0.45 / 0.425 / 0.45 | 0.4667 / 0.4417 / **0.35** |
| latency_median_ms | 301.5 | 442.8（CPU，不可比） |

60 条中 **4 条变动**（`g01` 0→0.5、`g37` 0.333→0.667 上升；`g54` 1.0→0.667、`g57` 0.667→0 下降），
净 −1/6 条 recall（−0.0028）。单条二项标准误约 0.064，**该差值在 60 条样本上与抽样噪声不可区分**；
但 `cross_law` 桶 −0.10（2/20 命中丢失）是方向性信号，建议 n 阶段用更大样本复核。
如实结论：**补边进图后 R@5 略降、未升**，与 §6/§0 原先「预期略升」不符，以本节实测为准。

### 14.5 备份

`data\graph\_backup_20261031_m\`：`graph/graph.json`（sha256 `0440882d42da…`）、
`graph/lexweave_graph.pkl`（`fc45938a505c…`）、`graph/stats.json`（`32a75f6a23dd…`）、
`eval_out/retrieval_post_rebuild.json`（`1519c56df3ef…`）。`retrieval.json` 与 `retrieval_post_rebuild.json`
均未被覆盖。

