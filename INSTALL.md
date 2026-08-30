# 從零把這套 lab 架起來

**這份是「照著打」的順序**；每一步「為什麼這樣做」在對應的 Day 文章裡。

⏱️ 第一次大約 **半天**，其中一半在等（CRC 拉映像、build image、mirror）。

> ⚠️ **這是 lab 的做法，不是正式環境的做法。**
> 匿名免登入的 Grafana、`insecureRegistries`、內建 MariaDB、沒有防護的 Route——
> 都是為了跑得起來，正式環境每一項都要換。檔案裡有標。

---

## 0. 你需要先有的東西

| | 要求 |
|---|---|
| 機器 | **12 核以上 / 32 GB 以上 / 120 GB 可用空間**（我用 13 核 / 40 GB） |
| OS | Linux + libvirt（我是 Arch）；macOS/Windows 的 CRC 也可以，指令要自己對 |
| 帳號 | Red Hat 帳號（免費），拿 CRC 的 pull secret |
| 工具 | `oc`、`podman`、`skopeo`、`jq`、`python3` |

⚠️ **CPU 是最容易低估的**（Day 4、Day 22）。給 10 核會裝得起來但排不進東西。

---

## 1. 起一套 OpenShift（CRC）

📖 [Day 4：在一台筆電上裝一套 OpenShift AI](https://ryangtr.github.io/2026/09/install-openshift-ai-on-a-laptop/)

```bash
crc setup
crc config set cpus 13
crc config set memory 40960      # MB
crc config set disk-size 120     # GB
crc start -p ./pull-secret.txt
eval $(crc oc-env)
```

**驗證**：`crc status` 顯示 `OpenShift: Running`，`oc whoami` 有輸出。

---

## 2. 起叢集外的兩個服務（跑在宿主的 podman）

⚠️ **`crc start` 不會帶起這兩個**，重開機後要自己起，不然模型服務會抓不到權重。

```bash
# MinIO（S3）
podman run -d --name minio -p 9000:9000 -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin -e MINIO_ROOT_PASSWORD=CHANGE_ME \
  -v ~/minio-data:/data:Z quay.io/minio/minio server /data --console-address ':9001'

# 建 bucket（用 mc 或 web console :9001）
#   models     ← 模型權重
#   pipelines  ← pipeline 產物
```

私有 registry（Harbor 或任何一個都行）也在這一步起。
📖 [Day 19：離線環境](https://ryangtr.github.io/2026/09/airgapped-images/)

**驗證**：從宿主 `curl -I http://<你的IP>:9000/minio/health/live` 回 200。

⚠️ **不要用 `localhost`**。CRC 是 VM，`localhost` 指的是 VM 自己。
要用宿主在 VM 看得到的那個 IP —— `host.crc.testing` **解析得到但連不通**（實測）。

---

## 3. 換掉檔案裡的主機名

```bash
./set-lab-host.sh <registry:port> <s3-host:port>
# 例：./set-lab-host.sh 192.168.1.50:8088 192.168.1.50:9000
```

**驗證**：

```bash
grep -rn 'registry.lab\|minio.lab' . --exclude=set-lab-host.sh --exclude=README.md || echo ok
```

---

## 4. 裝 operator

📖 [Day 4](https://ryangtr.github.io/2026/09/install-openshift-ai-on-a-laptop/)

從 OperatorHub 裝兩個（web console 或 `Subscription` YAML）：

- **cert-manager**（`cert-manager-operator`）—— **3.x 的必要相依，2.x 不需要**
- **Open Data Hub**（`opendatahub-operator`）

**驗證**：

```bash
oc get csv -A --no-headers | awk '{print $2}' | sort -u | grep -Ei 'opendatahub|cert-manager'
```

兩個都要 `Succeeded` 才往下走。

---

## 5. 開啟平台元件（DSC）

📖 [Day 5：DataScienceCluster](https://ryangtr.github.io/2026/09/datasciencecluster-turning-components-on/)

```bash
oc apply -f manifests/00-dsc.yaml
```

**驗證**（⚠️ **不要看頂層 `Ready`**，`Removed` 的元件也被算進那個 AND）：

```bash
oc get dsc default-dsc -o json | jq -r '.status.conditions[]
  | select(.type|test("Dashboard|Kserve|Workbench|AIPipelines|ModelRegistry"))
  | "\(.type)=\(.status)"'
```

五個都 `True` 才算好。元件 pod 起來要幾分鐘：

```bash
oc get pods -n opendatahub
```

---

## 6. 建 project、憑證、ServiceAccount

📖 [Day 7：Connection](https://ryangtr.github.io/2026/09/connect-your-storage/) ·
[Day 21：憑證](https://ryangtr.github.io/2026/09/secrets-on-the-platform/)

```bash
oc apply -f manifests/10-namespace.yaml

# 🔴 先把 11-secrets.yaml 裡的 CHANGE_ME 全部換掉再 apply
oc apply -f manifests/11-secrets.yaml

# registry 憑證建議用指令建（robot 帳號名含 $ 和 +，一定要單引號）
oc create secret docker-registry harbor-pull \
  --docker-server=<registry:port> \
  --docker-username='<user>' --docker-password='<token>' \
  -n llm-serve-demo --dry-run=client -o yaml | oc apply -f -

oc apply -f manifests/12-serviceaccount.yaml
```

**驗證** —— ⚠️ **dashboard 顯示 connection 存在只代表格式對，它不會拿憑證去連一次**。
真的連一次：

```bash
oc run s3check --rm -it --restart=Never --image=quay.io/minio/mc \
  --env-from=secret/minio-connection -n llm-serve-demo -- \
  sh -c 'mc alias set t $AWS_S3_ENDPOINT $AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY && mc ls t/models'
```

**列得出東西才算設好。**

---

## 7. Build 並推兩顆 image

📖 [Day 10](https://ryangtr.github.io/2026/09/deploy-a-model-with-inferenceservice/)

context 是 [llm-from-scratch](https://github.com/ryanGTR/llm-from-scratch) 那個 repo：

```bash
git clone https://github.com/ryanGTR/llm-from-scratch ~/llm-from-scratch

podman build -f images/Containerfile.cpu   -t <registry>/tools/llm-serve:cpu ~/llm-from-scratch
podman push <registry>/tools/llm-serve:cpu

cp images/containerignore.train ~/llm-from-scratch/.containerignore
podman build -f images/Containerfile.train -t <registry>/tools/llm-train:cpu ~/llm-from-scratch
podman push <registry>/tools/llm-train:cpu
```

**驗證**：`skopeo inspect docker://<registry>/tools/llm-serve:cpu | jq -r .Digest` 拿得到 digest。

---

## 8. 把模型放上 S3

服務要讀的是 `s3://models/llm/`，裡面要有 `ckpt.pt` 和 `tokenizer.json`。

用 llm-from-scratch 訓一個（`make smoke` 就有），或放你自己的——
**任何能被 `serve/app.py` 讀起來的 checkpoint 都行**。

```bash
mc cp artifacts/ckpt.pt        t/models/llm/
mc cp artifacts/tokenizer.json t/models/llm/
```

---

## 9. 部署模型服務

📖 [Day 10：InferenceService](https://ryangtr.github.io/2026/09/deploy-a-model-with-inferenceservice/)

```bash
oc apply -f manifests/20-isvc-llm.yaml
oc get isvc -n llm-serve-demo -w
```

**驗證**（照順序，卡住就停在那一條查）：

```bash
# ① init container 有沒有把權重抓下來 —— 第一次失敗八成在這
oc logs -n llm-serve-demo <pod> -c storage-initializer

# ② pod 2/2 Running（第二個是 kube-rbac-proxy）
oc get pods -n llm-serve-demo -l serving.kserve.io/inferenceservice=llm-scratch

# ③ 權重真的在裡面（時間戳與大小要合理）
oc exec -n llm-serve-demo <pod> -c kserve-container -- ls -la /mnt/models

# ④ 叢集內打得到
oc run t --rm -it --restart=Never --image=curlimages/curl -n llm-serve-demo -- \
  curl -s http://llm-scratch-predictor.llm-serve-demo.svc.cluster.local/health
```

⚠️ **從你的筆電 curl 那個 `.svc.cluster.local` 不會通**，那是叢集內位址。
要對外得自己建 Route（Day 10 有講，**而且那等於把模型公開了**）。

---

## 10. Pipeline

📖 [Day 9：第一條 Data Science Pipeline](https://ryangtr.github.io/2026/09/your-first-data-science-pipeline/)

```bash
oc apply -f manifests/30-dspa.yaml
oc get pods -n llm-serve-demo | grep ds-pipeline    # 六個左右都要 Running
```

編譯並上傳 pipeline：

```bash
pip install 'kfp>=2'
# 🔴 先把 pipeline_llm.py 的 IMAGE_DIGEST 換成你第 7 步 push 上去的那顆
python3 pipeline/pipeline_llm.py       # → pipeline_llm.yaml
```

到 dashboard → Pipelines → Import pipeline 上傳，然後 Create run。

**驗證** —— ⚠️ **UI 綠燈只代表 exit code 0**（Day 26）：

```bash
oc get workflows -n llm-serve-demo          # Argo 的執行實體
mc ls t/pipelines/runs/<run_id>/            # ⭐ 產物在不在、大小合不合理
```

---

## 11. 監控

📖 [Day 16：監控](https://ryangtr.github.io/2026/09/monitoring-your-model-service/)

```bash
oc apply -k gitops/monitoring -n llm-serve-demo     # ⚠️ -n 不能省
oc get route grafana -n llm-serve-demo
```

**驗證** —— ⚠️ **一定要用眼睛看面板**：

```bash
# Prometheus 抓到 target 了嗎
oc exec -n llm-serve-demo deploy/prometheus -- \
  wget -qO- localhost:9090/api/v1/targets | jq -r '.data.activeTargets[].health'
```

Prometheus 有資料、Grafana pod Running、面板卻全空，是會發生的
（datasource `uid` 沒指定就會這樣，我踩過一整天）。**打開頁面看過才算數。**

---

## 12. 收驗收證據

📖 [Day 25](https://ryangtr.github.io/2026/09/writing-acceptance-criteria/) ·
[Day 26](https://ryangtr.github.io/2026/09/green-is-not-done/)

```bash
./scripts/collect-evidence.sh <registry:port>
```

⚠️ **這支腳本本身也要被檢查**：故意弄壞一個東西（例如把 ISvc scale 到 0），
確認它抓得到。**沒驗過會響的檢查，不知道它會不會響。**

---

## 重開機之後

CRC 的 VM 會停，宿主的 podman 服務也會停。**順序有差**：

```bash
podman start minio                       # ① 先起 S3
podman pod start pod_harbor              # ② 再起 registry（有的話）
crc start                                # ③ 最後才起叢集
eval $(crc oc-env)
```

⚠️ **反過來的話**，KServe 的 storage-initializer 會在 S3 還沒起來時就跑，
ISvc 起不來。已經卡住的話刪 pod 讓它重來：

```bash
oc delete pod -n llm-serve-demo -l serving.kserve.io/inferenceservice=llm-scratch
```

---

## 卡住的時候

| 症狀 | 先看哪裡 | 說明 |
|---|---|---|
| ISvc 一直 not ready | `oc logs <pod> -c storage-initializer` | 八成是 S3 憑證或 bucket 路徑 |
| `unauthorized` 拉不到 image | SA 有沒有連上 pull secret | 建了 Secret ≠ 有人在用它（Day 19） |
| pod 一直 Pending | `oc describe pod` 最後幾行 | `Insufficient cpu`（Day 22） |
| dashboard 看不到 project | namespace 的 label | `opendatahub.io/dashboard=true`（Day 6） |
| pipeline run 綠但沒產物 | S3 上的檔案 | 綠燈在 Runs 頁，證據在 Artifacts 頁（Day 26） |
| Grafana 面板全空 | datasource 的 `uid` | Day 16 |
| 元件沒出現在 dashboard | DSC 開了沒 | Day 5 |

---

📖 **完整說明**：<https://ryangtr.github.io>
