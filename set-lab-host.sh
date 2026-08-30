#!/usr/bin/env bash
# 把 repo 裡的預設主機名換成你自己的 —— 對應 Day 19。
#
# 為什麼需要這支：這些 manifest 裡有兩個外部端點（私有 registry 和 S3），
# 而它們的位址每個人都不一樣。硬編在檔案裡的位址遲早會咬人（我被咬過三次）。
#
# 用法：
#   ./set-lab-host.sh <registry:port> <s3-host:port>
#   ./set-lab-host.sh registry.internal:8088 minio.internal:9000
#
# ⚠️ 這支會直接改檔案。改壞了用 git checkout . 還原。
set -euo pipefail

REG="${1:?用法: $0 <registry:port> <s3-host:port>}"
S3="${2:?用法: $0 <registry:port> <s3-host:port>}"
S3_HOST="${S3%:*}"

cd "$(dirname "$0")"

# 只改文字檔，跳過 .git
mapfile -t FILES < <(git ls-files 2>/dev/null || find . -type f -not -path './.git/*')

for f in "${FILES[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in *.md|set-lab-host.sh) continue;; esac
  sed -i \
    -e "s|registry\.lab:8088|${REG}|g" \
    -e "s|minio\.lab:9000|${S3}|g" \
    -e "s|minio\.lab|${S3_HOST}|g" \
    "$f"
done

echo "已替換：registry.lab:8088 → ${REG}"
echo "        minio.lab:9000    → ${S3}"
echo
echo "⚠️ 還有三件事只有你自己知道，不在這支腳本的範圍："
echo "   1. manifests/connection-s3.yaml 的 CHANGE_ME（S3 帳密）"
echo "   2. pipeline/pipeline_llm.py 的 IMAGE_DIGEST（要換成你自己 build 出來的那顆）"
echo "   3. manifests/idms-odh-workbench.yaml 的 mirror 目的地是否真的存在"
echo
echo "改完先驗一次再套用（排除本腳本自己）："
echo "   grep -rn 'registry.lab\\|minio.lab\\|CHANGE_ME' . --exclude=set-lab-host.sh --exclude=README.md || echo ok"
