# OpenShift AI 入門 30 天 — lab 檔案

[《OpenShift AI 入門 30 天》](https://ryangtr.github.io/) 這個系列用到的 YAML、
Containerfile 與腳本。**每一份都在文章裡出現過，這裡是可以直接拿去跑的版本。**

環境：**OpenShift Local (CRC) 2.63.0** · OpenShift 4.22.7 · Kubernetes v1.35.6 ·
**Open Data Hub 3.5.0** · 13 vCPU / 40 GiB / 120 GB · 單節點、無 GPU。

> ⚠️ **ODH ≠ RHOAI。** 兩者元件與 CRD 絕大部分相同，
> 但 namespace 名稱不同（`opendatahub` vs `redhat-ods-applications`）。
> 用在 RHOAI 上要換 namespace。

---

## 先做這件事

檔案裡的主機名是佔位符，換成你自己的：

```bash
./set-lab-host.sh <registry:port> <s3-host:port>
# 例：./set-lab-host.sh registry.internal:8088 minio.internal:9000
```

然後確認沒有漏的：

```bash
grep -rn 'registry.lab\|minio.lab\|CHANGE_ME' . \
  --exclude=set-lab-host.sh --exclude=README.md || echo ok
```

⚠️ 腳本換不掉的三件事：S3 帳密（`manifests/connection-s3.yaml` 的 `CHANGE_ME`）、
`pipeline/pipeline_llm.py` 裡的 `IMAGE_DIGEST`（要換成你自己 build 的那顆）、
以及 IDMS 的 mirror 目的地是否真的存在。

---

## Day 對照表

| Day | 主題 | 檔案 |
|---|---|---|
| 5 | DataScienceCluster | `manifests/dsc.yaml` |
| 7 | Connection（S3） | `manifests/connection-s3.yaml` |
| 9 | Data Science Pipeline | `pipeline/pipeline_llm.py`、`pipeline/pipeline_llm.yaml`、`images/Containerfile.train` |
| 10 | InferenceService | `manifests/isvc-llm.yaml`、`images/Containerfile.cpu` |
| 16 | 監控 | `gitops/monitoring/` |
| 19 | 離線環境 / mirror | `manifests/idms-odh-workbench.yaml` |
| 24 | GitOps | `gitops/monitoring/kustomization.yaml` |
| 25、26 | 驗收證據 | `scripts/collect-evidence.sh` |
| 27 | 放行閘門 | `pipeline/pipeline_llm.py` 的 `GATE` 段 |

---

## 目錄

```
manifests/                  單獨 apply 的資源
  dsc.yaml                  平台元件總開關
  connection-s3.yaml        S3 連線（貼了 label 的 Secret）
  dspa.yaml                 Data Science Pipelines 實例
  isvc-llm.yaml             模型服務（KServe，自帶容器）
  idms-odh-workbench.yaml   image mirror（離線用）

gitops/monitoring/          kustomize，可直接給 Argo CD
  prometheus.yaml grafana.yaml kustomization.yaml
  files/                    ConfigMap 的來源檔（含 dashboard JSON）

pipeline/
  pipeline_llm.py           KFP v2 pipeline 原始碼（編譯成下面那個）
  pipeline_llm.yaml         編譯結果，可直接上傳到 dashboard

images/
  Containerfile.cpu         推論 image（CPU）
  Containerfile.train       訓練／評估 image
  containerignore.train

scripts/
  collect-evidence.sh       驗收證據收集器
```

---

## 模型從哪來

這個 lab 服務的模型來自 [**llm-from-scratch**](https://github.com/ryanGTR/llm-from-scratch)
——一個從零手刻的小 GPT。build image 時的 context 是那個 repo：

```bash
podman build -f openshift-ai-30days/images/Containerfile.cpu \
  -t llm-serve:cpu ~/path/to/llm-from-scratch
```

**用你自己的模型也可以。** 這些檔案的重點是平台怎麼用，不是那個模型。
換成任何能讀 `/mnt/models` 並開一個 HTTP port 的容器都行。

---

## 三個先講清楚的限制

1. **單節點、無 GPU。** 排程、HA、跨節點網路、真實負載的表現，這個 lab 驗不出來。
   **所以這裡沒有 GPU 相關的 manifest** —— 我沒有卡可以實測，不放沒驗過的東西。
   Hardware Profile 的寫法在 Day 13 的文章裡，但請當成範例而不是驗證過的配置。
2. **內建資料庫是 lab 設定。** `dspa.yaml` 的 `mariaDB.deploy: true` 沒有備份、
   沒有 HA，pod 掛了 pipeline 紀錄與血緣就沒了。正式環境要接外部資料庫。
3. **Grafana 匿名免登入、`insecureRegistries`、對外 Route 無防護** ——
   都是 lab 的方便做法，**正式環境不要照抄**。檔案裡有標。

---

## 這裡沒有的東西

- **模型權重與訓練資料**（在 S3，不在 git）
- **任何憑證**（`CHANGE_ME` 佔位）
- 一鍵安裝腳本 —— **裝機的步驟在 Day 4，那是要一步一步看輸出的**

---

## 授權與回饋

MIT。

發現哪裡寫錯、或你的環境行為不一樣，
歡迎開 issue —— 我會查、會改、會在文章裡標明是誰指出的。
