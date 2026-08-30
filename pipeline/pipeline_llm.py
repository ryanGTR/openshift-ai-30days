"""用 Data Science Pipelines 管理模型的生成（訓練→評估→放行）— 對應 Day 9、Day 27。

目的不是訓練出好模型，是證明**流程可被平台管理**：
每一棒在容器內跑、產物落 S3、放行與否由 gate 判定而非人工。

設計取捨（三個值得在驗收時問到的點）：
  1. 用 @dsl.container_component 而非 @dsl.component
     —— lightweight component 執行時會 `pip install kfp`，**離線環境裝不了**。
        container_component 直接執行 image 內的程式，離線可行。
  2. base image 來自私有 registry，不是 quay.io。
  3. 大檔（語料 / bin / 34MB 權重）走 S3，步驟間只用 run_id 串接、以 .after() 表達依賴
     —— 不把權重塞進 KFP 的 artifact metadata。

編譯：python pipeline_llm.py  → pipeline_llm.yaml
"""
from kfp import dsl, compiler, kubernetes

# 釘 digest 而不是 tag：tag 可以被覆蓋，覆蓋之後這次 run 就再也答不出
# 「我跑的是哪一版程式碼」。而容器裡沒有 .git（image 只 COPY 程式檔），
# 所以 registry 的 lineage.code_commit 一定是 None——程式碼的身份只能靠 image digest。
# 取得方式：oc get pod ... -o jsonpath='{..imageID}'（叢集實際拉到的那個，不是本機 podman 的）
IMAGE_REPO = "registry.lab:8088/tools/llm-train"
IMAGE_DIGEST = "sha256:2ce7d45fadd3803fdf7c607ae1187b9516cbdee9f6831f8bf2030ff52441cc69"
IMAGE = f"{IMAGE_REPO}@{IMAGE_DIGEST}"
WORK, MODELS = "pipelines", "models"
S3_ENDPOINT = "http://minio.lab:9000"
REG_KEY = "registry.json"          # 台帳在 s3://models/registry.json（單一真相）

S3HELP = r'''
import os, boto3, pathlib
_s3 = boto3.client("s3", endpoint_url=os.environ["S3_ENDPOINT"],
                   aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                   aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
def get(b, k, d):
    pathlib.Path(d).parent.mkdir(parents=True, exist_ok=True)
    _s3.download_file(b, k, d); print("  <- s3://%s/%s" % (b, k))
def put(s, b, k):
    _s3.upload_file(s, b, k); print("  -> s3://%s/%s" % (b, k))
def get_prefix(b, p, dd):
    for o in _s3.list_objects_v2(Bucket=b, Prefix=p).get("Contents", []):
        if not o["Key"].endswith("/"):
            get(b, o["Key"], os.path.join(dd, os.path.basename(o["Key"])))
'''

PREPARE = r'''set -e
RUN_ID="$1"; SAMPLE_MB="$2"
cd /app && mkdir -p work/artifacts work/data
python - <<PY
%s
get("%s", "llm/clean_corpus.txt", "/app/work/data/corpus_full.txt")
PY
head -c $((SAMPLE_MB * 1048576)) work/data/corpus_full.txt > work/data/input.txt
echo "取樣語料: $(du -h work/data/input.txt | cut -f1)"
python pipeline/01_prepare_data.py --input work/data/input.txt --artifacts work/artifacts --tokenizer bpe --merges 500
# 資料品質報表：gate 的第一道檢查（lineage.data_quality_gate）讀這個檔。
# 沒有它，registry.gate_reasons() 會判「資料品質 gate 未通過」而擋下。
python scripts/quality_report.py --input work/data/input.txt --artifacts work/artifacts --label pipeline
python - <<PY
import os
%s
for f in ["train.bin","val.bin","test.bin","meta.json","tokenizer.json","data_report.json","data_quality_report.json"]:
    p = "/app/work/artifacts/" + f
    if os.path.exists(p): put(p, "%s", "runs/$RUN_ID/" + f)
PY
echo "prepare 完成"
''' % (S3HELP, MODELS, S3HELP, WORK)

TRAIN = r'''set -e
RUN_ID="$1"; MAX_ITERS="$2"
cd /app && mkdir -p work/artifacts
python - <<PY
%s
get_prefix("%s", "runs/$RUN_ID/", "/app/work/artifacts")
PY
python pipeline/02_train.py --artifacts work/artifacts --max_iters $MAX_ITERS --run_name pipeline
python - <<PY
import os
%s
put("/app/work/artifacts/ckpt.pt", "%s", "runs/$RUN_ID/ckpt.pt")
for f in ["loss_history.csv"]:
    p = "/app/work/artifacts/" + f
    if os.path.exists(p): put(p, "%s", "runs/$RUN_ID/" + f)
PY
echo "train 完成"
''' % (S3HELP, WORK, S3HELP, WORK, WORK)

EVAL = r'''set -e
RUN_ID="$1"
cd /app && mkdir -p work/artifacts
python - <<PY
%s
get_prefix("%s", "runs/$RUN_ID/", "/app/work/artifacts")
PY
# val 與 test 都要算：
#   val  → 絕對門檻（max_val_loss）用
#   test → registry.gate_reasons() 的回歸檢查用（它比的是 test_loss；缺了會判「缺 test 評估」）
# 03_eval.py 每次都覆寫 eval_report.json，所以跑完各自另存，最後合併。
python pipeline/03_eval.py --artifacts work/artifacts --eval_iters 50 --split val
cp work/artifacts/eval_report.json work/artifacts/eval_val.json
python pipeline/03_eval.py --artifacts work/artifacts --eval_iters 50 --split test
cp work/artifacts/eval_report.json work/artifacts/eval_test.json
python - <<'PY'
import json
v = json.load(open("/app/work/artifacts/eval_val.json"))
t = json.load(open("/app/work/artifacts/eval_test.json"))
merged = {
    "val_loss": v.get("val_loss"),
    "test_loss": t.get("test_loss"),
    "perplexity": t.get("perplexity"),          # 以 test 為準
    "val_perplexity": v.get("perplexity"),
    "train_iter": v.get("train_iter"),
    "params_M": v.get("params_M"),
}
json.dump(merged, open("/app/work/artifacts/eval_report.json", "w"), indent=2, ensure_ascii=False)
print("eval:", json.dumps(merged, ensure_ascii=False))
PY
python - <<PY
%s
put("/app/work/artifacts/eval_report.json", "%s", "runs/$RUN_ID/eval_report.json")
PY
echo "eval 完成"
''' % (S3HELP, WORK, S3HELP, WORK)

# 放行閘門：這是「pipeline 管理生成」的核心——放行由條件決定，不是人說了算。
# promotion gate v2（2026-08-28）
# 改動理由：v1 只比一條絕對門檻（val_loss <= max_val_loss），而 repo 裡
# src/registry.py:gate_reasons() 早就實作了更完整的規則（資料品質 + 有 test 評估 +
# 不得比現行基準回歸 tol=0.05）卻沒有接上執行路徑。
# 治理的缺口通常不是「沒做」，是「做了但沒接上放行路徑」——所以這裡改成複用它。
#
# 同時把產出物寫進台帳（s3://models/registry.json 為單一真相），
# 並記下 code_image：容器裡沒有 .git，lineage.code_commit 必為 None，
# 程式碼的身份只能靠 image digest。
GATE = r'''set -e
RUN_ID="$1"; MAX_LOSS="$2"
cd /app && mkdir -p work/artifacts
python - <<PY
import json, os, sys
sys.path.insert(0, "/app")
%s
from pathlib import Path
from src.registry import build_entry, gate_reasons

ART = Path("/app/work/artifacts")
get_prefix("%s", "runs/$RUN_ID/", str(ART))

REG_LOCAL = "/app/work/registry.json"
try:
    get("%s", "%s", REG_LOCAL)
    reg = json.load(open(REG_LOCAL))
    print("台帳載入：", len(reg), "筆")
except Exception:
    reg = []
    print("台帳不存在 → 這是第一筆，本次為 baseline")

entry = build_entry(ART)
entry["lineage"]["code_image"] = os.environ.get("PIPELINE_IMAGE", "unknown")
entry["lineage"]["run_id"] = "$RUN_ID"

# 基準＝台帳裡最近一筆（不同 digest）。第一次跑沒有基準 → 回歸檢查自動跳過。
others = [e for e in reg if e["model_digest"] != entry["model_digest"]]
baseline = sorted(others, key=lambda e: e.get("created_at", ""))[-1] if others else None

reasons = []
thr = float("$MAX_LOSS")
vl = entry.get("metrics", {}).get("val_loss")
if vl is None or float(vl) > thr:
    reasons.append("val_loss " + str(vl) + " 超過門檻 " + str(thr))
reasons += gate_reasons(entry, baseline)      # 資料品質 + 缺 test + 回歸

m = entry.get("metrics", {})
print("=== promotion gate ===")
print("  候選 ", entry["short"], " val_loss=", m.get("val_loss"), " test_loss=", m.get("test_loss"))
print("  基準 ", baseline["short"] if baseline else "(無)",
      " test_loss=", baseline.get("metrics", {}).get("test_loss") if baseline else "-")
print("  code_image ", entry["lineage"]["code_image"])
print("  data_quality_gate ", entry["lineage"].get("data_quality_gate"))

if reasons:
    for r in reasons:
        print("  ✗ 擋下：", r)
    sys.exit(1)      # 不合格就讓這一棒紅燈，流程止步

print("  ✓ 放行")
reg.append(entry)
json.dump(reg, open(REG_LOCAL, "w"), indent=2, ensure_ascii=False)
put(REG_LOCAL, "%s", "%s")
put(str(ART / "ckpt.pt"), "%s", "llm-candidate/ckpt.pt")
put(str(ART / "tokenizer.json"), "%s", "llm-candidate/tokenizer.json")
print("  已註冊進台帳並送進候選區 s3://models/llm-candidate/")
PY
''' % (S3HELP, WORK, MODELS, REG_KEY, MODELS, REG_KEY, MODELS, MODELS)


def _spec(script, *args):
    return dsl.ContainerSpec(image=IMAGE, command=["bash", "-c", script, "bash"], args=list(args))


@dsl.container_component
def prepare_data(run_id: str, sample_mb: str):
    return _spec(PREPARE, run_id, sample_mb)


@dsl.container_component
def train_model(run_id: str, max_iters: str):
    return _spec(TRAIN, run_id, max_iters)


@dsl.container_component
def evaluate_model(run_id: str):
    return _spec(EVAL, run_id)


@dsl.container_component
def promotion_gate(run_id: str, max_val_loss: str):
    return _spec(GATE, run_id, max_val_loss)


@dsl.pipeline(
    name="llm-lifecycle",
    description="從零訓練的小 GPT：資料準備→訓練→評估→放行閘門。證明模型生成流程可被平台管理。",
)
def llm_lifecycle(run_id: str = "run1", sample_mb: str = "2",
                  max_iters: str = "300", max_val_loss: str = "6.0"):
    p = prepare_data(run_id=run_id, sample_mb=sample_mb)
    t = train_model(run_id=run_id, max_iters=max_iters).after(p)
    e = evaluate_model(run_id=run_id).after(t)
    g = promotion_gate(run_id=run_id, max_val_loss=max_val_loss).after(e)
    for task in (p, t, e, g):
        task.set_caching_options(False)
        # S3 憑證由 secret 注入，不寫進 image（image 會進 registry，密碼不該跟著走）
        kubernetes.use_secret_as_env(
            task, secret_name="minio-s3",
            secret_key_to_env={"AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
                               "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY"})
        task.set_env_variable("S3_ENDPOINT", S3_ENDPOINT)
        # 讓 gate 能把「這次用的程式碼身份」寫進 lineage（容器裡沒有 git 可問）
        task.set_env_variable("PIPELINE_IMAGE", IMAGE)
    # 訓練吃 CPU，給足；CRC 資源緊，其餘步驟保持小
    t.set_cpu_request("1").set_cpu_limit("3").set_memory_request("2Gi").set_memory_limit("6Gi")


if __name__ == "__main__":
    compiler.Compiler().compile(llm_lifecycle, "pipeline_llm.yaml")
    print("已編譯 → pipeline_llm.yaml")
