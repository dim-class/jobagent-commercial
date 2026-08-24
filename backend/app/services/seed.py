"""Demo data so the UI is useful before the user has pasted anything.

Every company below is fictional and every posting is explicitly labelled
``[演示数据]``. These are **not** real vacancies and must never be presented as
such - they exist to exercise the scoring, filtering and dashboard code paths.

The set deliberately covers the interesting cases:
  * 北京 Cloud Engineer            - the ideal match
  * 上海 DevOps Engineer           - strong match, different title family
  * 杭州 SRE                       - strong match, stretch on experience
  * 广州 Infrastructure Engineer   - middleware/AIX heavy, transferable
  * 深圳 Helpdesk                  - excluded role, should score low
  * 北京 Principal Cloud Architect - 8-10 years, a hard experience gap
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event
from app.models import ApplicationEvent, EventType, Job, JobStatus
from app.services.job_normalizer import normalize_job

logger = get_logger(__name__)

DEMO_MARKER = "[演示数据]"


@dataclass(slots=True)
class DemoJob:
    title: str
    company: str
    city: str
    salary_text: str
    experience_text: str
    education_text: str
    description: str


DEMO_JOBS: list[DemoJob] = [
    DemoJob(
        title="云计算工程师 / Cloud Engineer",
        company="示例科技（演示公司 A）",
        city="北京",
        salary_text="20k-30k·13薪",
        experience_text="1-3年",
        education_text="本科",
        description=f"""{DEMO_MARKER} 本岗位为演示用途，非真实招聘信息。

岗位职责：
1. 负责公司 AWS 云平台的日常运维与优化，包括 EC2、VPC、S3、ALB、RDS 等核心服务；
2. 使用 Terraform / CloudFormation 编写和维护基础设施即代码（IaC）模板；
3. 参与容器化改造，维护 Docker 与 Kubernetes 集群；
4. 搭建与维护监控告警体系（Prometheus + Grafana），持续提升系统可用性；
5. 编写 Python / Shell 自动化脚本，减少重复性运维工作。

任职要求：
1. 本科及以上学历，计算机相关专业，1-3 年云计算或运维相关经验；
2. 熟悉 Linux 系统管理与常见网络协议（TCP/IP、DNS、负载均衡）；
3. 有 AWS 使用经验，持有 AWS 认证者优先；
4. 熟悉 Docker，了解 Kubernetes；
5. 具备 Python 或 Shell 脚本能力；
6. 有 CI/CD（Jenkins / GitLab CI）经验者优先。
""",
    ),
    DemoJob(
        title="DevOps Engineer（平台工程方向）",
        company="示例数据（演示公司 B）",
        city="上海",
        salary_text="25k-35k",
        experience_text="3-5年",
        education_text="本科",
        description=f"""{DEMO_MARKER} 本岗位为演示用途，非真实招聘信息。

我们正在组建平台工程团队，为业务研发提供自助式的交付平台。

你将负责：
- 建设与维护 CI/CD 流水线（GitLab CI / ArgoCD），支撑上百个服务的持续交付；
- 维护多集群 Kubernetes 环境，推动服务网格与灰度发布能力落地；
- 以 Terraform 管理 AWS 多账号基础设施，推进 IaC 覆盖率；
- 完善可观测性体系：Prometheus、Grafana、ELK；
- 与研发团队协作，沉淀平台化工具与最佳实践。

我们期待你：
- 3-5 年 DevOps / SRE / 平台工程相关经验（优秀的 2 年候选人也欢迎沟通）；
- 精通 Linux，熟悉容器与 Kubernetes 生态；
- 熟练使用 Python 或 Go 开发运维工具；
- 有 AWS 或其他公有云的生产环境经验；
- 具备良好的自动化意识与工程化思维。
""",
    ),
    DemoJob(
        title="SRE 站点可靠性工程师",
        company="示例出行（演示公司 C）",
        city="杭州",
        salary_text="22k-32k·14薪",
        experience_text="3-5年",
        education_text="本科",
        description=f"""{DEMO_MARKER} 本岗位为演示用途，非真实招聘信息。

岗位职责：
1. 负责核心交易链路的稳定性建设，制定并落地 SLO / SLI 指标体系；
2. 参与容量规划、故障演练与应急预案建设，主导重大故障复盘；
3. 建设自动化运维平台，降低人工介入频率；
4. 优化 Kubernetes 集群资源调度与成本；
5. 参与值班（on-call）轮转。

任职要求：
1. 3 年以上 SRE / 运维开发经验；
2. 深入理解 Linux 系统与网络原理，具备较强的故障排查能力；
3. 熟悉 Kubernetes、Docker，了解服务治理；
4. 具备 Python 开发能力，能独立完成运维工具开发；
5. 熟悉 Prometheus / Grafana 等监控体系；
6. 有大规模分布式系统运维经验者优先。
""",
    ),
    DemoJob(
        title="基础架构工程师 Infrastructure Engineer（中间件方向）",
        company="示例金融科技（演示公司 D）",
        city="广州",
        salary_text="18k-26k",
        experience_text="2-4年",
        education_text="本科",
        description=f"""{DEMO_MARKER} 本岗位为演示用途，非真实招聘信息。

岗位职责：
1. 负责银行核心系统中间件（WebSphere / WAS、IHS、MQ）的部署、调优与日常维护；
2. 负责 AIX 与 Linux 服务器的系统运维、补丁管理与性能优化；
3. 参与基础设施上云项目，配合完成应用向 AWS 的迁移与验证；
4. 编写 Shell / Python 脚本实现巡检与部署自动化；
5. 配合安全部门完成合规检查与整改。

任职要求：
1. 2 年以上中间件或系统运维经验；
2. 熟悉 WebSphere / IHS 配置与故障处理；
3. 熟悉 AIX 或 Linux 系统管理；
4. 了解容器技术与公有云者优先；
5. 有金融行业经验者优先。
""",
    ),
    DemoJob(
        title="IT 桌面运维工程师（Helpdesk）",
        company="示例商贸（演示公司 E）",
        city="深圳",
        salary_text="8k-11k",
        experience_text="1-3年",
        education_text="大专",
        description=f"""{DEMO_MARKER} 本岗位为演示用途，非真实招聘信息。

岗位职责：
1. 负责公司员工日常 IT 支持，处理电脑软硬件故障、装机与系统重装；
2. 负责办公网络、打印机、会议室设备的日常维护；
3. 负责办公终端资产盘点与账号开通；
4. 协助处理弱电与综合布线相关事宜；
5. 接听 Helpdesk 电话与工单，跟进闭环。

任职要求：
1. 大专及以上学历，1-3 年桌面运维 / Desktop Support 经验；
2. 熟悉 Windows 系统安装与常见故障排查；
3. 熟悉常用办公软件；
4. 有较强的服务意识与沟通能力；
5. 能接受偶尔加班与驻场支持。
""",
    ),
    DemoJob(
        title="资深云架构师 Principal Cloud Architect",
        company="示例云智（演示公司 F）",
        city="北京",
        salary_text="45k-65k·16薪",
        experience_text="8-10年",
        education_text="本科",
        description=f"""{DEMO_MARKER} 本岗位为演示用途，非真实招聘信息。

岗位职责：
1. 负责集团级多云架构的顶层设计，制定云上技术标准与治理规范；
2. 主导大型客户的云迁移方案设计与技术决策；
3. 带领 10 人以上的架构团队，负责技术选型与人才培养；
4. 与业务高层沟通，推动云原生转型战略落地。

任职要求：
1. 硬性要求：8 年以上云计算领域经验，其中 3 年以上架构设计经验，不满足勿投；
2. 具备大规模（万台以上）基础设施的架构与治理经验；
3. 精通 AWS / Azure / 阿里云中至少两种公有云；
4. 精通 Kubernetes、Terraform 与云原生技术栈；
5. 有团队管理经验，具备优秀的方案表达与客户沟通能力；
6. 持有 AWS Certified Solutions Architect - Professional 者优先。
""",
    ),
]


def seed_demo_jobs(db: Session, *, reset: bool = False) -> dict[str, int]:
    """Insert the demo jobs, skipping any whose content hash already exists."""
    if reset:
        removed = 0
        for job in db.scalars(select(Job).where(Job.source == "demo")):
            db.delete(job)
            removed += 1
        db.commit()
        log_event(logger, "seed.reset", removed=removed)

    created = skipped = 0
    for demo in DEMO_JOBS:
        normalized = normalize_job(
            company=demo.company,
            title=demo.title,
            raw_description=demo.description,
            city=demo.city,
            salary_text=demo.salary_text,
            experience_text=demo.experience_text,
            education_text=demo.education_text,
        )
        exists = db.scalar(select(Job).where(Job.content_hash == normalized.content_hash))
        if exists is not None:
            skipped += 1
            continue

        job = Job(
            source="demo",
            external_id=None,
            source_url=None,
            company=normalized.company,
            title=normalized.title,
            city=normalized.city,
            salary_text=normalized.salary_text,
            experience_text=normalized.experience_text,
            education_text=normalized.education_text,
            raw_description=normalized.raw_description,
            normalized_description=normalized.normalized_description,
            content_hash=normalized.content_hash,
            status=JobStatus.new,
        )
        db.add(job)
        db.flush()
        db.add(
            ApplicationEvent(
                job_id=job.id,
                event_type=EventType.note,
                notes=f"{DEMO_MARKER} 由 seed 命令创建，仅用于演示，非真实招聘信息",
            )
        )
        created += 1

    db.commit()
    log_event(logger, "seed.completed", created=created, skipped=skipped, total=len(DEMO_JOBS))
    return {"created": created, "skipped": skipped, "total": len(DEMO_JOBS)}
