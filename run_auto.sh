#!/usr/bin/env bash
# =============================================================
# 主板两阶段选股系统 自动运行入口 run_auto.sh
# -------------------------------------------------------------
# 用法:
#   bash run_auto.sh stage1   # 阶段1 晚间全盘选TOP20
#   bash run_auto.sh stage2   # 阶段2 早盘竞价重排筛TOP5
#
# 流程:
#   1) 先从 GitHub 公开库 git 同步最新代码
#   2) 按目标阶段执行对应脚本, 非交易日自动跳过
#
# 关键约束:
#   night20_latest.json 是【阶段1的输出去产物】, 属于运行期生成的
#   实盘数据. 本脚本 git 同步时【不得覆盖】它 —— 仅在文件不存在
#   (首次部署/无阶段1输出) 时才从仓库填充样本; 其它同步改动照常拉取.
# =============================================================
set -u

SYSTEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL="https://github.com/godsavethekingx-cloud/stock-v1224-two-stage-mainboard.git"
NIGHT_POOL="${SYSTEM_DIR}/night20_latest.json"

STAGE="${1:-stage2}"
echo "[run_auto] 工作目录=${SYSTEM_DIR}  目标=${STAGE}"

# ---------- 0. 首次部署: 若目录无 git 仓库则克隆 ----------
if [ ! -d "${SYSTEM_DIR}/.git" ]; then
    echo "[run_auto] 未发现 git 仓库, 从远端克隆..."
    git clone "${REPO_URL}" "${SYSTEM_DIR}" || { echo "[run_auto] 克隆失败"; exit 1; }
fi

# ---------- 1. git 同步最新代码(保护阶段1输出 night20_latest.json) ----------
cd "${SYSTEM_DIR}" || exit 1

BACKUP=""
if [ -f "${NIGHT_POOL}" ]; then
    BACKUP=$(mktemp)
    cp "${NIGHT_POOL}" "${BACKUP}"
    echo "[run_auto] 已备份现有 night20_latest.json -> ${BACKUP}"
fi

# 丢弃本地未提交改动后拉取远端最新; 若远端在拉取中被改写由下面恢复逻辑保障
git fetch origin >/dev/null 2>&1
changed=0
if git diff --quiet origin/main -- night20_latest.json 2>/dev/null; then
    changed=1
fi

git reset --hard origin/main >/dev/null 2>&1
echo "[run_auto] 已 git 同步最新代码 (commit: $(git rev-parse --short HEAD))"

# 关键约束: night20_latest.json 是阶段1输出, 同步后不得被远端覆盖
if [ -n "$BACKUP" ] && [ -s "$BACKUP" ]; then
    cp "$BACKUP" "${NIGHT_POOL}"
    echo "[run_auto] 已恢复本地阶段1输出 night20_latest.json (未用仓库文件覆盖)"
fi
[ -n "$BACKUP" ] && rm -f "$BACKUP"
unset changed BACKUP

# ---------- 2. 执行目标阶段 ----------
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