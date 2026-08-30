#!/usr/bin/env bash
# ODH／RHOAI 驗收證據收集器 — 對應 Day 25、Day 26。
#
# 用法：./collect-evidence.sh <允許的registry清單,逗號分隔> [輸出檔]
#   例：./collect-evidence.sh registry.lab:8088
#   例：./collect-evidence.sh harbor.internal,registry.internal:5000
#
# ⚠️ 多 registry 是常態（私有 registry ＋ OCP 內建 registry ＋ 舊 registry）。
#    只填一個會把其他內部 registry 誤判成「外網」——實測踩過。
#
# 設計原則：每條都是「指令＋輸出」，不收口頭；輸出直接存檔當證據。
# ⚠️ 這支腳本本身也需要被檢查——故意弄壞一個東西，確認它會抓到（Day 26）。
set -u
REG="${1:?請給允許的 registry 清單（逗號分隔），例：harbor.internal,registry.internal:5000}"
# 轉成 grep 用的 alternation：a,b -> ^(a|b)
REG_RE="^($(printf '%s' "$REG" | sed 's/\./\\./g; s/,/|/g'))"
OUT="${2:-acceptance-evidence-$(date +%Y%m%d-%H%M).txt}"

sec() { printf '\n########## %s ##########\n' "$1" | tee -a "$OUT"; }
cap() { printf '$ %s\n' "$1" | tee -a "$OUT"; eval "$1" 2>&1 | tee -a "$OUT"; }

: > "$OUT"
printf '收集時間: %s\n叢集: %s\n私有 registry: %s\n' "$(date -Is)" "$(oc whoami --show-server 2>/dev/null)" "$REG" | tee -a "$OUT"

sec "[環境] OCP 版本"
cap "oc get clusterversion"

sec "[環境] 節點規格（GPU 節點另看 Capacity）"
cap "oc get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.capacity.cpu,MEM:.status.capacity.memory,GPU:'.status.capacity.nvidia\.com/gpu'"

sec "[環境] storage class（要有 default）"
cap "oc get storageclass"

sec "[環境] 身分（⚠️ 不要用 kube:admin 驗收 — Day 20）"
cap "oc whoami"

sec "[環境] 叢集是否已有 ODH（⚠️ 必須去重，否則 CSV 會複製到每個 namespace 吐假警報）"
cap "oc get csv -A -o jsonpath='{range .items[*]}{.metadata.name}{\"\\n\"}{end}' | sort -u | grep -i opendatahub || echo '(空——正確)'"

sec "[環境] 叢集信任私有 registry（Day 19）"
cap "oc get image.config.openshift.io/cluster -o jsonpath='{.spec.registrySources}{\"\\n\"}'"

sec "[環境] IDMS 存在（⚠️ 與上一條是兩件事：這條是「有沒有改導向」）"
cap "oc get imagedigestmirrorset -A"
cap "oc get imagedigestmirrorset -A -o jsonpath='{range .items[*].spec.imageDigestMirrors[*]}{.source} -> {.mirrors[0]} [{.mirrorSourcePolicy}]{\"\\n\"}{end}'"

sec "[安裝] CatalogSource（離線環境應為 oc mirror 產生）"
cap "oc get catalogsource -A"
cap "oc get pods -n openshift-marketplace --no-headers | grep -v Completed"

sec "[安裝] operator 版本"
cap "oc get csv -A -o jsonpath='{range .items[*]}{.metadata.name}{\"\\n\"}{end}' | sort -u | grep -Ei 'rhods|opendatahub|cert-manager|nfd|gpu-operator|kueue'"

sec "[安裝] DataScienceCluster（⚠️ 看你開的元件，不是看頂層 Ready — Day 5）"
cap "oc get dsc -o custom-columns=NAME:.metadata.name,PHASE:.status.phase --no-headers"
cap "oc get dsc -o jsonpath='{range .items[0].status.conditions[*]}{.type}={.status} {.reason}{\"\\n\"}{end}' | grep -v '=False Removed'"

sec "[安裝] namespace"
cap "oc get ns --no-headers | grep -Ei 'rhods|rhoai|redhat-ods|opendatahub' | awk '{print \$1, \$2}'"

sec "[安裝] route（⚠️ 3.x 走 data-science-gateway — Day 6）"
cap "oc get route -A -o custom-columns=NS:.metadata.namespace,NAME:.metadata.name,HOST:.spec.host --no-headers | grep -Ei 'dashboard|gateway|rhods|ods'"

sec "[護欄] ⭐ 真的用到 GPU（送 request 前跑一次，送完再跑一次比對 — Day 13）"
echo "# 在 GPU 節點上執行（把 <gpu-node> 換掉）：" | tee -a "$OUT"
echo "# oc debug node/<gpu-node> -- chroot /host nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv" | tee -a "$OUT"
cap "oc get nodes -o jsonpath='{range .items[*]}{.metadata.name} gpu={.status.capacity.nvidia\\.com/gpu}{\"\\n\"}{end}'"

sec "[端到端] InferenceService（Day 10）"
cap "oc get isvc -A"

sec "[護欄] ⭐ 哪些 image 必須進鏡像清單（清單完整性）"
echo "# 每一顆都要在鏡像清單內，否則離線裝不起來。" | tee -a "$OUT"
cap "oc get pods -A --field-selector=status.phase=Running -o jsonpath='{range .items[*].status.containerStatuses[*]}{.imageID}{\"\\n\"}{end}' | grep -v '^\$' | sed 's|@.*||' | sort -u | grep -vE \"${REG_RE}\" || echo '(空)'"
cap "oc get pods -A --field-selector=status.phase=Running -o jsonpath='{range .items[*].status.containerStatuses[*]}{.imageID}{\"\\n\"}{end}' | grep -v '^\$' | sed 's|@.*||' | sort -u | grep -cE \"${REG_RE}\" | xargs -I{} echo '在允許清單內: {}'"
cap "oc get pods -A --field-selector=status.phase=Running -o jsonpath='{range .items[*].status.containerStatuses[*]}{.imageID}{\"\\n\"}{end}' | grep -v '^\$' | sed 's|@.*||' | sort -u | grep -vcE \"${REG_RE}\" | xargs -I{} echo '不在允許清單: {}'"

sec "[護欄] ⭐ 實際上會不會走外網（⚠️ 不可用 imageID 判斷）"
cat <<'NOTE' | tee -a "$OUT"
# ⚠️ imageID 顯示 quay.io 不代表走了外網！
#    imageID 是 image 的「身份」（來源名＋digest）；IDMS 改的是「去哪裡拿」，不改「這是誰」。
#    有 IDMS 時 imageID 照樣顯示原始來源——本 lab 實測確認過。
#    要證明離線，看節點的 registries.conf：每個外部 source 要有 mirror，且 blocked = true。
NOTE
NODE=$(oc get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
cap "oc debug node/${NODE} -- chroot /host cat /etc/containers/registries.conf 2>/dev/null | grep -E 'location|blocked|pull-from-mirror' | head -40"
echo "# 判讀：有 blocked = true 的 source 才是真的封了原站；只有 [[registry.mirror]] 而沒 blocked＝拉不到會回頭找原站（假離線）" | tee -a "$OUT"

sec "[證據鏈] 模型身份（serving pod 的 image digest — Day 15）"
cap "oc get pods -A -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.imageID}{\"\\n\"}{end}{end}' | grep -i 'kserve\\|serve' | sort -u"

printf '\n=== 收集完成：%s ===\n' "$OUT" | tee -a "$OUT"
