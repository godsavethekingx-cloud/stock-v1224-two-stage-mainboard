#!/usr/bin/env bash
# =============================================================
# 主板两阶段选股系统 自动运行入口 run_auto.sh
# -------------------------------------------------------------
# 用法:
#   bash run_auto.sh stage1   # 阶段1 晚间全盘选TOP20
#   bash run_auto.sh stage2   # 阶段2 早盘竞价重排筛TOP5
#
# 流程:
#   1) 首次运行时自动安装依赖 (requests)
#   2) 自动创建运行期产物目录 (system/ edge/ reports/)
#   3) 从 GitHub 公开库 git 同步最新代码
#   4) 按目标阶段执行对应脚本, 非交易日自动跳过
#
# 可复现设计: 本脚本不依赖固定绝对路径, clone 到任意目录均可运行.
#
# 关键约束:
#   system/ edge/ reports/ 归属运行期产物, git 同步时【不得覆盖】它们;
#   同步只拉取代码改动, 不触碰本地产物.
# =============================================================
set -u

SYSTEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/godsavethekingx-cloud/stock-v1224-two-stage-mainboard.git"
RUN_DIR="${SYSTEM_DIR}/system"                 # 阶段1/阶段2 数据与核心库
EDGE_DIR="${SYSTEM_DIR}/edge"                  # 阶段2 输出
REPORT_DIR="${SYSTEM_DIR}/reports"             # 阶段1 日报
NIGHT_POOL="${RUN_DIR}/night20_latest.json"    # 阶段1 输出去产物(同步时保护)

STAGE="${1:-stage2}"
echo "[run_auto] 工作目录=${SYSTEM_DIR}  目标=${STAGE}"

# ---------- 0. 自动安装依赖 ----------
if ! python3 -c "import requests" >/dev/null 2>&1; then
    echo "[run_auto] 未检测到 requests, 尝试安装..."
    pip3 install -q requests || { echo "[run_auto] requests 安装失败"; exit 1; }
fi

# ---------- 0.5 自动创建产物目录 ----------
mkdir -p "${RUN_DIR}" "${EDGE_DIR}" "${REPORT_DIR}"

# ---------- 1. 首次部署: 若目录无 git 仓库则克隆 ----------
if [ ! -d "${SYSTEM_DIR}/.git" ]; then
    echo "[run_auto] 未发现 git 仓库, 从远端克隆..."
    git clone "${REPO_URL}" "${SYSTEM_DIR}" || { echo "[run_auto] 克隆失败"; exit 1; }
fi

# ---------- 2. git 同步最新代码(保护运行期产物) ----------
cd "${SYSTEM_DIR}" || exit 1

# 产物目录不受 git 管理(加入 .gitignore), 用 git stash/checkout 保护未跟踪产物
mkdir -p "${RUN_DIR}" "${EDGE_DIR}" "${REPORT_DIR}"
# 同步前备份阶段1输出去产物
BACKUP=""
if [ -f "${NIGHT_POOL}" ]; then
    BACKUP=$(mktemp)
    cp "${NIGHT_POOL}" "${BACKUP}"
    echo "[run_auto] 已备份现有 ${NIGHT_POOL} -> ${BACKUP}"
fi

git fetch origin >/dev/null 2>&1
git reset --hard origin/main >/dev/null 2>&1
git clean -fdq --exclude=system --exclude=edge --exclude=reports 2>/dev/null || true
echo "[run_auto] 已 git 同步最新代码 (commit: $(git rev-parse --short HEAD))"

# 恢复阶段1输出去产物 (同步不得覆盖)
if [ -n "$BACKUP" ] && [ -s "$BACKUP" ]; then
    mkdir -p "${RUN_DIR}"
    cp "$BACKUP" "${NIGHT_POOL}"
    echo "[run_auto] 已恢复阶段1输出 ${NIGHT_POOL}"
fi
[ -n "$BACKUP" ] && rm -f "$BACKUP"
unset BACKUP

# ---------- 3. 执行目标阶段 ----------
case "${STAGE}" in
    stage1)
        echo "[run_auto] 执行阶段1 晚间全盘选TOP20..."
        python3 "${SYSTEM_DIR}/stage1_night20.py"
        rc=$?
        if [ ${rc} -eq 0 ]; then
            echo "[run_auto] 阶段1执行完成, 生成全盘扫描日报..."
            python3 "${SYSTEM_DIR}/stage1_report.py"
        fi
        ;;
    stage2)
        echo "[run_auto] 执行阶段2 早盘竞价筛TOP5..."
        python3 "${SYSTEM_DIR}/stage2_auction.py"
        rc=$?
        ;;
    *)
        echo "[run_auto] 未知阶段: ${STAGE} (仅支持 stage1 / stage2)"
        rc=2
        ;;
esac

echo "[run_auto] 阶段${STAGE} 返回码 rc=${rc}"
exit ${rc}